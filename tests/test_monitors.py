"""28.10.2026 (owner): «Иван тест» and «Мой портфель» on the server — cnq/execution/monitors.py, the systemd timer and the install
command. Read-only (GET only), one writer (the lease's node), a key that can trade is refused."""
import os
import subprocess

from cnq.execution import monitors as mon
from cnq.execution import okx_lease

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IVAN = {"IVAN_BYBIT_KEY": "k", "IVAN_BYBIT_SECRET": "s", "IVAN_PASS": "a-long-passphrase"}
MY = {"MYSPOT_OKX_KEY": "k", "MYSPOT_OKX_SECRET": "s", "MYSPOT_OKX_PASSPHRASE": "p", "MYSPOT_PASS": "another-long-phrase"}


def test_only_the_leases_machine_publishes(tmp_path, monkeypatch):
    calls = []
    for node, want in (("server", True), ("mac", False)):
        monkeypatch.setattr(okx_lease, "status", lambda env=None, now=None, st=None, n=node: {"node": n})
        env = {"OKX_NODE": "server", "CNQ_GH_TOKEN": "t", **IVAN, **MY}
        out = mon.once(env, ivan_run=lambda: calls.append("ivan") or {"stats": {"n": 3}, "positions": []},
                       myspot_run=lambda: calls.append("my") or {"assets": [1], "sub": {"ok": True}})
        assert mon.writer(env)[0] is want
        if want:
            assert out[0].startswith("Иван тест: опубликовано (закрытых сделок 3") and "субаккаунт CRYPTO NOVA добавлен" in out[1]
        else:
            assert out == ["Мониторы: мониторы публикует Мак (замок), здесь (server) — нет"]
    assert calls == ["ivan", "my"]                                               # nothing ran on the wrong machine
    monkeypatch.setattr(okx_lease, "status", lambda env=None, now=None, st=None: {"error": "HTTP 500"})
    assert mon.writer({"OKX_NODE": "server"})[0] is False                        # unreadable lease → nobody writes
    monkeypatch.setattr(okx_lease, "status", lambda env=None, now=None, st=None: None)
    assert mon.writer({"OKX_NODE": "server"})[0] is False


def test_a_failing_monitor_is_one_line_and_a_missing_one_is_skipped(monkeypatch):
    monkeypatch.setattr(okx_lease, "status", lambda env=None, now=None, st=None: {"node": "server"})
    env = {"OKX_NODE": "server", "CNQ_GH_TOKEN": "t", **IVAN}

    def boom():
        raise RuntimeError("Bybit down")
    out = mon.once(env, ivan_run=boom)
    assert out[0].startswith("Иван тест: ошибка") and "Мой портфель OKX: нет строк" in out[1]


def test_check_refuses_a_key_that_can_trade(monkeypatch):
    monkeypatch.setattr(okx_lease, "status", lambda env=None, now=None, st=None: {"node": "server"})
    env = {"OKX_NODE": "server", "CNQ_GH_TOKEN": "t", **IVAN, **MY}
    ok = mon.check(env, ivan_check=lambda: "Иван тест: ключ работает (demo)", myspot_check=lambda: "Мой портфель OKX: ключ работает")
    assert not any(mon.REFUSE in x for x in ok)
    bad = mon.check(env, ivan_check=lambda: "Иван тест: ключ работает · внимание: у ключа есть право торговли",
                    myspot_check=lambda: "Мой портфель OKX: ключ работает")
    assert any(mon.REFUSE in x for x in bad)
    assert any(mon.REFUSE in x for x in mon.check({"OKX_NODE": "server", **IVAN}, ivan_check=lambda: "ok"))   # no token → refused


def test_monitors_never_send_anything_but_get():
    for name in ("monitors.py", "ivan_watch.py", "myspot_watch.py"):
        src = open(os.path.join(ROOT, "cnq", "execution", name), encoding="utf-8").read()
        for bad in ("/trade/order\"", "/v5/order/create", "cancel-", "amend-", ".order(", "ap.connect", "Okx(", "BybitTrader"):
            assert bad not in src, (name, bad)
    assert 'method="GET"' in open(os.path.join(ROOT, "cnq", "execution", "myspot_watch.py"), encoding="utf-8").read()
    iw = open(os.path.join(ROOT, "cnq", "execution", "ivan_watch.py"), encoding="utf-8").read()
    fetch = iw[iw.index("def make_fetch"):iw.index("def _call")]
    assert "urllib.request.Request(f\"{base}{path}?{q}\"" in fetch and "data=" not in fetch and "method=" not in fetch   # a plain GET


