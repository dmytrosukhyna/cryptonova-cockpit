"""Backlog 106 — size each whale copy by the trader's 60-day quality instead of an equal 3 % (09.11.2026, research only).

Shared by whale_cohort.py (OKX/HL, lab-data-whales) and bn_whale_cohort.py (Binance lead traders). Trades come from the same
builders as backlog 90/101 (live bar walk-forward, top-10 by 60-day P&L, copy +2 min, stop −16 %, costs 0.11 % + funding).

Pre-registered (written before any result was looked at):
 Each trade carries, from the trader's decisions closed in the prior 60 days (the same window as the live bar):
   pf = profit factor (capped at 20), sh = mean / st.dev. of the decision P&L («stability»).
 Weight of a copy w = clip(m / ref, 0.5, 1.5), where ref = the median of m over ALL earlier bar-passing decisions in the
 sample (expanding, causal; the first 20 get w = 1). Risk of the copy = 3 % × w.
 Variants: PF-weighted, stability-weighted (2) × sources (2) = 4 looks. Base = equal 3 % (the live rule).
 Book mirrors the live engine: daily stop 3 % (on weighted P&L), at most 15 open, one copy per coin, first come first served.
 Comparison at EQUAL DRAWDOWN: the variant's daily curve is scaled by k = base max-DD / variant max-DD over the whole period
 (k uses the full sample — it fixes only the scale, not which trades get more; stated). A change is suggested only if the
 scaled variant beats base with a paired daily t >= 2.5 in BOTH time halves and BOTH sources, and its scaled worst day is not
 worse than 1.25 × base. Anything else = not passed. Also reported: mean result of copies with w > 1 vs w < 1.
"""
import bisect, math, statistics as st
import daystop_sim as D

DAY = D.DAY
NOTIONAL = D.NOTIONAL


def quality(pri):
    """pri: list of (t0, t1, pnl) closed decisions of the prior 60 d. -> (pf, sh)."""
    xs = [d[2] for d in pri]
    gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    pf = min(20.0, gp / gl) if gl > 0 else 20.0
    sd = st.pstdev(xs) if len(xs) > 1 else 0.0
    sh = (st.mean(xs) / sd) if sd > 0 else 0.0
    return pf, sh


def add_weights(trades, key):
    """Causal expanding-median weights for metric `key` ('pf' or 'sh'); returns new trade dicts with field wt."""
    ev = sorted(trades, key=lambda x: x["t_in"]); seen, out = [], []
    for tr in ev:
        m = tr[key]
        if len(seen) >= 20:
            ref = st.median(seen)
            w = min(1.5, max(0.5, m / ref)) if ref > 0 else 1.0
        else:
            w = 1.0
        bisect.insort(seen, m)
        out.append(dict(tr, wt=w))
    return out


def simulate(trades, mark, X=3.0, max_pos=15):
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, day_pnl, blocked_day, taken = [], {}, {}, set(), []

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t:
                d = p["t_out"] // DAY; v = p["ret"] * NOTIONAL * p.get("wt", 1.0)
                day_pnl[d] = day_pnl.get(d, 0.0) + v; closed_today[d] = closed_today.get(d, 0.0) + v
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
                    u = s * (px / p["a"] - 1) * 100 * NOTIONAL * p.get("wt", 1.0)
                    if u < 0: unl += u
            if closed_today.get(d, 0.0) + unl <= -X: blocked_day.add(d)
        if X is not None and d in blocked_day: continue
        if len(openp) >= max_pos or any(p["coin"] == tr["coin"] for p in openp): continue
        openp.append(tr); taken.append(tr)
    close_until(float("inf"))
    return {"days": day_pnl, "taken": taken}


def report(trades, mark, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1, "mean_all": round(st.mean(t["ret"] for t in trades), 3)}
    base = simulate(trades, mark)
    bst = D.curve_stats(base["days"], d0, d1)
    R["base"] = {p: D.curve_stats(base["days"], a, b) for p, (a, b) in parts}
    R["base"]["taken"] = len(base["taken"])
    emit(f"::notice title={tag} base::taken {len(base['taken'])} | all {R['base']['all']} | h1 {R['base']['h1']['total']} | h2 {R['base']['h2']['total']}")
    for key in ("pf", "sh"):
        tw = add_weights(trades, key)
        S = simulate(tw, mark)
        raw = D.curve_stats(S["days"], d0, d1)
        k = (bst["max_dd"] / raw["max_dd"]) if raw["max_dd"] < 0 and bst["max_dd"] < 0 else 1.0
        sc = {d: v * k for d, v in S["days"].items()}
        hi = [t["ret"] for t in S["taken"] if t["wt"] > 1]; lo = [t["ret"] for t in S["taken"] if t["wt"] < 1]
        cell = {"taken": len(S["taken"]), "k": round(k, 3), "raw": raw,
                "mean_w": round(st.mean(t["wt"] for t in S["taken"]), 3) if S["taken"] else None,
                "hi_n": len(hi), "hi_mean": round(st.mean(hi), 3) if hi else None,
                "lo_n": len(lo), "lo_mean": round(st.mean(lo), 3) if lo else None}
        for p, (a, b) in parts: cell[p] = D.curve_stats(sc, a, b)
        P = {p: D.paired(sc, base["days"], a, b) for p, (a, b) in parts}
        P["risk_ok"] = bool(cell["all"]["worst_day"] >= 1.25 * R["base"]["all"]["worst_day"])
        P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["risk_ok"])
        cell["vs_base"] = P
        R[key] = cell
        emit(f"::notice title={tag} {key}::k {cell['k']} mean_w {cell['mean_w']} | eqDD all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']} "
             f"| hi {cell['hi_n']}:{cell['hi_mean']}% lo {cell['lo_n']}:{cell['lo_mean']}% | vs base {P}")
    return R
