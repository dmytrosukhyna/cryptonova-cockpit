"""Autopilot → Bybit demo mirror. Runs on YOUR machine or VPS (exchange keys never leave it; Bybit also blocks US cloud IPs).

  BYBIT_API_KEY=... BYBIT_API_SECRET=... python -m cnq.execution.mirror --check     # keys + demo balance, places nothing
  BYBIT_API_KEY=... BYBIT_API_SECRET=... python -m cnq.execution.mirror             # mirror the «норма» autopilot every 60 s

Every minute it reads the autopilot's demo desk (web/paper.json published by the server) and makes the Bybit demo account
match it: a NEW autopilot position (opened ≤ 45 min ago) → market order of the same size with the same stop-loss and target;
a position the autopilot closed (time exit, target, stop) → reduce-only market close; a moved stop → stop updated.
Positions that were already open before the mirror started are not chased. Env:
  CNQ_MIRROR_PROFILE=bal (cons | bal | aggr)   CNQ_MIRROR_SCALE=auto (sizes × Bybit balance / autopilot balance) or a number
  CNQ_MIRROR_SRC=<url of paper.json>          CNQ_LIVE_TRADING=yes → real account (default: Bybit demo)
Optional «Иван тест» monitor (cnq/execution/ivan_watch.py): with IVAN_BYBIT_KEY / IVAN_BYBIT_SECRET / IVAN_PASS and CNQ_GH_TOKEN
set, every 15 min a read-only snapshot of that account is encrypted and published to web/ivan.enc.json.
Optional «Мой портфель · OKX» (cnq/execution/myspot_watch.py): with MYSPOT_OKX_KEY / MYSPOT_OKX_SECRET / MYSPOT_OKX_PASSPHRASE
(a READ-ONLY OKX key) and MYSPOT_PASS, every 15 min the owner's OKX spot portfolio + the model's plan are encrypted and published
to web/myspot.enc.json (nothing is traded).
«Реальный счёт CRYPTO NOVA на OKX» (cnq/execution/public_real.py): with OKX_API_* (the autopilot's key, read here with GET only)
and CNQ_GH_TOKEN, every 15 min a public summary of the SUB-ACCOUNT only → web/public_real.json (OKX_PUBLIC=off — never).
Only while the autopilot runs on the Mac (the lease's node); in server mode the server's autopilot service writes it.
28.10: the same for «Иван тест» and «Мой портфель» — in server mode the server's monitor timer publishes them (monitors.py).
«Автопилот OKX» (cnq/execution/okx_autopilot.py) runs in its OWN window (okx_autopilot --loop), not here: CNQ_LIVE_TRADING=yes
for it must never reach this Bybit futures mirror. `--check` here also prints the autopilot's key, host and state.
Log: data/mirror.jsonl, state: data/mirror_state.json. Stdlib only."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from . import ivan_watch, myspot_watch, okx_autopilot, public_real
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


GH_TOKEN = os.environ.get("CNQ_GH_TOKEN", "")          # optional: fine-grained token (this repo, Contents: write) → web/bybit.json
GH_REPO = os.environ.get("CNQ_GH_REPO", "dmytrosukhyna/cryptonova-cockpit")


def pulse() -> bool:
    """GitHub delays/skips scheduled runs (gaps of up to 4 h were seen); a commit to web/pulse.json triggers the server run.
    Called every 30 min while the mirror runs — the panel, channel and paper desk then refresh on time."""
    if not GH_TOKEN:
        return False
    import base64
    url = f"https://api.github.com/repos/{GH_REPO}/contents/web/pulse.json"
    hdr = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-mirror"}
    content = base64.b64encode(json.dumps({"t": int(time.time() * 1000)}).encode()).decode()
    return ivan_watch.put_contents(url, hdr, content, "pulse: run the server")   # retries a 409 conflict with a fresh sha


def publish(bx: BybitTrader, st: dict) -> bool:
    """Push the Bybit demo account state (balance, open positions — never keys) to web/bybit.json so the site can show it."""
    if not GH_TOKEN:
        return False
    import base64
    eq = bx.equity(); pos = bx.positions()
    st.setdefault("start_equity", eq)
    body = {"asof": int(time.time() * 1000), "mode": bx.mode, "equity": round(eq, 2), "start_equity": round(st["start_equity"], 2),
            "positions": [{"symbol": k.replace("USDT", ""), "side": "LONG" if v.get("side") == "Buy" else "SHORT", "size": float(v.get("size") or 0),
                           "entry": float(v.get("avgPrice") or 0), "upnl": round(float(v.get("unrealisedPnl") or 0), 2),
                           "stop": float(v.get("stopLoss") or 0) or None} for k, v in pos.items()]}
    url = f"https://api.github.com/repos/{GH_REPO}/contents/web/bybit.json"
    hdr = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-mirror"}
    content = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
    return ivan_watch.put_contents(url, hdr, content, "bybit demo status")   # retries a 409 conflict with a fresh sha


_SAID: dict = {}


def _monitor_writer(name: str) -> bool:
    """28.10 (owner): the monitors moved to the server (cnq/execution/monitors.py). One writer: the Mac publishes «Иван тест» and
    «Мой портфель» only while the lease names the Mac (okx_lease «node»), like web/public_real.json. Says why once."""
    from . import monitors
    ok, why = monitors.writer()
    if not ok and _SAID.get(name) != why:
        _SAID[name] = why
        print(f"{name}: {why}", flush=True)
    if ok:
        _SAID.pop(name, None)
    return ok


def myspot_tick() -> bool:
    """«Мой портфель · OKX»: one read-only snapshot → encrypted web/myspot.enc.json. A failure prints one line and never stops the mirror."""
    try:
        if not _monitor_writer("Мой портфель OKX"):
            return False
        return myspot_watch.run() is not None
    except Exception as e:  # noqa: BLE001
        print(myspot_watch.describe_error(e), flush=True)
        return False


def public_real_tick() -> bool:
    """22.10 «Реальный счёт CRYPTO NOVA на OKX»: the sub-account only → web/public_real.json (unencrypted, public_real.py). A failure
    prints one line and never stops the mirror."""
    try:
        return public_real.run() is not None
    except Exception as e:  # noqa: BLE001
        print("Реальный счёт OKX: " + okx_autopilot.describe_error(e).replace("Автопилот OKX: ", ""), flush=True)
        return False


def ivan_tick() -> bool:
    """«Иван тест»: one read-only snapshot → encrypted web/ivan.enc.json. A failure prints one line and never stops the mirror."""
    try:
        if not _monitor_writer("Иван тест"):
            return False
        return ivan_watch.run() is not None
    except Exception as e:  # noqa: BLE001
        print(ivan_watch.describe_error(e), flush=True)
        return False


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
        try:
            eq = bx.equity()
            sc = float(SCALE_ENV) if SCALE_ENV != "auto" else eq / max(1.0, float(fetch_desk().get("equity") or 10_000))
            print(f"масштаб сделок ×{sc:.3f} (автопилот считает от своих $10 000, на Bybit — пропорционально балансу)")
            print(f"OK · {bx.mode} · баланс счёта ${eq:,.2f} · открытых позиций {len(bx.positions())} · источник {SRC} · профиль {PROFILE}")
        finally:
            msg = ivan_watch.check()          # None (prints nothing) when the IVAN_* lines are not in ~/.cryptonova.env
            if msg:
                print(msg + ("" if GH_TOKEN else " · нет CNQ_GH_TOKEN — публиковать не буду"), flush=True)
            if myspot_watch.creds() or okx_autopilot.creds():     # «Мой портфель» (read-only key) + «Автопилот OKX» (trade key)
                for line in okx_autopilot.check():
                    print(line + ("" if GH_TOKEN or not line.startswith("Мой портфель") else " · нет CNQ_GH_TOKEN — публиковать не буду"), flush=True)
        return 0
    global SCALE
    eq = bx.equity()
    SCALE = float(SCALE_ENV) if SCALE_ENV != "auto" else eq / max(1.0, float(fetch_desk().get("equity") or 10_000))
    st = load_state(); save_state(st)
    r = {}
    log({"ev": "start", "mode": bx.mode, "profile": PROFILE, "scale": SCALE, "equity": bx.equity()})
    last_pub = 0.0
    last_pulse = time.time() - 25 * 60
    ivan_on, last_ivan, ivan_said = bool(GH_TOKEN and ivan_watch.creds()), 0.0, False
    my_on, last_my, my_said = bool(GH_TOKEN and myspot_watch.creds()), 0.0, False
    real_on, last_real, real_said = bool(GH_TOKEN and public_real.enabled()), 0.0, False
    while True:
        try:
            r = step(bx, st, fetch_desk())
            if any(r.values()):
                log({"ev": "step", **r})
        except Exception as e:  # noqa: BLE001
            log({"ev": "error", "error": str(e)[:200]})
        if GH_TOKEN and (time.time() - last_pub > 300 or any(r.values() if isinstance(r, dict) else [])):
            try:
                if publish(bx, st):
                    last_pub = time.time(); save_state(st)
            except Exception as e:  # noqa: BLE001
                log({"ev": "publish_error", "error": str(e)[:200]})
        if GH_TOKEN and time.time() - last_pulse > 30 * 60:
            try:
                if pulse():
                    last_pulse = time.time(); log({"ev": "pulse"})
            except Exception as e:  # noqa: BLE001
                log({"ev": "pulse_error", "error": str(e)[:200]}); last_pulse = time.time()
        if ivan_on and time.time() - last_ivan >= ivan_watch.EVERY_S:   # once at start, then every IVAN_EVERY min (default 15)
            last_ivan = time.time()
            if ivan_tick() and not ivan_said:
                ivan_said = True; print(f"Иван тест: монитор работает, web/ivan.enc.json обновляется каждые {ivan_watch.EVERY_S / 60:g} мин", flush=True)
        if my_on and time.time() - last_my >= myspot_watch.EVERY_S:   # once at start, then every MYSPOT_EVERY min (default 15)
            last_my = time.time()
            if myspot_tick() and not my_said:
                my_said = True; print(f"Мой портфель OKX: карточка обновляется каждые {myspot_watch.EVERY_S / 60:g} мин (данные зашифрованы)", flush=True)
        if real_on and time.time() - last_real >= public_real.EVERY_S:   # once at start, then every 15 min
            last_real = time.time()
            if public_real_tick() and not real_said:
                real_said = True; print("Реальный счёт CRYPTO NOVA на OKX: web/public_real.json обновляется каждые 15 мин (только субаккаунт)", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
