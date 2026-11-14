"""Backlog 165 (14.11.2026, research only): the rotation's FAST EXIT — «his copies lost more than 10 % of his share → he leaves A
and his copies are closed at once» — against «he leaves A, but his open copies run to his own exit or our −16 % stop».

Pre-registered (written before any result of THIS comparison was looked at; note: backlog 117 already reported, on the same data,
that the copies cut by the rule averaged −4.2 % (OKX/HL) / −3.7 % (Binance) at the cut against −3.2 % / −2.4 % at their own exit —
so the per-copy direction is known; what is new here is the whole book, both halves and the worst case):
 Trades, pools and book exactly as backlog 117 (lock_sim.py): live 60-day bar walk-forward, primary pool top-10 by 60-day P&L
 (check: all passing the bar), copy +2 min, stop −16 %, costs 0.11 % + funding, risk 3 % at the stop, daily stop 3 %, ≤ 15 open,
 one copy per coin; share = budget / 7, threshold −10 % of the share, checked hourly and at every entry; reviews every 3 days.
  base «cut»  (live rotation-v1) — at the threshold his open copies are closed at the market, new decisions not copied until review.
  H «hold»    — the same, except his open copies are NOT closed: they run to his exit (+2 min) or our −16 % stop.
 One variant × 2 sources. H is a candidate (money only with Dmytro's yes) only if, in the PRIMARY pool of BOTH sources:
  H − cut > 0 in total and in BOTH halves of time, the paired daily t over the whole period ≥ 2.0, H's max drawdown and worst
  day not worse than 1.25 × cut, and the worst held copy not worse than the −16 % stop after costs (−16.5 %).
 Reported, not decisive: per cut copy — cut vs own exit, by halves, the worst and the share that recovered above the cut.
"""
import math, statistics as st
import daystop_sim as D
import lock_sim as K

DAY = D.DAY
NOTIONAL = D.NOTIONAL


def _m(xs):
    return round(st.mean(xs), 3) if xs else None


def report(trades, mark, cost, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1, "whales": len({t["w"] for t in trades})}
    sims = {"cut": K.simulate(trades, mark, cost, "live"), "hold": K.simulate(trades, mark, cost, "hold")}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "busy": S["busy"], "drops": S["drops"], "copies_at_threshold": S["cut_n"],
                "skipped_out_n": len(S["skipped_out"]), "skipped_out_mean": _m(S["skipped_out"])}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        R[k] = cell
        emit(f"::notice title={tag} {k}::taken {cell['taken']} drops {cell['drops']} at-threshold {cell['copies_at_threshold']} "
             f"| all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}")
    S = sims["cut"]; cr, orr, ct = S["cut_ret"], S["own_ret"], S.get("cut_t", [])
    per = {}
    for p, (a, b) in parts:
        ix = [i for i, t in enumerate(ct) if a <= t // DAY <= b]
        c = [cr[i] for i in ix]; o = [orr[i] for i in ix]; dlt = [o_ - c_ for c_, o_ in zip(c, o)]
        tt = None
        if len(dlt) >= 3 and st.pstdev(dlt) > 0: tt = round(st.mean(dlt) / (st.pstdev(dlt) / math.sqrt(len(dlt))), 2)
        per[p] = {"n": len(ix), "cut_mean": _m(c), "own_mean": _m(o), "hold_minus_cut": _m(dlt), "t": tt,
                  "worst_cut": round(min(c), 2) if c else None, "worst_own": round(min(o), 2) if o else None,
                  "own_better_share": round(sum(d > 0 for d in dlt) / len(dlt), 3) if dlt else None,
                  "own_stopped": sum(1 for x in o if x <= -16.0)}
    R["per_copy"] = per
    emit(f"::notice title={tag} per cut copy::{per}")
    P = {p: D.paired(sims["hold"]["days"], sims["cut"]["days"], a, b) for p, (a, b) in parts}
    b0, b1 = R["cut"]["all"], R["hold"]["all"]
    P["risk_ok"] = bool(b1["worst_day"] >= 1.25 * b0["worst_day"] and b1["max_dd"] >= 1.25 * b0["max_dd"])
    P["worst_ok"] = bool(per["all"]["worst_own"] is None or per["all"]["worst_own"] >= -16.5)
    P["pass_here"] = bool(P["all"]["d"] > 0 and P["h1"]["d"] > 0 and P["h2"]["d"] > 0 and P["all"]["t"] is not None
                          and P["all"]["t"] >= 2.0 and P["risk_ok"] and P["worst_ok"])
    R["hold_minus_cut"] = P
    emit(f"::notice title={tag} hold−cut::{P}")
    return R
