"""Backlog 172 (16.11.2026, 03:15 Athens, research only): RETURN to column A after a FAST EXIT.

Case: 14.11 建斌论坛 left A at 15:50 UTC by the fast exit (his copies lost more than 10 % of his share) and came back at 15:58 by
«3 full days in a row in plus» — days 11–13.11, all BEFORE the exit; after the return 2 copies −$10.69.
Live rule (cnq/agents/whale_study.py rotate → day_run(q, now, 1) without `since`): the last ROT_DAYS = 3 full UTC days of his own
closed decisions each net > 0 → he may come back at once, the days before the exit count.

Pre-registered (written before any result was looked at), shared by whale_ret172.py (OKX/HL) and bn_whale_ret172.py (Binance):
 Trades, pools, book exactly as backlog 117/165 (lock_sim.py): live 60-day bar walk-forward, primary pool top-10 by 60-day P&L
 (check: all passing the bar), copy +2 min, stop −16 %, costs + funding, risk 3 % at the stop, daily stop 3 %, ≤ 15 open, one copy
 per coin; share = budget / 7, fast exit at −10 % of the share since (re)joining, checked hourly and at every entry; at the exit
 his open copies close at the market, his new decisions are not copied while he is out.
 Return (checked every hour):
  base «now»   — live: his own decisions (ALL his closed decisions, not only copied ones, by UTC close day) were net > 0 on each of
                 the last 3 full UTC days before the current day; days before the exit count.
  V «after»    — the same, but only full UTC days strictly after the exit day count (day_run(..., since=exit_t)): ≥ 3 days out.
 Simplifications (same in both arms, stated): the seat is free when he qualifies (no back-fill by others), slow / streak / hard
 exits and the 3-day review are not modeled, after a return his still-open positions are not joined.
 One variant × 2 sources. V passes (a DEMO/owner question, never automatic) only if, in the PRIMARY pool of BOTH sources:
  V − base ≥ 0 in BOTH halves of time AND V's max drawdown is smaller (less negative) than base's. Reported, not decisive: the
  paired daily t, number of fast exits and returns, the copies taken in the 72 h after each return (n, mean, sum in % of budget),
  hours out, and the worst day.
"""
import math, statistics as st
import daystop_sim as D
import book_sim as Bk

DAY, H = D.DAY, 3_600_000
NOTIONAL = D.NOTIONAL
LOCK_N, LOCK_LOSS, ROT_DAYS = 7, 0.10, 3
AFTER_H = 72 * H


