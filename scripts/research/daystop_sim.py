"""Portfolio simulator for backlog 90 — does the 3 % daily stop on whale copies cut more than it saves? (07.11.2026, research only)

Shared by whale_daystop.py (OKX/HL, lab-data-whales) and bn_whale_daystop.py (Binance lead traders).

Pre-registered (written before any result was looked at):
 Book: copies of decisions of whales that pass the LIVE bar at that moment (prior 60 d: win >= 70 %, PF >= 2, >= 20 closed, P&L > 0).
 Copy: enter trader open + 2 min, exit trader close + 2 min, protective stop −16 % from our entry, costs 0.11 % a round trip + funding.
 Size: risk 3 % of the futures budget at the 16 % stop → notional 18.75 % of the budget per copy; fixed budget (no compounding).
 Engine rules mirrored: at most 15 open copies, one copy per coin (a second one is skipped as «busy»).
 Daily stop (UTC day), as okx_futures.step: before each new entry, day P&L = realised P&L of copies closed today + the open
 copies' unrealised LOSSES; if <= −X % of the budget, no new entries until the next UTC day; open copies keep running.
 Variants: X = 3 % (live), 5 %, none → 2 alternatives × 2 sources = 4 looks.
 A change is suggested only if an alternative beats 3 % on total P&L with a paired daily t >= 2.5, in BOTH time halves and
 BOTH sources (where a source has >= 60 days), and its worst day and max drawdown are not worse than 1.25 × those of 3 %.
 Reported: total % of budget, days, worst day, max drawdown of the daily curve, longest losing-day streak, days the stop fired,
 entries blocked and the blocked copies' own result (what the stop «saved» or «cut»).
"""
import bisect, math, statistics as st

DAY = 86_400_000
NOTIONAL = 0.03 / 0.16          # share of the budget per copy
MAX_POS = 15


def simulate(trades, X, mark):
    """trades: dicts with t_in, t_out (effective, after the stop), ret (% after costs), coin, side, a (entry price).
    mark(coin, t) -> price or None. X: daily stop in % of the budget, or None. Returns per-day P&L (% budget) and stats."""
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, day_pnl = [], {}, {}
    blocked_day, n_blocked, blocked_ret, taken, busy, taken_ret = set(), 0, [], 0, 0, []

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t:
                d = p["t_out"] // DAY
                v = p["ret"] * NOTIONAL
                day_pnl[d] = day_pnl.get(d, 0.0) + v
                closed_today[d] = closed_today.get(d, 0.0) + v
            else:
                keep.append(p)
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
            if closed_today.get(d, 0.0) + unl <= -X:
                blocked_day.add(d)
        if X is not None and d in blocked_day:
            n_blocked += 1; blocked_ret.append(tr["ret"]); continue
        if len(openp) >= MAX_POS or any(p["coin"] == tr["coin"] for p in openp):
            busy += 1; continue
        openp.append(tr); taken += 1; taken_ret.append(tr["ret"])
    close_until(float("inf"))
    return {"days": day_pnl, "taken": taken, "busy": busy, "blocked": n_blocked, "stop_days": len(blocked_day),
            "blocked_ret": blocked_ret, "taken_ret": taken_ret}


def top_n(trades, status, n=10):
    """status: whale -> sorted [(t, bar, pnl60)] at its decisions. Keep a trade only if its whale is among the n best by 60-day
    P&L of whales passing the bar at that moment (each whale's latest status within 30 days)."""
    keys = {w: [x[0] for x in v] for w, v in status.items()}
    out = []
    for tr in trades:
        t = tr["t_in"]; ok = []
        for w, v in status.items():
            i = bisect.bisect_right(keys[w], t) - 1
            if i >= 0 and t - v[i][0] <= 30 * DAY and v[i][1]: ok.append((v[i][2], w))
        ok.sort(reverse=True)
        if tr["w"] in {w for _, w in ok[:n]}: out.append(tr)
    return out


def curve_stats(days, d0, d1):
    xs = [days.get(d, 0.0) for d in range(d0, d1 + 1)]
    eq = pk = dd = 0.0; streak = best = 0
    for x in xs:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
        streak = streak + 1 if x < 0 else 0; best = max(best, streak)
    return {"total": round(sum(xs), 2), "n_days": len(xs), "worst_day": round(min(xs), 2) if xs else None,
            "max_dd": round(dd, 2), "lose_streak": best}


def paired(a, b, d0, d1):
    xs = [a.get(d, 0.0) - b.get(d, 0.0) for d in range(d0, d1 + 1)]
    xs = [x for x in xs if x != 0.0] or [0.0]
    if len(xs) < 2: return {"d": round(sum(xs), 2), "n": len(xs), "t": None}
    sd = st.pstdev(xs)
    return {"d": round(sum(xs), 2), "n": len(xs), "t": round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None}


def report(trades, mark, tag, emit=print):
    if not trades:
        return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY
    mid = (d0 + d1) // 2
    R = {"n_trades": len(trades), "from_day": d0, "to_day": d1, "mean_ret_all": round(st.mean(t["ret"] for t in trades), 3)}
    sims = {k: simulate(trades, X, mark) for k, X in (("x3", 3.0), ("x5", 5.0), ("none", None))}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "taken_mean_ret": round(st.mean(S["taken_ret"]), 3) if S["taken_ret"] else None, "busy": S["busy"], "blocked": S["blocked"], "stop_days": S["stop_days"],
                "blocked_mean_ret": round(st.mean(S["blocked_ret"]), 3) if S["blocked_ret"] else None,
                "blocked_sum_budget": round(sum(S["blocked_ret"]) * NOTIONAL, 2)}
        for part, (a, b) in (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1))):
            cell[part] = {k2: (float(v) if v is not None else None) for k2, v in curve_stats(S["days"], a, b).items()}
        R[k] = cell
        emit(f"::notice title={tag} {k}::taken {S['taken']} (mean {cell['taken_mean_ret']}% vs all {R['mean_ret_all']}%) busy {S['busy']} blocked {S['blocked']} on {S['stop_days']} days "
             f"(their mean {cell['blocked_mean_ret']}%) | all {cell['all']} | h1 {cell['h1']['total']} dd {cell['h1']['max_dd']} "
             f"| h2 {cell['h2']['total']} dd {cell['h2']['max_dd']}")
    for alt in ("x5", "none"):
        P = {part: paired(sims[alt]["days"], sims["x3"]["days"], a, b)
             for part, (a, b) in (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))}
        R[f"{alt}_minus_x3"] = P
        emit(f"::notice title={tag} {alt}−x3::{P}")
    return R
