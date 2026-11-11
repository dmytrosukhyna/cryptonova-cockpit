"""«Разворот» — a PAPER test desk (11.11.2026, owner 11:30: «когда наша удачная сделка закрылась — дождаться разворота и открыть
обратную, прыгать по уровням»). TEST, owner-only, demo money. It is NOT in web/paper.json and has no «whales:» / «ivan:» signal id,
so the real account can never open its trades (okx_futures opens only those two prefixes).

What it does, every pipeline run (cli «whalestudy», after the copy desk is published):
  1. reads OUR closed copies (web/whales_live.json desk «closed») that closed after the desk started and moved ≥ MIN in our favour;
  2. for each, the OPPOSITE side becomes a candidate; the entry rule of the registered variant (config/reversal_desk.json — the
     best of the 54 variants registered before the backtest, docs/research/reversal-desk-2026-11-11.md) decides:
       «now»    — at once (within 2 h of the close);
       «level»  — at once, only when the close was within 2 % of the 20-day extreme the trade ran into;
       «candle» — at the close of the first 4-hour block within 24 h that moves ≥ 1.5 % in the reversal direction;
  3. a paper position: 10 % of the desk's equity, no leverage, one per coin, at most 5; a stop (−8 % / −16 %) and a time exit
     (1 / 3 / 7 days); 0.10 % costs per round trip, funding from OKX at the close.
Prices: OKX {COIN}-USDT-SWAP (ticker, hourly candles) — public data, no keys. State: ws_kv «reversal_desk». → web/reversal_desk.json."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

H = 3_600_000
DAY = 24 * H
START = 10_000.0
SLOT = 0.10
MAX_OPEN = 5
COST = 0.001
CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "reversal_desk.json")
DEFAULT = {"entry": "now", "min_profit": 0.05, "hold_days": 3, "stop": 0.08}
ENTRY_RU = {"now": "сразу после закрытия нашей сделки", "level": "только у 20-дневного уровня (в пределах 2%)",
            "candle": "после 4-часовой свечи разворота (≥ 1.5%) в течение суток"}
OKX = "https://www.okx.com"


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _http(path: str, **q):
    req = urllib.request.Request(f"{OKX}{path}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "cnq-reversal-desk/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg') or d.get('code')}")
    return d.get("data") or []


def cfg(path: str | None = None) -> dict:
    try:
        with open(path or CFG, encoding="utf-8") as f:
            d = json.load(f)
        return {**DEFAULT, **(d.get("variant") or {}), "backtest": d.get("backtest"), "id": d.get("id")}
    except Exception:  # noqa: BLE001
        return dict(DEFAULT)


def last_px(coin: str, http=_http) -> float | None:
    try:
        return _f((http("/api/v5/market/ticker", instId=f"{coin}-USDT-SWAP") or [{}])[0].get("last")) or None
    except Exception:  # noqa: BLE001
        return None


def hourly(coin: str, pages: int, http=_http) -> dict:
    """{bar open ms: (o, h, l, c)} — the last pages × 100 hourly bars"""
    out, after = {}, None
    for _ in range(pages):
        try:
            d = http("/api/v5/market/history-candles", instId=f"{coin}-USDT-SWAP", bar="1H", limit=100, **({"after": after} if after else {}))
        except Exception:  # noqa: BLE001
            break
        if not d:
            break
        for r in d:
            out[int(r[0])] = (_f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]))
        after = d[-1][0]
    return out


def funding(coin: str, t0: int, t1: int, http=_http) -> float:
    try:
        d = http("/api/v5/public/funding-rate-history", instId=f"{coin}-USDT-SWAP", limit=100)
        return sum(_f(r.get("realizedRate") or r.get("fundingRate")) for r in d if t0 < int(r["fundingTime"]) <= t1)
    except Exception:  # noqa: BLE001
        return 0.0


def moved(c: dict) -> float:
    s = 1 if c.get("side") == "LONG" else -1
    return s * (_f(c.get("exit")) / _f(c.get("entry")) - 1) if _f(c.get("entry")) and _f(c.get("exit")) else 0.0


def entry_now(v: dict, cand: dict, now: int, px: float, K: dict | None) -> tuple[bool | None, str]:
    """(True = open now / False = drop the candidate / None = keep waiting, reason)"""
    age = now - cand["t"]
    if v["entry"] == "now":
        return (True, "сразу после закрытия нашей сделки") if age <= 2 * H else (False, "прошло больше 2 часов после закрытия — поздно")
    if v["entry"] == "level":
        if age > 2 * H:
            return False, "прошло больше 2 часов после закрытия — поздно"
        win = [b for t, b in (K or {}).items() if cand["t"] - 20 * DAY <= t < cand["t"]]
        if len(win) < 200:
            return False, "нет 20 дней часовых свечей — уровень не проверить"
        if cand["closed_side"] == "SHORT":
            ext = min(b[2] for b in win)
            ok = cand["exit"] <= ext * 1.02
        else:
            ext = max(b[1] for b in win)
            ok = cand["exit"] >= ext * 0.98
        return (True, f"закрытие у 20-дневного уровня {ext:g}") if ok else (False, f"закрытие далеко от 20-дневного уровня {ext:g}")
    hb = cand["t"] // H * H                                    # «candle»
    for j in range(6):
        t0 = hb + H + j * 4 * H
        if t0 + 4 * H > now:
            break
        blk = [(K or {}).get(t0 + i * H) for i in range(4)]
        if any(b is None for b in blk):
            continue
        mv = blk[-1][3] / blk[0][0] - 1
        if (mv >= 0.015 and cand["side"] == "LONG") or (mv <= -0.015 and cand["side"] == "SHORT"):
            if now - (t0 + 4 * H) <= 2 * H:
                return True, f"4-часовая свеча разворота {mv * 100:+.1f}%"
            return False, "свеча разворота была, но мы увидели её позже чем через 2 часа"
    return (None, "ждём 4-часовую свечу разворота") if age <= 26 * H else (False, "за сутки свечи разворота не было")


def step(st: dict | None, closed: list, now: int, v: dict, px_of=last_px, candles=hourly, fund=funding) -> dict:
    d = st or {"start": START, "cash": START, "open": [], "closed": [], "cands": [], "seen": [], "events": [], "started": now}
    seen = set(d.get("seen") or [])
    ev = d.setdefault("events", [])
    # 1. new candidates from our closed copies
    for c in closed or []:
        t = int(_f(c.get("closed_t")))
        key = f"{c.get('whale')}|{c.get('coin')}|{c.get('side')}|{t}"
        if t <= int(d["started"]) or key in seen or c.get("side") not in ("LONG", "SHORT"):
            continue
        seen.add(key)
        m = moved(c)
        if m < v["min_profit"]:
            continue
        cand = {"key": key, "coin": c["coin"], "closed_side": c["side"], "side": "SHORT" if c["side"] == "LONG" else "LONG", "t": t,
                "exit": _f(c.get("exit")), "move_pct": round(m * 100, 2), "whale": c.get("whale")}
        d["cands"].append(cand)
        ev.append({"t": now, "kind": "candidate", "coin": c["coin"], "side": cand["side"],
                   "why": f"наша сделка {c['side']} {c['coin']} закрылась с ходом {m * 100:+.1f}% — кандидат на обратную"})
    # 2. open positions: stop / time exit
    for o in list(d["open"]):
        px = px_of(o["coin"])
        if not px:
            continue
        s = 1 if o["side"] == "LONG" else -1
        r = s * (px / o["entry"] - 1)
        why = None
        if r <= -v["stop"]:
            why = f"стоп −{v['stop'] * 100:g}%"
        elif now - o["opened_t"] >= v["hold_days"] * DAY:
            why = f"выход по времени ({v['hold_days']} дн.)"
        if why:
            fr = fund(o["coin"], o["opened_t"], now) * s
            net = r - COST - fr
            pnl = round(o["notional"] * net, 2)
            d["cash"] += pnl
            d["open"].remove(o)
            d["closed"].append({**o, "exit": px, "closed_t": now, "pnl": pnl, "pnl_pct": round(net * 100, 2), "funding_pct": round(fr * 100, 3), "why": why})
            ev.append({"t": now, "kind": "close", "coin": o["coin"], "side": o["side"], "pnl": pnl, "why": why})
        else:
            o["price"], o["upnl"] = px, round(o["notional"] * r, 2)
    # 3. candidates → entries
    keep = []
    for cand in d["cands"]:
        coin = cand["coin"]
        if any(o["coin"] == coin for o in d["open"]):
            ev.append({"t": now, "kind": "skip", "coin": coin, "side": cand["side"], "why": "по этой монете уже есть бумажная позиция"})
            continue
        px = px_of(coin)
        if not px:
            if now - cand["t"] <= 26 * H:
                keep.append(cand)
            continue
        K = candles(coin, 5) if v["entry"] == "level" else candles(coin, 1) if v["entry"] == "candle" else None
        ok, why = entry_now(v, cand, now, px, K)
        if ok is None:
            keep.append(cand)
            continue
        if not ok:
            ev.append({"t": now, "kind": "skip", "coin": coin, "side": cand["side"], "why": why})
            continue
        if len(d["open"]) >= MAX_OPEN:
            ev.append({"t": now, "kind": "skip", "coin": coin, "side": cand["side"], "why": f"уже {MAX_OPEN} бумажных позиций — предел"})
            continue
        eq = d["cash"] + sum(_f(o.get("upnl")) for o in d["open"])
        s = 1 if cand["side"] == "LONG" else -1
        o = {"coin": coin, "side": cand["side"], "entry": px, "opened_t": now, "notional": round(SLOT * eq, 2),
             "stop": round(px * (1 - v["stop"] * s), 8), "exit_by": now + v["hold_days"] * DAY, "after": cand["key"],
             "after_move_pct": cand["move_pct"], "why": why, "price": px, "upnl": 0.0}
        d["open"].append(o)
        ev.append({"t": now, "kind": "open", "coin": coin, "side": cand["side"], "why": f"обратная к нашей закрытой сделке ({cand['move_pct']:+.1f}%): {why}"})
    d["cands"] = keep
    d["seen"] = sorted(seen)[-500:]
    d["events"] = ev[-200:]
    d["equity"] = round(d["cash"] + sum(_f(o.get("upnl")) for o in d["open"]), 2)
    d["t"] = now
    return d


def public(d: dict, v: dict, now: int) -> dict:
    cl = d.get("closed") or []
    wins = sum(1 for c in cl if c["pnl"] > 0)
    return {"asof": now, "name": "Разворот (тест)", "test": True, "owner_only": True, "paper": True, "real_account": False,
            "label_ru": "ТЕСТ · бумажный стол, только для владельца. На реальный счёт сделки этого стола не попадают.",
            "variant": {k: v.get(k) for k in ("entry", "min_profit", "hold_days", "stop")},
            "rule_ru": (f"Когда наша копия закрывается с ходом цены ≥ {v['min_profit'] * 100:g}% в нашу сторону, стол открывает обратную "
                        f"сторону на бумаге: {ENTRY_RU.get(v['entry'], v['entry'])}. Размер — 10% бумажного счёта без плеча, не больше "
                        f"{MAX_OPEN} позиций, одна на монету. Выход: стоп −{v['stop'] * 100:g}% или через {v['hold_days']} дн. "
                        "Издержки 0.10% на сделку и фандинг учтены."),
            "backtest": v.get("backtest"), "research": "docs/research/reversal-desk-2026-11-11.md",
            "start": d["start"], "started": d.get("started"), "equity": d.get("equity", d["start"]),
            "pnl_usd": round(d.get("equity", d["start"]) - d["start"], 2), "trades": len(cl), "wins": wins,
            "open": d.get("open") or [], "closed": sorted(cl, key=lambda c: -c["closed_t"])[:50],
            "waiting": d.get("cands") or [], "events": (d.get("events") or [])[-40:]}


def run(conn, out: str = "web", now: int | None = None, kv_get=None, kv_set=None, **kw) -> dict:
    now = int(now or time.time() * 1000)
    v = cfg()
    try:
        with open(os.path.join(out, "whales_live.json"), encoding="utf-8") as f:
            closed = (json.load(f).get("desk") or {}).get("closed") or []
    except Exception:  # noqa: BLE001
        closed = []
    st = kv_get(conn, "reversal_desk")
    if st and st.get("variant_id") != v.get("id"):             # a new registered variant → a fresh desk
        st = None
    d = step(st, closed, now, v, **kw)
    d["variant_id"] = v.get("id")
    kv_set(conn, "reversal_desk", d)
    conn.commit()
    doc = public(d, v, now)
    tmp = os.path.join(out, "reversal_desk.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out, "reversal_desk.json"))
    return doc
