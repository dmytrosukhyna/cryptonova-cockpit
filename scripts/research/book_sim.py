"""Backlog 101 — does the 15-slot copy book pick bad trades? Slot variants on a book of whale copies (08.11.2026, research only).

Shared by whale_book.py (OKX/HL, lab-data-whales) and bn_whale_book.py (Binance lead traders). Trades come from the same
builders as backlog 90 (whale_daystop.py / bn_whale_daystop.py): live bar walk-forward, top-10, copy +2 min, stop −16 %,
costs 0.11 % a round trip + funding.

Pre-registered (written before any result was looked at):
 Base = the live engine: risk 3 % at the 16 % stop (notional 18.75 % of the budget), daily stop 3 %, at most 15 open copies,
 one copy per coin, first come first served (a decision that finds the book full or its coin taken is skipped).
 Alternatives (3) × sources (2) = 6 looks:
  A «new first»  — when all 15 slots are full, the OLDEST open copy is closed at the market (+ costs) and the new one taken;
  B «time exit»  — every copy is closed at the market 7 days after our entry if the trader has not closed it by then;
  C «no cap»     — no slot limit (one copy per coin and the daily stop stay), same risk per copy.
 A change is suggested only if an alternative beats base on total P&L with a paired daily t >= 2.5 in BOTH time halves and
 BOTH sources, and its worst day and max drawdown are not worse than 1.25 × base. Anything else = not passed.
 Funding of a copy cut early is pro-rated by time from its full-length funding (approximation, stated in the result).
 Also reported: the «busy» share, the mean of taken vs all decisions, and the mean of skipped decisions.
"""
import math, statistics as st
import daystop_sim as D

DAY = D.DAY
NOTIONAL = D.NOTIONAL


def _cut(tr, t, mark, cost):
    """Return the copy's % result if closed at the market at time t (before its own exit)."""
    px, pe = mark(tr["coin"], t), mark(tr["coin"], tr["t_out"])
    if not px or not pe: return None
    s = 1 if tr["side"] == "LONG" else -1
    fund_full = s * (pe / tr["a"] - 1) * 100 - cost - tr["ret"]          # implied funding (+ small price-source mismatch)
    frac = (t - tr["t_in"]) / max(1, tr["t_out"] - tr["t_in"])
    return s * (px / tr["a"] - 1) * 100 - cost - fund_full * frac


def time_exit(trades, mark, cost, days=7):
    out = []
    for tr in trades:
        lim = tr["t_in"] + days * DAY
        if tr["t_out"] > lim:
            r = _cut(tr, lim, mark, cost)
            if r is not None and math.isfinite(r):
                tr = dict(tr, t_out=lim, ret=r)
        out.append(tr)
    return out


def simulate(trades, mark, cost, max_pos=15, evict=False, X=3.0):
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, day_pnl = [], {}, {}
    blocked_day, taken_ret, skip_ret, busy, evicted = set(), [], [], 0, 0

    def book(d, v):
        day_pnl[d] = day_pnl.get(d, 0.0) + v; closed_today[d] = closed_today.get(d, 0.0) + v

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t: book(p["t_out"] // DAY, p["ret"] * NOTIONAL)
            else: keep.append(p)
        openp = keep

    for tr in ev:
        t = tr["t_in"]; d = t // DAY
        close_until(t)
        if X is not None and d not in blocked_day:
            unl = 0.0
            for p in openp:
                px = mark(p["coin"], t)
                if px:
                    s = 1 if p["side"] == "LONG" else -1
                    u = s * (px / p["a"] - 1) * 100 * NOTIONAL
                    if u < 0: unl += u
            if closed_today.get(d, 0.0) + unl <= -X: blocked_day.add(d)
        if X is not None and d in blocked_day: continue
        if any(p["coin"] == tr["coin"] for p in openp):
            busy += 1; skip_ret.append(tr["ret"]); continue
        if max_pos is not None and len(openp) >= max_pos:
            if not evict:
                busy += 1; skip_ret.append(tr["ret"]); continue
            old = min(openp, key=lambda p: p["t_in"])
            r = _cut(old, t, mark, cost)
            if r is None:
                busy += 1; skip_ret.append(tr["ret"]); continue
            openp.remove(old); book(d, r * NOTIONAL); evicted += 1
        openp.append(tr); taken_ret.append(tr["ret"])
    close_until(float("inf"))
    return {"days": day_pnl, "taken": len(taken_ret), "busy": busy, "evicted": evicted,
            "taken_mean": round(st.mean(taken_ret), 3) if taken_ret else None,
            "skip_mean": round(st.mean(skip_ret), 3) if skip_ret else None}


def report(trades, mark, cost, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "mean_all": round(st.mean(t["ret"] for t in trades), 3), "days": d1 - d0 + 1}
    sims = {"base": simulate(trades, mark, cost),
            "A_new_first": simulate(trades, mark, cost, evict=True),
            "B_time7d": simulate(time_exit(trades, mark, cost), mark, cost),
            "C_no_cap": simulate(trades, mark, cost, max_pos=None)}
    for k, S in sims.items():
        cell = {x: S[x] for x in ("taken", "busy", "evicted", "taken_mean", "skip_mean")}
        for p, (a, b) in parts: cell[p] = D.curve_stats(S["days"], a, b)
        R[k] = cell
        emit(f"::notice title={tag} {k}::taken {cell['taken']} mean {cell['taken_mean']}% (all {R['mean_all']}%) busy {cell['busy']} "
             f"skip-mean {cell['skip_mean']}% evicted {cell['evicted']} | all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}")
    for alt in ("A_new_first", "B_time7d", "C_no_cap"):
        P = {p: D.paired(sims[alt]["days"], sims["base"]["days"], a, b) for p, (a, b) in parts}
        b0, b1 = R["base"]["all"], R[alt]["all"]
        P["risk_ok"] = (b1["worst_day"] >= 1.25 * b0["worst_day"]) and (b1["max_dd"] >= 1.25 * b0["max_dd"])
        P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["all"]["d"] > 0 and P["risk_ok"])
        R[alt + "_minus_base"] = P
        emit(f"::notice title={tag} {alt}−base::{P}")
    return R
