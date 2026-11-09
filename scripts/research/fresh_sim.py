"""Backlog 116 — «whale in form / out of form»: the trader's last-14-day record at the moment of the copy (09.11.2026, research only).

Shared by whale_fresh.py (OKX/HL, lab-data-whales) and bn_whale_fresh.py (Binance). Trades are built exactly as in backlog 106
(whale_cohort.py / bn_whale_cohort.py): live bar walk-forward (prior 60 d: win >= 70 %, PF >= 2, >= 20 decisions, P&L > 0),
primary pool top-10 by 60-day P&L, copy +2 min, stop -16 %, costs 0.11 % + funding. Book = live engine (daily stop 3 %,
max 15 open, one copy per coin) via cohort_sim.simulate.

Pre-registered (written before any result was looked at):
 For each copy, from the trader's decisions CLOSED in the 14 days before the copy: n14, win14, pf14.
 ONE boundary: «out of form» = n14 >= 5 and win14 < 0.60 (the trader still passes the 60-day bar).
 Variant = skip out-of-form copies; base = copy everything (the live rule).
 Passes only if, in BOTH sources (primary pool top-10): paired daily t (variant − base) >= 2.5 in BOTH time halves,
 the variant's worst day is not worse than the base's, and out-of-form copies have a lower mean result than the rest in
 both halves. Anything else = not passed. Reported only (not decisive): pf14 < 1 split, and the «all bar-passing» pool.
 Looks: 1 boundary × 2 sources (+ 2 extra pools).
"""
import math, statistics as st
import daystop_sim as D
import cohort_sim as K

DAY = D.DAY


def fresh(done, t0):
    """done: list of (open_t, close_t, pnl) earlier decisions -> (n14, win14, pf14) over decisions closed in [t0-14d, t0)."""
    xs = [d[2] for d in done if t0 - 14 * DAY <= d[1] < t0]
    if not xs: return 0, None, None
    gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return len(xs), sum(1 for x in xs if x > 0) / len(xs), (min(20.0, gp / gl) if gl > 0 else 20.0)


def is_out(tr):
    return tr["n14"] >= 5 and tr["win14"] is not None and tr["win14"] < 0.60


def _m(xs):
    return round(st.mean(xs), 3) if xs else None


def _welch(a, b):
    if len(a) < 3 or len(b) < 3: return None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb > 0 else None


def report(trades, mark, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1}
    ws = [t["win14"] for t in trades if t["n14"] >= 5 and t["win14"] is not None]
    R["form"] = {"n14ge5": len(ws), "out_all": sum(1 for t in trades if is_out(t)),
                 "win14_q": [round(q, 3) for q in st.quantiles(ws, n=10)] if len(ws) >= 10 else None,
                 "win14_lt70": sum(1 for w in ws if w < 0.70), "out_all_mean": _m([t["ret"] for t in trades if is_out(t)])}
    base = K.simulate(trades, mark)
    var = K.simulate([t for t in trades if not is_out(t)], mark)
    R["base"] = {p: D.curve_stats(base["days"], a, b) for p, (a, b) in parts}
    R["skip"] = {p: D.curve_stats(var["days"], a, b) for p, (a, b) in parts}
    R["base"]["taken"], R["skip"]["taken"] = len(base["taken"]), len(var["taken"])
    P = {p: D.paired(var["days"], base["days"], a, b) for p, (a, b) in parts}
    # copies the base book actually took, split by form (per half by entry day)
    split = {}
    for p, (a, b) in parts:
        tk = [t for t in base["taken"] if a <= t["t_in"] // DAY <= b]
        o = [t["ret"] for t in tk if is_out(t)]; r = [t["ret"] for t in tk if not is_out(t)]
        pl = [t["ret"] for t in tk if t["n14"] >= 5 and t["pf14"] is not None and t["pf14"] < 1]
        split[p] = {"out_n": len(o), "out_mean": _m(o), "rest_n": len(r), "rest_mean": _m(r), "welch_t": _welch(o, r),
                    "pf14lt1_n": len(pl), "pf14lt1_mean": _m(pl)}
    R["split"] = split
    worse = all(split[p]["out_mean"] is not None and split[p]["rest_mean"] is not None and split[p]["out_mean"] < split[p]["rest_mean"] for p in ("h1", "h2"))
    P["risk_ok"] = bool(R["skip"]["all"]["worst_day"] >= R["base"]["all"]["worst_day"])
    P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["risk_ok"] and worse)
    R["vs_base"] = P
    emit(f"::notice title={tag} fresh::n {len(trades)} form {R['form']} | base {R['base']['all']} h1 {R['base']['h1']['total']} h2 {R['base']['h2']['total']} "
         f"| skip {R['skip']['all']} h1 {R['skip']['h1']['total']} h2 {R['skip']['h2']['total']} | split {split} | vs {P}")
    return R
