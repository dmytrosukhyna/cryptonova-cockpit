"""Autopilot → Bybit demo mirror. Runs on YOUR machine or VPS (exchange keys never leave it; Bybit also blocks US cloud IPs).

  BYBIT_API_KEY=... BYBIT_API_SECRET=... python -m cnq.execution.mirror --check     # keys + demo balance, places nothing
  BYBIT_API_KEY=... BYBIT_API_SECRET=... python -m cnq.execution.mirror             # mirror the «норма» autopilot every 60 s

Every minute it reads the autopilot's demo desk (web/paper.json published by the server) and makes the Bybit demo account
match it: a NEW autopilot position (opened ≤ 45 min ago) → market order of the same size with the same stop-loss and target;
a position the autopilot closed (time exit, target, stop) → reduce-only market close; a moved stop → stop updated.
Positions that were already open before the mirror started are not chased. Env:
  CNQ_MIRROR_PROFILE=bal (cons | bal | aggr)   CNQ_MIRROR_SCALE=auto (sizes × Bybit balance / autopilot balance) or a number
  CNQ_MIRROR_SRC=<url of paper.json>          CNQ_LIVE_TRADING=yes → real account (default: Bybit demo)
Log: data/mirror.jsonl, state: data/mirror_state.json. Stdlib only."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from .bybit_trade import BybitTrader

SRC = os.environ.get("CNQ_MIRROR_SRC", "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/paper.json")
PROFILE = os.environ.get("CNQ_MIRROR_PROFILE", "bal")
SCALE_ENV = os.environ.get("CNQ_MIRROR_SCALE", "auto")   # auto = Bybit balance / autopilot balance (same % result on any deposit)
SCALE = 1.0
STATE = "data/mirror_state.json"
LOG = "data/mirror.jsonl"
FRESH_MS = 45 * 60_000


def log(ev: dict) -> None:
    os.makedirs("data", exist_ok=True)
    ev = {"t": int(time.time() * 1000), **ev}
    print(json.dumps(ev, ensure_ascii=False), flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:  # noqa: BLE001
        return {"started": int(time.time() * 1000), "trades": {}}


def save_state(st: dict) -> None:
    os.makedirs("data", exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


def fetch_desk() -> dict:
    req = urllib.request.Request(SRC + f"?t={int(time.time())}", headers={"Cache-Control": "no-cache", "User-Agent": "cnq-mirror"})
    with urllib.request.urlopen(req, timeout=20) as r:
        p = json.loads(r.read().decode())
    return (p.get("profiles") or {}).get(PROFILE) or p


def step(bx: BybitTrader, st: dict, desk: dict, now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    want = {t["id"]: t for t in desk.get("open", [])}
    done = {"opened": 0, "closed": 0, "stops": 0}
    pos = bx.positions()
    # 1. close what the autopilot closed
    for tid, m in list(st["trades"].items()):
        if tid in want:
            continue
        p = pos.get(m["symbol"])
        if p:
            side = "Sell" if p["side"] == "Buy" else "Buy"
            try:
                bx.place(m["symbol"], side, "Market", float(p["size"]), reduce_only=True, link_id=f"cnqx{now // 1000}")
                log({"ev": "close", "id": tid, "symbol": m["symbol"], "size": p["size"]}); done["closed"] += 1
            except Exception as e:  # noqa: BLE001
                log({"ev": "close_error", "id": tid, "error": str(e)[:200]}); continue
        else:
            log({"ev": "gone", "id": tid, "symbol": m["symbol"], "note": "закрыта биржей (стоп/цель)"})
        st["trades"].pop(tid)
    # 2. open new autopilot positions
    for tid, t in want.items():
        sym = t["symbol"] + "USDT"
        if tid in st["trades"]:
            m = st["trades"][tid]
            if t.get("stop_now") and abs(t["stop_now"] - m["stop"]) / m["stop"] > 1e-4 and sym in pos:
                try:
                    bx.set_stop(sym, t["stop_now"]); m["stop"] = t["stop_now"]; done["stops"] += 1
                    log({"ev": "stop", "id": tid, "stop": t["stop_now"]})
                except Exception as e:  # noqa: BLE001
                    log({"ev": "stop_error", "id": tid, "error": str(e)[:200]})
            continue
        if now - (t.get("opened") or 0) > FRESH_MS or (t.get("opened") or 0) < st["started"] - FRESH_MS or sym in pos:
            continue
        long = t["side"] == "LONG"
        qty = (t.get("qty_left") or t["qty"]) * SCALE
        notional = qty * t["entry"]
        lev = max(1, min(10, int(notional / max(1.0, desk.get("equity") or 10_000) * 2) + 1))
        sig = {"symbol": sym, "side": t["side"], "qty": qty, "leverage": lev, "order": "MARKET", "entry": t["entry"],
               "stop": t.get("stop_now") or t["stop"], "targets": [t["tps"][0]], "parts": [1.0]}
        try:
            res = bx.execute_signal(sig)
            st["trades"][tid] = {"symbol": sym, "side": t["side"], "qty": qty, "stop": sig["stop"], "opened": now}
            log({"ev": "open", "id": tid, "symbol": sym, "side": t["side"], "qty": qty, "stop": sig["stop"], "mode": res.get("mode")}); done["opened"] += 1
        except Exception as e:  # noqa: BLE001
            log({"ev": "open_error", "id": tid, "symbol": sym, "error": str(e)[:200]})
    save_state(st)
    return done


def main() -> int:
    bx = BybitTrader()
    if "--check" in sys.argv:
        eq = bx.equity()
        sc = float(SCALE_ENV) if SCALE_ENV != "auto" else eq / max(1.0, float(fetch_desk().get("equity") or 10_000))
        print(f"масштаб сделок ×{sc:.3f} (автопилот считает от своих $10 000, на Bybit — пропорционально балансу)")
        print(f"OK · {bx.mode} · баланс счёта ${eq:,.2f} · открытых позиций {len(bx.positions())} · источник {SRC} · профиль {PROFILE}")
        return 0
    global SCALE
    eq = bx.equity()
    SCALE = float(SCALE_ENV) if SCALE_ENV != "auto" else eq / max(1.0, float(fetch_desk().get("equity") or 10_000))
    st = load_state(); save_state(st)
    log({"ev": "start", "mode": bx.mode, "profile": PROFILE, "scale": SCALE, "equity": bx.equity()})
    while True:
        try:
            r = step(bx, st, fetch_desk())
            if any(r.values()):
                log({"ev": "step", **r})
        except Exception as e:  # noqa: BLE001
            log({"ev": "error", "error": str(e)[:200]})
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
