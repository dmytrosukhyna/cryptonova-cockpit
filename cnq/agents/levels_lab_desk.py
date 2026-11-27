"""«Уровни · лаборатория» paper desks (owner 27.11.2026 08:59 / 09:00). PAPER ONLY, owner-only, TEST — never routed to the real
account (no copy-signal id). Each desk = one variant of scripts/research/levels_lab_study.py (the SAME code: events, fills, stops,
targets, management are imported from the study file, so the desk trades exactly what was tested) on OKX public hourly candles.

Stateless replay: every pass rebuilds the variant's trades from the candles of the last ~50 days; only events AFTER the desk's start
count (no replay of history); a trade whose exit is not reached yet is open; closed trades are kept by key. Sizing: risk % of the
$10 000 start at the trade's stop, notional ≤ 1× the start; ≤ 5 open per desk (later events skipped while 5 are open).
Desks come from config/levels_lab_desks.json — «observation» desks are labelled unproven («наблюдение — не доказано»)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys

from . import levels_desk as LD

H = LD.H
START = 10_000.0
MAX_OPEN = 5
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CFG = os.path.join(ROOT, "config", "levels_lab_desks.json")
_M = None


def lab():
    """the study module (scripts/research/levels_lab_study.py), loaded once"""
    global _M
    if _M is None:
        d = os.path.join(ROOT, "scripts", "research")
        if d not in sys.path:
            sys.path.insert(0, d)
        spec = importlib.util.spec_from_file_location("levels_lab_study", os.path.join(d, "levels_lab_study.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _M = m
    return _M


def cfg(path=None) -> list:
    try:
        with open(path or CFG, encoding="utf-8") as f:
            return [x for x in (json.load(f).get("desks") or []) if isinstance(x, dict) and x.get("key") and x.get("variant")]
    except (OSError, ValueError):
        return []


def prep(K):
    import numpy as np
    M = lab()
    P = M.V2.prep(K)
    P["_L"] = [LD.levels_at(P, i) if i >= 200 else [] for i in range(len(P["c"]))]
    P["_swl"], P["_swh"] = M.swing_lows_highs(P)
    P["_tarr"] = np.asarray(P["t"], dtype=np.int64)
    return P


def trades(P, variant: str, raw15, since: int) -> list:
    """[{key, coin?, side, t, fill_t, entry, stop, tgt, pct?, why?, open}] for events at or after `since`"""
    import numpy as np
    M = lab()
    stream, side, ent, sn, tn, mg = variant.split("|")
    o, h, l, c = (np.asarray(P[k]) for k in ("o", "h", "l", "c"))
    n = len(c)
    out = []
    horizon = {"t12": 12, "t24": 24, "t48": 48}.get(mg, 72)
    for ev in M.events(P, raw15)[stream]:
        if ev["side"] != side or P["t"][ev["i"]] < since:
            continue
        long = side == "L"
        for en, fi, p, so, ext in M.fills(P, ev, stream, raw15):
            if en != ent or fi >= n:
                continue
            stp = M.stop_of(sn, long, P, fi, ev, ext)
            if stp is None:
                continue
            tg = M.tgt_of(tn, long, p, stp, ev)
            if tg is None:
                continue
            seg = slice(fi, min(n, fi + 72))
            r = M.sim(long, p, stp, tg, ev["A"], o[seg], h[seg], l[seg], c[seg], mg, so)
            if r is None:
                continue
            avail = n - fi
            is_open = r[1] == "время" and avail < horizon
            out.append({"key": f"{P['t'][ev['i']]}|{side}", "side": side, "t": int(P["t"][ev["i"]]), "fill_t": int(P["t"][fi]),
                        "entry": float(p), "stop": float(stp), "tgt": float(tg), "open": bool(is_open),
                        **({} if is_open else {"pct": round(float(r[0]), 3), "why": r[1], "exit_t": int(P["t"][min(n - 1, fi + r[2])]) + H})})
    return out


def step(st: dict | None, desk: dict, now: int, candles, c15=None) -> dict:
    """one pass of one desk; candles(coin) → OKX hourly [t, o, h, l, c, v]; c15(coin) → 15m rows (only for E_15m desks)"""
    import numpy as np
    st = st or {"key": desk["key"], "started": now, "closed": {}, "open": [], "errors": []}
    st.setdefault("closed", {})
    risk = float(desk.get("risk") or 0.01)
    opened = []
    errs = []
    for coin in desk.get("coins") or LD.COINS:
        try:
            K = [k for k in candles(coin) if k[0] + H <= now]
            if len(K) < 400:
                continue
            P = prep(K)
            if "E_15m" in desk["variant"] and c15:
                r15 = c15(coin)
                raw15 = tuple(np.asarray([x[j] for x in r15], dtype=(np.int64 if j == 0 else float)) for j in range(5))
            else:
                raw15 = (np.zeros(0, dtype=np.int64),) + tuple(np.zeros(0) for _ in range(4))
            for tr in trades(P, desk["variant"], raw15, int(st["started"])):
                k = f"{coin}|{tr['key']}"
                if tr["open"]:
                    opened.append({**tr, "coin": coin, "key": k})
                elif k not in st["closed"]:
                    sp = abs(tr["entry"] - tr["stop"]) / tr["entry"]
                    notional = min(risk * START / max(sp, 1e-4), START)
                    st["closed"][k] = {**tr, "coin": coin, "key": k, "usd": round(notional * tr["pct"] / 100, 2), "risk": risk}
        except Exception as e:  # noqa: BLE001
            errs.append(f"{coin}: {type(e).__name__}")
    opened.sort(key=lambda x: x["fill_t"])
    st["open"] = opened[:MAX_OPEN]
    st["skipped_open"] = max(0, len(opened) - MAX_OPEN)
    cl = list(st["closed"].values())
    st["equity"] = round(START + sum(x["usd"] for x in cl), 2)
    st["trades"] = len(cl)
    st["wins"] = sum(1 for x in cl if x["usd"] > 0)
    st["errors"] = errs[:5]
    st["t"] = now
    return st