def day_pnl_map(decs):
    """decs: whale -> [(close_t, pnl)] of ALL his closed decisions → whale -> {utc_day: net pnl}"""
    out = {}
    for w, xs in decs.items():
        m = out.setdefault(w, {})
        for ct, p in xs:
            m[int(ct) // DAY] = m.get(int(ct) // DAY, 0.0) + float(p or 0.0)
    return out


def plus_run(dp, t, since=None):
    """live day_run(q, t, 1, since): full UTC days in a row before today with net > 0 (a day with nothing closed = 0 = break)"""
    today = int(t) // DAY
    d0 = int(since) // DAY + 1 if since is not None else today - 14
    n = 0
    for d in range(today - 1, max(d0, today - 14) - 1, -1):
        if dp.get(d, 0.0) > 0: n += 1
        else: break
    return n


def simulate(trades, decs_dp, mark, cost, mode, X=3.0, max_pos=15):
    """mode 'now' (live) | 'after'. Returns per-day P&L (% of the budget) + stats."""
    ev = sorted(trades, key=lambda x: x["t_in"])
    if not ev: return {"days": {}}
    T0 = ev[0]["t_in"] // DAY * DAY
    T1 = max(t["t_out"] for t in ev)
    thr = -LOCK_LOSS * 100.0 / LOCK_N
    openp, day_pnl, closed_today = [], {}, {}
    blocked_day, wpnl, out = set(), {}, {}
    st_ = {"exits": 0, "returns": 0, "hours_out": [], "taken": 0, "busy": 0, "skipped_out": [], "post": [], "post_t": []}
    recent = {}                                                   # whale -> return time (copies within 72 h counted)

    def book(d, v):
        day_pnl[d] = day_pnl.get(d, 0.0) + v; closed_today[d] = closed_today.get(d, 0.0) + v

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t:
                v = p["ret"] * NOTIONAL; book(p["t_out"] // DAY, v); wpnl[p["w"]] = wpnl.get(p["w"], 0.0) + v
            else: keep.append(p)
        openp = keep

    def unreal(p, t):
        px = mark(p["coin"], t)
        if not px: return 0.0
        s = 1 if p["side"] == "LONG" else -1
        return s * (px / p["a"] - 1) * 100 * NOTIONAL

    def rejoin(t):
        for w in list(out):
            since = out[w] if mode == "after" else None
            if plus_run(decs_dp.get(w, {}), t, since) >= ROT_DAYS:
                st_["returns"] += 1; st_["hours_out"].append((t - out[w]) / H)
                del out[w]; wpnl[w] = 0.0; recent[w] = t

    def check(t):
        nonlocal openp
        for w in set(wpnl) | {p["w"] for p in openp}:
            if w in out: continue
            mine = [p for p in openp if p["w"] == w]
            if wpnl.get(w, 0.0) + sum(unreal(p, t) for p in mine) > thr: continue
            st_["exits"] += 1
            for p in mine:
                r = Bk._cut(p, t, mark, cost)
                if r is None or not math.isfinite(r): continue        # no price: the copy runs to its own exit
                openp.remove(p); book(t // DAY, r * NOTIONAL)
                if p.get("_post") is not None: st_["post"][p["_post"]] = r       # a post-return copy cut again: its realised result
            out[w] = t; wpnl[w] = 0.0; recent.pop(w, None)

    ticks = list(range(T0 + H, int(T1) + H, H))
    i = j = 0
    while i < len(ev) or j < len(ticks):
        if j < len(ticks) and (i >= len(ev) or ticks[j] < ev[i]["t_in"]):
            t = ticks[j]; j += 1
            close_until(t); rejoin(t); check(t); continue
        tr = ev[i]; i += 1
        t = tr["t_in"]; d = t // DAY
        close_until(t); rejoin(t); check(t)
        if tr["w"] in out:
            st_["skipped_out"].append(tr["ret"]); continue
        if X is not None and d not in blocked_day:
            unl = sum(min(0.0, unreal(p, t)) for p in openp)
            if closed_today.get(d, 0.0) + unl <= -X: blocked_day.add(d)
        if X is not None and d in blocked_day: continue
        if any(p["coin"] == tr["coin"] for p in openp) or (max_pos is not None and len(openp) >= max_pos):
            st_["busy"] += 1; continue
        p = dict(tr)
        if tr["w"] in recent and t - recent[tr["w"]] <= AFTER_H:
            p["_post"] = len(st_["post"]); st_["post"].append(tr["ret"]); st_["post_t"].append(t)
        openp.append(p); st_["taken"] += 1
    close_until(float("inf"))
    st_["days"] = day_pnl
    return st_


def _m(xs):
    return round(st.mean(xs), 3) if xs else None


def report(trades, decs, mark, cost, tag, emit=print):
    if not trades: return {"error": "no trades"}
    dp = day_pnl_map(decs)
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1, "whales": len({t["w"] for t in trades})}
    sims = {k: simulate(trades, dp, mark, cost, k) for k in ("now", "after")}
    for k, S in sims.items():
        post = S["post"]
        cell = {"taken": S["taken"], "busy": S["busy"], "exits": S["exits"], "returns": S["returns"],
                "hours_out_median": round(st.median(S["hours_out"]), 1) if S["hours_out"] else None,
                "returns_within_24h": sum(1 for h in S["hours_out"] if h <= 24),
                "skipped_out_n": len(S["skipped_out"]), "skipped_out_mean": _m(S["skipped_out"]),
                "post72_n": len(post), "post72_mean": _m(post), "post72_sum_budget": round(sum(post) * NOTIONAL, 2)}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        R[k] = cell
        emit(f"::notice title={tag} {k}::exits {cell['exits']} returns {cell['returns']} (≤24h {cell['returns_within_24h']}, median out "
             f"{cell['hours_out_median']} h) post72 {cell['post72_n']} mean {cell['post72_mean']}% sum {cell['post72_sum_budget']}% | taken "
             f"{cell['taken']} | all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}")
    P = {p: D.paired(sims["after"]["days"], sims["now"]["days"], a, b) for p, (a, b) in parts}
    b0, b1 = R["now"]["all"], R["after"]["all"]
    P["dd_better"] = bool(b1["max_dd"] > b0["max_dd"])
    P["pass_here"] = bool(P["h1"]["d"] >= 0 and P["h2"]["d"] >= 0 and P["dd_better"])
    R["after_minus_now"] = P
    emit(f"::notice title={tag} after−now::{P}")
    return R
