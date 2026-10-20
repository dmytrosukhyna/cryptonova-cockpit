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
Every SYNC_S: sync with GitHub — pull what the GitHub pipeline makes (news, research, macro …) into run/web and run/data, push
what the engine owns (OWNED + engine.json). The GitHub pipeline skips the engine's steps while the repository variable
CNQ_ENGINE = server, so there is only one writer of each file.
Nothing here sends orders to an exchange: every desk is a demo account. Stdlib only."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.environ.get("CNQ_REPO_DIR", "/opt/cnq/repo")
RUN = os.environ.get("CNQ_RUN_DIR", "/opt/cnq/run")
INTERVAL_S = int(os.environ.get("CNQ_ENGINE_INTERVAL", "60"))
SYNC_S = int(os.environ.get("CNQ_ENGINE_SYNC", "120"))
FAST = ["paper", "listings", "own", "lsn", "turbo", "signalfeed", "apphome", "apppush"]          # order matters: paper rewrites paper.json, the rest add
SLOW = ["quality", "altport"]
OWNED = ["paper.json", "signals_feed.json", "calendar.json", "quality.json", "altseason.json", "engine.json", "app_home.json", "app_btc.json",
         # 20.10: the app home in the other languages of the app (cnq/agents/i18n.py LANGS), written in the same step as app_home.json
         "app_home.en.json", "app_home.uk.json", "app_home.el.json", "app_home.es.json"]
DB_OWNED = {"agents.sqlite", "cnq.sqlite"}                                 # the engine's own state — never overwritten by a pull
STEP_TIMEOUT = {"paper": 150, "listings": 90, "own": 150, "lsn": 90, "turbo": 60, "signalfeed": 60, "quality": 240, "altport": 90}


def _now_ms() -> int:
    return int(time.time() * 1000)


def sh(args: list[str], cwd: str, timeout: int = 120, env: dict | None = None) -> tuple[bool, float, str]:
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, timeout=timeout, capture_output=True, text=True, env=env)
        tail = (p.stdout or "")[-300:] if p.returncode == 0 else ((p.stderr or "") + (p.stdout or ""))[-600:]
        return p.returncode == 0, round(time.time() - t0, 1), tail.strip()
    except subprocess.TimeoutExpired:
        return False, round(time.time() - t0, 1), f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        return False, round(time.time() - t0, 1), repr(e)[:300]


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


def pull_inputs() -> None:
    """Fresh inputs from the GitHub pipeline (news, macro, markets …) — everything except what the engine owns."""
    for sub, keep in (("web", set(OWNED)), ("data", DB_OWNED)):
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


def sync(push: bool = True) -> dict:
    """git pull (the repo stays clean), copy inputs in, copy the engine's files out, commit, push."""
    git = ["git", "-C", REPO]
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
    return {k: {"ok": v[0], "sec": v[1], **({} if v[0] else {"err": v[2][-300:]})} for k, v in steps.items()}


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
        t0 = time.time()
        if not a.no_sync and time.time() - t_sync >= SYNC_S:
            last_sync = {"t": _now_ms(), **sync(push=False)}          # fresh inputs before the desks act
        steps = cycle(n, env)
        dt_ = time.time() - t0
        write_status(n, steps, dt_, last_sync, started)
        if not a.no_sync and time.time() - t_sync >= SYNC_S:
            last_sync = {"t": _now_ms(), **sync(push=not a.no_push)}
            t_sync = time.time()
        bad = [k for k, v in steps.items() if not v["ok"]]
        print(time.strftime("%H:%M:%S"), f"cycle {n} {dt_:.0f}s", "errors: " + ", ".join(bad) if bad else "ok", flush=True)
        n += 1
        if a.cycles and n >= a.cycles:
            return 0
        try:
            if not a.cycles and os.path.getmtime(os.path.realpath(__file__)) != code_mtime:
                print("engine code updated by git pull — exiting so systemd restarts the new version", flush=True)
                return 0
        except OSError:
            pass
        # start every cycle 3 s after a minute boundary: a 15-minute candle closes at :00 / :15 / :30 / :45, so the desks see it
        # within seconds (history 2020–26: the cascade buy entered 15 minutes late lost 40 % of its edge, and all of it in 2025–26)
        nxt = (int((time.time() - 3) // INTERVAL_S) + 1) * INTERVAL_S + 3
        time.sleep(max(1.0, nxt - time.time()))


if __name__ == "__main__":
    sys.exit(main())
