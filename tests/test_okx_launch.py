"""One double-click on the Mac (scripts/install_mac.sh) and the server mode prepared but OFF (scripts/okx_autopilot_server.sh,
deploy/okx/cnq-okx-autopilot.service). Everything runs in a sandbox: fake git / pgrep / osascript / python3 / systemctl in PATH,
a temporary Desktop and /etc — nothing is installed, no window is opened, OKX and GitHub are never called."""
import os
import stat
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bin(d, name, body):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write("#!/bin/bash\n" + body + "\n")
    os.chmod(p, 0o755)


def _sh(args, env=None, inp=""):
    return subprocess.run(["bash"] + args, capture_output=True, text=True, input=inp, timeout=60, env={**os.environ, **(env or {})})


def _install(tmp):
    desk = os.path.join(tmp, "Desktop")
    r = _sh([os.path.join(ROOT, "scripts", "install_mac.sh")], {"CNQ_DESKTOP": desk, "CNQ_REPO": ROOT})
    assert r.returncode == 0, r.stdout + r.stderr
    return desk, r.stdout


def test_install_mac_writes_two_executable_command_files_and_is_idempotent():
    assert _sh(["-n", os.path.join(ROOT, "scripts", "install_mac.sh")]).returncode == 0
    tmp = tempfile.mkdtemp()
    desk, out = _install(tmp)
    start, stop = os.path.join(desk, "CRYPTO NOVA.command"), os.path.join(desk, "CRYPTO NOVA стоп.command")
    for f in (start, stop):
        assert os.stat(f).st_mode & stat.S_IXUSR and _sh(["-n", f]).returncode == 0, f
    a = open(start, encoding="utf-8").read()
    for s in (f"REPO='{ROOT}'", "git pull", "osascript", "bash scripts/mirror_mac.sh", "bash scripts/okx_autopilot_mac.sh loop --live",
              'running "scripts/mirror_mac.sh"', 'running "okx_autopilot_mac.sh loop"', "okx_lease --node", "unset CNQ_LIVE_TRADING"):
        assert s in a, s
    assert "CNQ_LIVE_TRADING=yes" not in a and "ДА" in a                      # the ДА gate stays in okx_autopilot_mac.sh
    assert "bash scripts/okx_autopilot_mac.sh stop" in open(stop, encoding="utf-8").read()
    before = (open(start, "rb").read(), open(stop, "rb").read())
    _, out2 = _install(tmp)
    assert out2.count("Уже на месте") == 2 and (open(start, "rb").read(), open(stop, "rb").read()) == before
    assert sorted(os.listdir(desk)) == sorted(["CRYPTO NOVA.command", "CRYPTO NOVA стоп.command"])   # no leftovers, no duplicates


def _run_start(tmp, running="", node="mac", key=True):
    desk, _ = _install(tmp)
    b, home = os.path.join(tmp, "bin"), os.path.join(tmp, "home")
    os.makedirs(b, exist_ok=True)
    os.makedirs(home, exist_ok=True)
    log = os.path.join(tmp, "osa.log")
    _bin(b, "git", "exit 0")
    _bin(b, "pgrep", 'shift; for p in $FAKE_RUNNING; do [ "$1" = "${p//+/ }" ] && exit 0; done; exit 1')
    _bin(b, "osascript", f'shift; echo "$2" >> "{log}"; cat >/dev/null')
    _bin(b, "python3", 'echo "$FAKE_NODE"')
    with open(os.path.join(home, ".cryptonova.env"), "w") as f:
        f.write("export OKX_API_KEY=test-key\n" if key else "export BYBIT_API_KEY=x\n")
    r = _sh([os.path.join(desk, "CRYPTO NOVA.command")], {"PATH": b + ":" + os.environ["PATH"], "HOME": home, "FAKE_RUNNING": running,
                                                          "FAKE_NODE": node})
    assert r.returncode == 0, r.stdout + r.stderr
    opened = open(log).read().split("\n")[:-1] if os.path.exists(log) else []
    return opened, r.stdout


