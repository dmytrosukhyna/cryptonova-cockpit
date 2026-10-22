"""The header of the owner's closed system (cryptonova.vip, web/app.html) on the server — the app's PUBLIC portfolio.

Owner 22.10 (build 44): the app's public portfolio is exactly «наша система CRYPTO NOVA» as the panel's header shows it:
  FUTURES · CRYPTO NOVA («отобранные сигналы»)  — novaRes(): the desks of the mix (bal + imp; the own daily signals join once
                                                   their live record passes the gate) as ONE $10 000 account, each from 17.10;
  SPOT · CRYPTO NOVA портфель                    — spotEq(portfolio.json → portfolios.ideas);
  «Заработано всего»                             — hdrVals().tot = futures result + spot result (hdrExtras() is empty since 19.10).

Every function here is the panel's own JavaScript, line for line (the names in brackets), so the app shows the same numbers as
the owner's screen — tests/test_public_home.py runs the panel's real functions in node on the same data and compares.
`px` stands for the panel's live prices (pxNow: the last 15-minute close it holds for «XXXUSDT»). The server passes none: the
panel's own fallbacks then apply — a futures trade at its desk's last price (o.price), a spot coin at the portfolio's price
(no correction). Stdlib only."""
from __future__ import annotations

import datetime as dt

START = 10000.0
# const FUT_D0=Date.UTC(2026,9,16,21,0,0) — 17.10 00:00 Kyiv, the CRYPTO NOVA futures start
FUT_D0 = int(dt.datetime(2026, 10, 16, 21, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
NOVA_BASE = ("bal", "imp")
OWN_GATE_N, OWN_GATE_AVG = 30, 0.3


def _num(x, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def px_now(px: dict | None, sym) -> float | None:
    """[pxNow] the panel's live price for a coin: «BTC» and «BTCUSDT» are the same key; None = the panel has none"""
    if not px or not sym:
        return None
    s = str(sym).upper()
    s = s if s.endswith("USDT") else s + "USDT"
    v = px.get(s)
    return float(v) if v else None


def fut_eq(v: dict | None, px: dict | None = None) -> float:
    """[futEq] a futures desk: equity + Σ open (side × (price − entry) × qty_left + realised part), price = live or the trade's"""
    if not v:
        return START
    f = _num(v.get("equity"), START)
    for o in v.get("open") or []:
        entry = _num(o.get("entry"))
        p = px_now(px, o.get("symbol")) or o.get("price") or entry     # pxNow(o.symbol)||o.price
        sgn = 1.0 if o.get("side") == "LONG" else -1.0
        q = o.get("qty_left")
        q = o.get("qty") if q is None else q                           # (a desk always stores qty_left; old test rows only qty)
        f += sgn * (_num(p) - entry) * _num(q) + _num(o.get("pnl_usd"))
    return f


def base_d0(v: dict) -> float:
    """[baseD0] the desk's equity at the CRYPTO NOVA start (17.10), or its own start when it began later"""
    h = v.get("equity_history") or []
    st = _num(v.get("start_equity"), 0.0) or START
    if not h or _num(h[0].get("time")) >= FUT_D0:
        return st
    b = h[0].get("equity")
    for e in h:
        if _num(e.get("time")) <= FUT_D0:
            b = e.get("equity")
        else:
            break
    return _num(b, 0.0) or st


def since_d0(v: dict) -> int:
    """[sinceD0] when the desk's part of the result starts (ms): 17.10, or its first record when it began later"""
    h = v.get("equity_history") or []
    return int(max(FUT_D0, _num(h[0].get("time")))) if h else FUT_D0


def nova_keys(prof: dict) -> list[str]:
    """the desks inside [novaRes]: bal + imp, and the own daily signals once ≥ 30 closed trades at ≥ +0.3 % a trade"""
    s = (prof.get("own") or {}).get("stats") or {}
    avg = s.get("avg_trade_pct")
    ok = int(_num(s.get("trades"))) >= OWN_GATE_N and isinstance(avg, (int, float)) and avg >= OWN_GATE_AVG
    return [k for k in (*NOVA_BASE, *(("own",) if ok else ())) if prof.get(k)]


def nova_res(prof: dict, px: dict | None = None) -> dict | None:
    """[novaRes] CRYPTO NOVA futures as ONE $10 000 account: Σ (desk now − desk at 17.10) ÷ Σ desk starts × $10 000"""
    comps, t0 = [], None
    for k in nova_keys(prof):
        v = prof[k]
        h = v.get("equity_history") or []
        t = h[0].get("time") if h else None
        if t and (t0 is None or t < t0):
            t0 = t
        comps.append({"k": k, "r": fut_eq(v, px) - base_d0(v), "base": _num(v.get("start_equity"), 0.0) or START,
                      "since": since_d0(v)})
    if not comps:
        return None
    sb = sum(c["base"] for c in comps) or 1.0
    return {"usd": sum(c["r"] for c in comps) / sb * START, "base": START,
            "since": int(max(FUT_D0, _num(t0, FUT_D0) if t0 else FUT_D0)), "comps": comps}


def spot_eq(s: dict | None, px: dict | None = None) -> float:
    """[spotEq] a spot portfolio at live prices: start × index ÷ 100 × (1 + Σ weight × (live ÷ its price − 1))"""
    if not s:
        return START
    k = 1.0
    for p in s.get("positions") or []:
        live = px_now(px, p.get("symbol"))
        if live and p.get("price"):
            k += _num(p.get("weight")) * (live / float(p["price"]) - 1)
    return (_num(s.get("start_usd"), 0.0) or START) * _num(s.get("index")) / 100.0 * k


def spot_since(s: dict) -> int | None:
    """[sSince] the spot portfolio's start (ms): started, or its reset, or its first history point"""
    for x in (s.get("started"), (s.get("reset") or {}).get("time"), ((s.get("history") or [{}])[0] or {}).get("time")):
        if x:
            return int(x)
    return None


def header(prof: dict, ideas: dict | None, px: dict | None = None) -> dict | None:
    """[hdrVals], the panel's default (no account picked by hand): futures = CRYPTO NOVA, spot = CRYPTO NOVA портфель.
    f / sp — the accounts now; f0 / s0 — their starts; fr / sr — the results; tot — «Заработано всего» = fr + sr.
    None when neither part exists; a missing part is None and adds nothing (the panel would show another account there)."""
    n = nova_res(prof or {}, px)
    s = ideas if isinstance(ideas, dict) and ideas.get("index") is not None else None
    if n is None and s is None:
        return None
    out: dict = {"N": n, "fut": None, "spot": None}
    tot = 0.0
    if n is not None:
        f, f0 = n["base"] + n["usd"], n["base"]
        out["fut"] = {"f": f, "f0": f0, "fr": f - f0, "since": n["since"], "keys": [c["k"] for c in n["comps"]]}
        tot += f - f0
    if s is not None:
        sp, s0 = spot_eq(s, px), _num(s.get("start_usd"), 0.0) or START
        out["spot"] = {"sp": sp, "s0": s0, "sr": sp - s0, "since": spot_since(s)}
        tot += sp - s0
    out["tot"] = tot
    return out