def test_the_timer_unit_runs_the_monitors_with_the_autopilots_env_and_never_starts_by_itself():
    svc = open(os.path.join(ROOT, "deploy", "okx", "cnq-okx-monitors.service"), encoding="utf-8").read()
    tim = open(os.path.join(ROOT, "deploy", "okx", "cnq-okx-monitors.timer"), encoding="utf-8").read()
    assert "Type=oneshot" in svc and "EnvironmentFile=/etc/cnq-okx.env" in svc and "Environment=OKX_NODE=server" in svc
    assert "cnq.execution.monitors --once" in svc and "okx_autopilot" not in svc.split("[Service]")[1]
    assert "OnUnitActiveSec=15min" in tim
    ex = open(os.path.join(ROOT, "deploy", "okx", "cnq-okx.env.example"), encoding="utf-8").read()
    for k in list(IVAN) + list(MY):
        assert f"\n{k}=\n" in ex                                                  # names only, no values


def _sh(args, env, stdin=""):
    return subprocess.run(["bash"] + args, env={**os.environ, **env}, input=stdin, capture_output=True, text=True, timeout=60)


def test_server_script_monitors_command(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "calls.log"
    (b / "systemctl").write_text(f'#!/bin/bash\necho "systemctl $*" >> "{log}"\n')
    (b / "fakepy").write_text(f'#!/bin/bash\necho "py $*" >> "{log}"\ncase "$*" in *monitors*--check*) echo "${{FAKE_CHECK:-Иван тест: ключ работает}}";; esac\n')
    for f in b.iterdir():
        f.chmod(0o755)
    envf, units, state, src = tmp_path / "cnq-okx.env", tmp_path / "units", tmp_path / "state", tmp_path / "root"
    units.mkdir()
    src.mkdir()
    (src / ".cryptonova_ivan.json").write_text("{}")
    env = {"CNQ_OKX_TEST": "1", "CNQ_OKX_ENV": str(envf), "CNQ_MAIN_ENV": str(tmp_path / "cnq.env"), "CNQ_OKX_STATE": str(state),
           "CNQ_UNIT_DIR": str(units), "CNQ_OKX_PY": str(b / "fakepy"), "CNQ_SYSTEMCTL": str(b / "systemctl"), "CNQ_OKX_COPY_FROM": str(src)}
    script = os.path.join(ROOT, "scripts", "okx_autopilot_server.sh")
    base = "OKX_API_KEY=k\nOKX_API_SECRET=s\nOKX_API_PASSPHRASE=p\nOKX_AUTOPILOT=on\nCNQ_LIVE_TRADING=yes\nCNQ_GH_TOKEN=t\n"
    for lines, want in ((base, "включать нечего"), (base + "IVAN_BYBIT_KEY=k\nIVAN_BYBIT_SECRET=s\nIVAN_PASS=x\nHOME=/x\n", "Уберите из"),
                        (base.replace("CNQ_GH_TOKEN=t\n", "") + "IVAN_BYBIT_KEY=k\nIVAN_BYBIT_SECRET=s\nIVAN_PASS=x\n", "CNQ_GH_TOKEN")):
        envf.write_text(lines)
        r = _sh([script, "monitors"], env)
        assert r.returncode == 1 and want in r.stdout and os.listdir(units) == [], (want, r.stdout)
    envf.write_text(base + "IVAN_BYBIT_KEY=k\nIVAN_BYBIT_SECRET=s\nIVAN_PASS=x\n")
    r = _sh([script, "monitors"], {**env, "FAKE_CHECK": "Иван тест: право торговли · ОТКАЗ"})
    assert r.returncode == 1 and "ОТКАЗ" in r.stdout and os.listdir(units) == []
    r = _sh([script, "monitors"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(os.listdir(units)) == ["cnq-okx-monitors.service", "cnq-okx-monitors.timer"]
    calls = log.read_text()
    assert calls.index("monitors --check") < calls.index("systemctl enable --now cnq-okx-monitors.timer")
    assert "cnq-okx-autopilot" not in calls and (state / ".cryptonova_ivan.json").exists()      # the autopilot is not touched
    r = _sh([script, "monitors-stop"], env)
    assert "systemctl disable --now cnq-okx-monitors.timer" in log.read_text()