def test_double_click_opens_the_mirror_and_the_autopilot_but_never_a_second_one():
    opened, out = _run_start(tempfile.mkdtemp())
    assert opened == ["bash scripts/mirror_mac.sh", "bash scripts/okx_autopilot_mac.sh loop --live"] and "напишите там ДА" in out
    opened, out = _run_start(tempfile.mkdtemp(), running="scripts/mirror_mac.sh okx_autopilot_mac.sh+loop")
    assert opened == [] and "Зеркало уже работает" in out and "Автопилот уже работает" in out
    opened, out = _run_start(tempfile.mkdtemp(), running="cnq.execution.okx_autopilot+--loop")
    assert opened == ["bash scripts/mirror_mac.sh"]
    opened, out = _run_start(tempfile.mkdtemp(), node="server")                  # moved to the server: no Mac autopilot window
    assert opened == ["bash scripts/mirror_mac.sh"] and "на сервере" in out
    opened, out = _run_start(tempfile.mkdtemp(), key=False)
    assert opened == ["bash scripts/mirror_mac.sh"] and "Ключа автопилота OKX нет" in out


def _server_sandbox(env_lines, main_lines="CNQ_GH_TOKEN=ghtok\n", state=True, armed=True):
    tmp = tempfile.mkdtemp()
    b = os.path.join(tmp, "bin")
    os.makedirs(b)
    log = os.path.join(tmp, "calls.log")
    _bin(b, "systemctl", f'echo "systemctl $*" >> "{log}"')
    _bin(b, "fakepy", f'echo "py $*" >> "{log}"; case "$*" in *--check*) echo "Автопилот OKX: ключ OKX_API_KEY работает · сервер www.okx.com · '
                      'это ключ РЕАЛЬНОГО счёта";; *--switch*) echo "Замок: автопилот переведён на сервер";; esac')
    envf, main = os.path.join(tmp, "cnq-okx.env"), os.path.join(tmp, "cnq.env")
    if env_lines is not None:
        open(envf, "w").write(env_lines)
    open(main, "w").write(main_lines)
    src = os.path.join(tmp, "root")
    os.makedirs(src)
    if state:
        open(os.path.join(src, ".cryptonova_okx_autopilot.json"), "w").write('{"armed":%s,"host":"https://www.okx.com"}' % ("true" if armed else "false"))
        open(os.path.join(src, ".cryptonova_okx_futures.json"), "w").write("{}")
    env = {"CNQ_OKX_TEST": "1", "CNQ_OKX_ENV": envf, "CNQ_MAIN_ENV": main, "CNQ_OKX_STATE": os.path.join(tmp, "state"),
           "CNQ_UNIT_DIR": os.path.join(tmp, "units"), "CNQ_OKX_PY": os.path.join(b, "fakepy"), "CNQ_SYSTEMCTL": os.path.join(b, "systemctl"),
           "CNQ_OKX_COPY_FROM": src}
    os.makedirs(env["CNQ_UNIT_DIR"])
    return tmp, env, log


GOOD = "OKX_API_KEY=k\nOKX_API_SECRET=s\nOKX_API_PASSPHRASE=p\nOKX_AUTOPILOT=on\nCNQ_LIVE_TRADING=yes\n"
SERVER = os.path.join(ROOT, "scripts", "okx_autopilot_server.sh")


