"""«Уровни · шире стоп» (owner 27.11.2026 09:38: «стопы выбивает, а потом цена идёт к цели — попробуй стопы шире»). PAPER ONLY,
owner-only, TEST — never routed to the real account (no copy-signal id).
The EXACT live desk (cnq/agents/levels_desk.py, its variant from config/levels_desk.json): the same levels, the same signals, the
same target R, the same sizing (1 % risk, one position ≤ 20 % of the equity, ≤ 5 positions, one per coin) — only the stop differs:
  a1.5 / a2 / a3   S − k × ATR(H1, 14) at the signal bar (the desk: S − 0.5 ATR)
  swing            the last confirmed H1 swing low (fractal 3, known 3 bars later) below the entry − 0.1 ATR
  swing24          as swing, and the trade is closed after 24 H1 bars (the desk: 72)
A wider stop at the same 1 % risk = a smaller position. Shorts are the mirror (swing highs above)."""
from __future__ import annotations

from . import levels_desk as LD

H = LD.H
MODES = ("a1.5", "a2", "a3", "swing", "swing24")


def swings(P):
    if "_wsw" not in P:
        hi, lo = LD.fractals(P["h"], P["l"], 3)
        P["_wsw"] = (sorted((j + 3, p) for j, p in lo), sorted((j + 3, p) for j, p in hi))
    return P["_wsw"]


def wide(P, i, sg, side, mode, entry):
    """the signal with the mode's stop (None when no stop can be placed); also the max bars"""
    a = P["atr"][i]
    if not a:
        return None, None
    long = side == "L"
    lvl = sg.get("lvl") or entry
    if mode.startswith("a"):
        k = float(mode[1:])
        stop = lvl - k * a if long else lvl + k * a
    else:
        lows, highs = swings(P)
        src = lows if long else highs
        cand = [p for kk, p in src if kk <= i and ((p < entry) if long else (p > entry))]
        if not cand:
            return None, None
        p = cand[-1]
        stop = p - 0.1 * a if long else p + 0.1 * a
    if (long and stop >= entry) or (not long and stop <= entry):
        return None, None
    return {**sg, "stop": stop}, (24 if mode == "swing24" else None)


def step(st, now, v, mode, candles=LD.hourly, coins=LD.COINS):
    """levels_desk.step with the mode's stop (same flow: open positions on the new closed bars, then signals on the last bar)"""
    md, side, ex = v.split("|")
    st = st or {"start": LD.START, "cash": LD.START, "open": [], "closed": [], "started": now, "last_bar": {}, "pending": [], "mode": mode}
    for k in ("open", "closed", "pending"):
        st.setdefault(k, [])
    st.setdefault("last_bar", {})
    errs = []
    for coin in coins:
        try:
            K = candles(coin)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{coin}: {type(e).__name__}")
            continue
        K = [k for k in K if k[0] + H <= now]
        if len(K) < 300:
            continue
        lb = int(st["last_bar"].get(coin) or 0)
        new = [i for i, k in enumerate(K) if k[0] > lb]
        if not new:
            continue
        t, o, h, l, c, vv = (list(x) for x in zip(*K))
        P = LD.prep(t, o, h, l, c, vv)
        for i in new:
            bar = (o[i], h[i], l[i], c[i])
            for tr in [x for x in st["open"] if x["coin"] == coin]:
                r = LD.step_trade(tr, bar, P["atr"][i])
                if r:
                    px, why = r
                    pct = LD.ret_pct(tr["side"], tr["entry"], px, tr["bars"])
                    usd = round(tr["notional"] * pct / 100, 2)
                    st["cash"] += usd
                    st["open"].remove(tr)
                    st["closed"].append({**tr, "exit": px, "closed_t": t[i] + H, "pct": round(pct, 3), "usd": usd, "why": why})
        st["last_bar"][coin] = t[new[-1]]
        i = new[-1]
        if lb == 0 or any(x["coin"] == coin for x in st["open"]) or len(st["open"]) >= LD.MAX_OPEN:
            continue                                           # the first read of a coin opens nothing (no replay)
        L, Lp = LD.levels_at(P, i), LD.levels_at(P, i - 1)
        for sg in LD.signals(P, i, Lp, L, md, side):
            if sg["kind"] != "market":
                break
            sg2, maxb = wide(P, i, sg, side, mode, c[i])
            if sg2:
                _open(st, coin, side, ex, sg2, c[i], t[i] + H, maxb)
            break
    st["equity"] = round(st["cash"], 2)
    st["errors"] = errs[:5]
    st["t"] = now
    st["closed"] = st["closed"][-500:]
    return st


def _open(st, coin, side, ex, sg, entry, t, maxb):
    p = LD.plan(sg, side, ex, entry)
    if not p or len(st["open"]) >= LD.MAX_OPEN:
        return
    eq = st["cash"]
    want = LD.RISK * eq / (p["risk"] / entry)
    notional = min(want, LD.POS_CAP * eq)
    st["open"].append({"coin": coin, "side": side, "entry": entry, "opened_t": t, "stop": p["stop"], "stop0": p["stop"], "tgt": p["tgt"],
                       "maxb": maxb or p["maxb"], "trail": p["trail"], "risk": p["risk"], "bars": 0, "notional": round(notional, 2),
                       "risk_usd": round(notional * p["risk"] / entry, 2), "capped": notional < want - 1e-9, "level": sg.get("lvl")})
