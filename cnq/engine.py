"""Быстрый контур — the trading desks every minute on a server (19.10.2026, owner: «сделки должны моментально открываться, цена
скачет очень быстро … должно работать онлайн прямо моментально»).

Why: on GitHub Actions a full step takes ~18 minutes (11 of them only candles) and starts every 16–30 minutes, so a desk could act
up to ~30 minutes after its 15-minute candle closed. Here the desks act within about a minute of it.

Layout on the server (deploy/engine/install.sh):
  /opt/cnq/repo — a clean git checkout: pull and push only;
  /opt/cnq/run  — where the engine works: data/ (its own agents.sqlite and candle store), web/ (outputs), links to the repo code.
Every minute (cycle): new 15-minute candles of the 18 core coins (only the missing bars) and a fifth of the 36 lab coins → the
CNPTS desks (paper), listing shorts, our own signals, the neutral basket, turbo, the signal feed (Telegram; the app bridge on
the same server reads run/web/signals_feed.json directly). Every 60 cycles: funding. Every 30: the quality card and «ядро +
альтсезон» (each does its daily work once a day). The 11-minute full candle check runs only once, in start.sh.
Every 60 (and on cycle 1 after a start): the strategy conveyor (stages LAB → PAPER → READY → RETIRED, demo only), then the main
futures desk selector (28.10, cnq/agents/desk_select.py → web/desk_select.json, read by the panel header and the real OKX futures).
Every SYNC_S: sync with GitHub — pull what the GitHub pipeline makes (news, research, macro …) into run/web and run/data, push
what the engine owns (OWNED + engine.json). The GitHub pipeline skips the engine's steps while the repository variable
CNQ_ENGINE = server, so there is only one writer of each file.
Nothing here sends orders to an exchange: every desk is a demo account. 22.10: the OKX signal step (okxsignal, OFF unless
CNQ_OKX_SIGNAL=on in /etc/cnq.env) publishes the header desks' trades as a public OKX Marketplace signal — it places no orders on any
account of ours; subscribers' own OKX bots trade on their accounts. Stdlib only."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

REPO = os.environ.get("CNQ_REPO_DIR", "/opt/cnq/repo")
RUN = os.environ.get("CNQ_RUN_DIR", "/opt/cnq/run")
INTERVAL_S = int(os.environ.get("CNQ_ENGINE_INTERVAL", "60"))
SYNC_S = int(os.environ.get("CNQ_ENGINE_SYNC", "120"))
# order matters: risk first (20.10 portfolio risk layer, cnq/risk: it judges the fresh candles and the desks' last positions, the
# desks then ask its gate before every entry), paper rewrites paper.json, the rest add
FAST = ["risk", "paper", "listings", "own", "lsn", "turbo", "signalfeed",
        "okxsignal",       # 22.10: CRYPTO NOVA on the OKX Signal Marketplace (cnq/agents/okx_signal.py) — off unless CNQ_OKX_SIGNAL=on
        "apphome", "apppush",
        "scorecard"]       # 20.10: last — it records what the steps above (and the GitHub feeds) just published → web/scorecard.json
SLOW = ["quality", "altport", "buynow"]     # 22.10: «Если покупать сегодня» for the app (cnq/agents/buy_now.py), every 30 minutes
# 20.10: strategy conveyor LAB → PAPER → READY → RETIRED (cnq/agents/conveyor.py): once an hour (cycle 1 = soon after a restart, then
# every 60 cycles), after the desks; it writes data/conveyor_state.json (read by the risk gate) and web/conveyor.json
HOURLY = ["conveyor",
          "deskselect"]    # 28.10: the main futures desk (cnq/agents/desk_select.py) — after the conveyor, whose stages it reads
OWNED = ["paper.json", "signals_feed.json", "calendar.json", "quality.json", "altseason.json", "engine.json", "app_home.json", "app_btc.json",
         # 20.10: the app home in the other languages of the app (cnq/agents/i18n.py LANGS), written in the same step as app_home.json
         "app_home.en.json", "app_home.uk.json", "app_home.el.json", "app_home.es.json",
         "conveyor.json",
         "buy_now.json",                                                   # 22.10: the app's «Если покупать сегодня» (SLOW step)
         "risk.json",                                                      # 20.10: the risk layer's public summary
         "scorecard.json",                                                 # 20.10: the public prediction scorecard
         "okx_signal.json",                                                # 22.10: the OKX signal sender's public log (no token)
         "desk_select.json"]                                               # 28.10: the main futures desk (state + decision; public)
# the engine's own state — never overwritten by a pull (20.10: + the conveyor's state, the risk layer's kill switch, state, reset
# request and log)
DB_OWNED = {"agents.sqlite", "cnq.sqlite", "conveyor_state.json", "kill_switch.json", "risk_state.json", "risk_reset.json", "risk_log.jsonl"}
STEP_TIMEOUT = {"risk": 60, "paper": 150, "listings": 90, "own": 150, "lsn": 90, "turbo": 60, "signalfeed": 60, "quality": 240, "altport": 90, "buynow": 150,
                "conveyor": 120, "scorecard": 60, "okxsignal": 60, "deskselect": 60}
# 20.10: the public scorecard's append-only, hash-chained ledgers (cnq/agents/scorecard.py) — two active files and the directory of
# their closed segments. Unlike DB_OWNED they are public: the engine is their only writer, a pull never overwrites them, and every
# sync commits them (like OWNED, but in data/). A fresh install starts from the repo's copy (prepare).
DATA_OWNED = ["scorecard_ledger.jsonl", "scorecard_outcomes.jsonl", "scorecard",
              "desk_select.jsonl"]           # 28.10: one line per switch of the main futures desk (cnq/agents/desk_select.py)


def _now_ms() -> int:
    return int(time.time() * 1000)


def sh(args: list[str], cwd: str, timeout: int = 120, env: dict | None = None) -> tuple[bool, float, str]:
    """run one step with a hard time limit. 20.10: the engine went silent mid-cycle at 08:45 UTC — subprocess.run(timeout=…) can
    wait forever after the kill when a grandchild still holds the output pipes, so the step gets its own process group and the
    whole group is killed, and reading its output after the kill is bounded too."""
    t0 = time.time()
    _now_step(args, timeout)
    try:
        p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                             preexec_fn=_cap_memory if "cnq.cli" in args else None)
    except Exception as e:  # noqa: BLE001
        return False, round(time.time() - t0, 1), repr(e)[:300]
    try:
        out = err = None
        peak = 0
        while True:                                  # wait in 2-second slices and note the step's memory each time (→ /v2/diag)
            try:
                out, err = p.communicate(timeout=2)
                break
            except subprocess.TimeoutExpired:
                if time.time() - t0 > timeout:
                    raise
                rss = _rss_mb(p.pid)
                if rss > peak:
                    peak = rss
                    _now_rss(rss)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        try:
            p.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        return False, round(time.time() - t0, 1), f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        p.kill()
        return False, round(time.time() - t0, 1), repr(e)[:300]
    tail = (out or "")[-300:] if p.returncode == 0 else ((err or "") + (out or ""))[-600:]
    _phase(f"done {_step_name(args)} rc={p.returncode} {round(time.time() - t0, 1)}s")
    return p.returncode == 0, round(time.time() - t0, 1), tail.strip()


# 20.10: from 08:45 UTC the service restarted every ~80 s, always while «paper» ran — the step's memory went over the service's
# MemoryMax=2G, the kernel's OOM killer fired and systemd stopped the whole unit (no status, no commits). A step now has its own
# address-space cap: past it the step fails with MemoryError (its error shows in engine.json / /v2/diag), the engine lives on.
STEP_MEM_MB = int(os.environ.get("CNQ_STEP_MEM_MB", "1800"))


def _cap_memory() -> None:
    try:
        import resource
        lim = STEP_MEM_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:  # noqa: BLE001
        pass


def _peak_mb() -> int:
    """the largest resident size any finished step has had so far (MB) — grows when a step sets a new record"""
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024)
    except Exception:  # noqa: BLE001
        return 0


_STARTED = int(time.time() * 1000)


def _step_name(args: list[str]) -> str:
    return next((a for a in args if not a.startswith("-") and a not in ("-m", "cnq.cli") and not a.endswith("python") and
                 not a.endswith("python3") and "/" not in a), " ".join(args)[-60:])


def _write_now(doc: dict) -> None:
    try:
        with open(os.path.join(RUN, "web", "engine_now.json"), "w", encoding="utf-8") as f:
            json.dump({**doc, "pid": os.getpid(), "started": _STARTED}, f)
    except Exception:  # noqa: BLE001
        pass


def _now_step(args: list[str], timeout: int) -> None:
    """web/engine_now.json: which step the engine is in right now (the API's /v2/diag shows it) — a hang or a restart loop is
    visible from outside without logging in to the server"""
    _write_now({"step": _step_name(args), "since": int(time.time() * 1000), "timeout": timeout})


def _rss_mb(pid: int) -> int:
    try:
        for line in open(f"/proc/{pid}/status", encoding="utf-8"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def _now_rss(mb: int) -> None:
    try:
        p = os.path.join(RUN, "web", "engine_now.json")
        d = json.load(open(p, encoding="utf-8"))
        d["rss_mb"] = mb
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:  # noqa: BLE001
        pass


def _phase(text: str) -> None:
    """the last thing the engine finished (kept next to the current step), so a crash between steps is located too"""
    try:
        p = os.path.join(RUN, "web", "engine_now.json")
        d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        d["phase"], d["phase_t"] = text, int(time.time() * 1000)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:  # noqa: BLE001
        pass


def prepare() -> None:
    """run/ = links to the repo code + its own data/ and web/ (first start: copies of the repo's)."""
    os.makedirs(os.path.join(RUN, "data"), exist_ok=True)
    os.makedirs(os.path.join(RUN, "web"), exist_ok=True)
    for name in ("cnq", "scripts", "config"):
        link, target = os.path.join(RUN, name), os.path.join(REPO, name)
        if os.path.exists(target) and not os.path.islink(link):
            if os.path.exists(link):
                shutil.rmtree(link)
            os.symlink(target, link)
    for sub in ("data", "web"):
        src = os.path.join(REPO, sub)
        for f in os.listdir(src) if os.path.isdir(src) else []:
            a, b = os.path.join(src, f), os.path.join(RUN, sub, f)
            if os.path.isfile(a) and not os.path.exists(b):
                shutil.copy2(a, b)
    for d in DATA_OWNED:                                    # directories the engine owns (closed scorecard segments): first start only
        a, b = os.path.join(REPO, "data", d), os.path.join(RUN, "data", d)
        if os.path.isdir(a) and not os.path.exists(b):
            shutil.copytree(a, b)


def pull_inputs() -> None:
    """Fresh inputs from the GitHub pipeline (news, macro, markets …) — everything except what the engine owns."""
    for sub, keep in (("web", set(OWNED)), ("data", DB_OWNED | set(DATA_OWNED))):
        src = os.path.join(REPO, sub)
        for f in os.listdir(src) if os.path.isdir(src) else []:
            a = os.path.join(src, f)
            if f in keep or not os.path.isfile(a):
                continue
            b = os.path.join(RUN, sub, f)
            try:
                if not os.path.exists(b) or os.path.getmtime(a) > os.path.getmtime(b) or os.path.getsize(a) != os.path.getsize(b):
                    shutil.copy2(a, b)
            except OSError:
                pass


def copy_out_data() -> list[str]:
    """the engine's own public data/ files and directories (DATA_OWNED) → the repo checkout; returns the paths to «git add»"""
    out = []
    for f in DATA_OWNED:
        a, b = os.path.join(RUN, "data", f), os.path.join(REPO, "data", f)
        try:
            if os.path.isdir(a):
                os.makedirs(b, exist_ok=True)
                for x in os.listdir(a):
                    if x.endswith(".tmp") or not os.path.isfile(os.path.join(a, x)):
                        continue
                    xa, xb = os.path.join(a, x), os.path.join(b, x)
                    if not os.path.exists(xb) or os.path.getsize(xa) != os.path.getsize(xb):
                        shutil.copy2(xa, xb)
                out.append(f"data/{f}")
            elif os.path.isfile(a):
                shutil.copy2(a, b)
                out.append(f"data/{f}")
        except OSError:
            pass
    return out


def sync(push: bool = True) -> dict:
    """git pull (the repo stays clean), copy inputs in, copy the engine's files out, commit, push."""
    # 20.10, the real cause of the restart loop since 08:45 UTC (seen in /v2/diag): every pull started git's automatic «gc --auto»;
    # its «pack-objects» over the 1.5 GB repo grew to 1.6+ GB inside this service, the service hit MemoryMax=2G, the OOM killer
    # stopped the whole unit, and after the restart the next pull started the same gc again. The engine never lets git collect
    # garbage now (gc.auto=0, maintenance off); if git ever packs, it stays small (one thread, 64 MB windows).
    git = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "pack.threads=1", "-c", "pack.windowMemory=64m",
           "-C", REPO]
    ok, _t, out = sh(git + ["pull", "-q", "--rebase", "--autostash"], REPO, 120)
    if not ok:
        sh(git + ["rebase", "--abort"], REPO, 30)
        sh(git + ["reset", "-q", "--hard", "origin/main"], REPO, 30)
    pull_inputs()
    if not push:
        return {"pulled": ok, "pushed": False}
    changed = []
    for f in OWNED:
        a, b = os.path.join(RUN, "web", f), os.path.join(REPO, "web", f)
        if os.path.exists(a):
            shutil.copy2(a, b)
            changed.append(f"web/{f}")
    sh(git + ["add"] + changed, REPO, 30)
    data_changed = copy_out_data()
    if data_changed:                                        # separate «git add»: a problem with these never blocks the desks' files
        sh(git + ["add"] + data_changed, REPO, 30)
    ok_c, _t, out_c = sh(git + ["commit", "-q", "-m", f"engine: desks {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"], REPO, 30)
    pushed = False
    if ok_c:
        for _ in range(3):
            p_ok, _t, _o = sh(git + ["push", "-q", "origin", "HEAD:main"], REPO, 60)
            if p_ok:
                pushed = True
                break
            sh(git + ["pull", "-q", "--rebase", "-X", "theirs"], REPO, 60)
    return {"pulled": ok, "pushed": pushed, "committed": ok_c}


def cycle(n: int, env: dict) -> dict:
    steps = {}
    py = sys.executable
    # new bars of the 18 core coins every cycle + a fifth of the 36 lab coins (each lab coin every 5 minutes); funding once an hour.
    # Never the 11-minute full check here — it would stop the minute loop (start.sh runs it once before the service starts).
    steps["candles"] = sh([py, "scripts/ci_refresh.py"], RUN, 120,
                          {**env, "CNQ_REFRESH": "quick", "CNQ_REFRESH_EXTRA": f"{n % 5}/5"})
    if n % 60 == 30:
        steps["funding"] = sh([py, "scripts/ci_refresh.py"], RUN, 120, {**env, "CNQ_REFRESH": "funding"})
    for cmd in FAST:
        steps[cmd] = sh([py, "-m", "cnq.cli", cmd, "--out", "web"], RUN, STEP_TIMEOUT.get(cmd, 120), env)
    if n % 30 == 0:
        for cmd in SLOW:
            steps[cmd] = sh([py, "-m", "cnq.cli", cmd, "--out", "web"], RUN, STEP_TIMEOUT.get(cmd, 120), env)
    if n % 60 == 1:
        for cmd in HOURLY:
            steps[cmd] = sh([py, "-m", "cnq.cli", cmd, "--out", "web"], RUN, STEP_TIMEOUT.get(cmd, 120), env)
    out = {k: {"ok": v[0], "sec": v[1], **({} if v[0] else {"err": v[2][-300:]})} for k, v in steps.items()}
    out["_peak_mb"] = {"ok": True, "sec": 0, "mb": _peak_mb()}
    return out


def write_status(n: int, steps: dict, t_cycle: float, last_sync: dict | None, started: int) -> None:
    doc = {"asof": _now_ms(), "started": started, "cycle": n, "cycle_sec": round(t_cycle, 1), "interval_sec": INTERVAL_S,
           "steps": steps, "errors": [k for k, v in steps.items() if not v["ok"]], "last_sync": last_sync,
           "about_ru": "Быстрый контур на сервере: торговые демо-столы каждую минуту, свечи — только новые."}
    with open(os.path.join(RUN, "web", "engine.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CRYPTO NOVA fast engine (server)")
    ap.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = run forever)")
    ap.add_argument("--no-push", action="store_true", help="never commit / push (tests)")
    ap.add_argument("--no-sync", action="store_true", help="do not touch git at all (tests)")
    a = ap.parse_args(argv)
    env = {**os.environ, "PYTHONPATH": RUN, "PYTHONUNBUFFERED": "1"}
    prepare()
    code_mtime = os.path.getmtime(os.path.realpath(__file__))        # a git pull that changes the engine → restart (systemd)
    started, n, last_sync, t_sync = _now_ms(), 0, None, 0.0
    while True:
        try:
            n, last_sync, t_sync, stop = _loop_once(a, env, n, last_sync, t_sync, started, code_mtime)
        except Exception:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()[-1500:]
            print(tb, flush=True)
            try:
                with open(os.path.join(RUN, "web", "engine_crash.json"), "w", encoding="utf-8") as f:
                    json.dump({"t": _now_ms(), "cycle": n, "trace": tb}, f)
            except Exception:  # noqa: BLE001
                pass
            n += 1
            time.sleep(5)
            continue
        if stop:
            return 0


def _loop_once(a, env, n, last_sync, t_sync, started, code_mtime):
    """one minute of the engine; returns (n, last_sync, t_sync, stop)"""
    if True:
        t0 = time.time()
        if not a.no_sync and time.time() - t_sync >= SYNC_S:
            last_sync = {"t": _now_ms(), **sync(push=False)}          # fresh inputs before the desks act
        steps = cycle(n, env)
        dt_ = time.time() - t0
        _phase(f"cycle {n} steps done {dt_:.0f}s")
        write_status(n, steps, dt_, last_sync, started)
        _phase(f"cycle {n} status written")
        if not a.no_sync and time.time() - t_sync >= SYNC_S:
            last_sync = {"t": _now_ms(), **sync(push=not a.no_push)}
            t_sync = time.time()
        bad = [k for k, v in steps.items() if not v["ok"]]
        print(time.strftime("%H:%M:%S"), f"cycle {n} {dt_:.0f}s", "errors: " + ", ".join(bad) if bad else "ok", flush=True)
        n += 1
        if a.cycles and n >= a.cycles:
            return n, last_sync, t_sync, True
        try:
            if not a.cycles and os.path.getmtime(os.path.realpath(__file__)) != code_mtime:
                print("engine code updated by git pull — exiting so systemd restarts the new version", flush=True)
                return n, last_sync, t_sync, True
        except OSError:
            pass
        # start every cycle 3 s after a minute boundary: a 15-minute candle closes at :00 / :15 / :30 / :45, so the desks see it
        # within seconds (history 2020–26: the cascade buy entered 15 minutes late lost 40 % of its edge, and all of it in 2025–26)
        nxt = (int((time.time() - 3) // INTERVAL_S) + 1) * INTERVAL_S + 3
        _phase(f"cycle {n - 1} done, sleeping")
        time.sleep(max(1.0, nxt - time.time()))
    return n, last_sync, t_sync, False


if __name__ == "__main__":
    sys.exit(main())