def test_server_install_refuses_every_unsafe_setup_and_installs_nothing():
    assert _sh(["-n", SERVER]).returncode == 0
    cases = [(None, {}, "Нет файла"), ("export " + GOOD, {}, "без слова export"), (GOOD.replace("CNQ_LIVE_TRADING=yes\n", ""), {}, "CNQ_LIVE_TRADING=yes"),
             (GOOD + "OKX_LEASE_FILE=/tmp/x\n", {}, "Уберите из"), (GOOD + "OKX_NODE=mac\n", {}, "Уберите из"),
             (GOOD, {"main_lines": "CNQ_LIVE_TRADING=yes\nCNQ_GH_TOKEN=t\n"}, "этот файл читают движок и API"),
             (GOOD, {"main_lines": ""}, "нет строки CNQ_GH_TOKEN"), (GOOD, {"state": False}, "scp ~/.cryptonova_okx_*.json"),
             (GOOD, {"armed": False}, "не включён")]
    for lines, kw, want in cases:
        tmp, env, log = _server_sandbox(lines, **kw)
        r = _sh([SERVER, "install"], env, "ДА\n")
        assert r.returncode == 1 and want in r.stdout, (want, r.stdout)
        assert os.listdir(env["CNQ_UNIT_DIR"]) == [] and (not os.path.exists(log) or "enable" not in open(log).read())
    tmp, env, log = _server_sandbox(GOOD)
    r = _sh([SERVER, "install"], env, "да\n")                                   # anything but «ДА» → nothing
    assert r.returncode == 1 and "Отменено" in r.stdout and os.listdir(env["CNQ_UNIT_DIR"]) == []
    assert "--switch" not in open(log).read() and "enable" not in open(log).read()


def test_server_install_with_da_switches_the_lease_then_installs_and_starts():
    tmp, env, log = _server_sandbox(GOOD)
    r = _sh([SERVER, "install"], env, "ДА\n")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = open(log).read()
    assert calls.index("--check") < calls.index("--switch server") < calls.index("systemctl enable --now cnq-okx-autopilot")
    assert os.listdir(env["CNQ_UNIT_DIR"]) == ["cnq-okx-autopilot.service"]
    assert "CNQ_GH_TOKEN=ghtok" in open(env["CNQ_OKX_ENV"]).read()             # copied from /etc/cnq.env
    assert "CNQ_LIVE_TRADING" not in open(env["CNQ_MAIN_ENV"]).read()
    assert sorted(os.listdir(env["CNQ_OKX_STATE"])) == [".cryptonova_okx_autopilot.json", ".cryptonova_okx_futures.json", "data"]
    r = _sh([SERVER, "stop"], env)
    assert "systemctl disable --now cnq-okx-autopilot" in open(log).read() and "не запустится после перезагрузки" in r.stdout
    r = _sh([SERVER, "uninstall"], env)
    assert os.listdir(env["CNQ_UNIT_DIR"]) == [] and "--switch mac" in open(log).read()


def test_server_unit_has_its_own_env_file_and_nothing_starts_it_by_itself():
    unit = open(os.path.join(ROOT, "deploy", "okx", "cnq-okx-autopilot.service"), encoding="utf-8").read()
    assert "EnvironmentFile=/etc/cnq-okx.env" in unit and "EnvironmentFile=/etc/cnq.env" not in unit
    assert "Environment=OKX_NODE=server" in unit and "okx_autopilot --loop" in unit and "CNQ_LIVE_TRADING" not in unit.split("[Service]")[1].split("\n#")[0]
    ex = open(os.path.join(ROOT, "deploy", "okx", "cnq-okx.env.example"), encoding="utf-8").read()
    assert "CNQ_LIVE_TRADING=yes" in ex and "OKX_API_KEY=\n" in ex                # an empty template, no secrets
    assert "CNQ_LIVE_TRADING" not in open(os.path.join(ROOT, "deploy", "engine", "cnq.env.example"), encoding="utf-8").read()
    # the auto-update (engine git pull), the engine's steps and the workflows never touch the autopilot service
    for p in ["cnq/engine.py", "deploy/engine/install.sh", "deploy/engine/start.sh", "deploy/api/install_api.sh"] + \
             [os.path.join(".github/workflows", f) for f in os.listdir(os.path.join(ROOT, ".github", "workflows"))]:
        t = open(os.path.join(ROOT, p), encoding="utf-8").read()
        assert "cnq-okx" not in t and "okx_autopilot" not in t, p
