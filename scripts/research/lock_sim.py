"""Backlog 117 — the lock's hard rule «his copies on our desk lost more than 10 % of his share → the whale leaves» (09.11.2026, research only).

Shared by whale_lockloss.py (OKX/HL, lab-data-whales) and bn_whale_lockloss.py (Binance). Trades are built exactly as in backlog 116
(whale_fresh.py / bn_whale_fresh.py): live bar walk-forward (prior 60 d: win >= 70 %, PF >= 2, >= 20 decisions, P&L > 0), primary
pool top-10 by 60-day P&L, copy +2 min, stop -16 %, costs 0.11 % + funding. Book = live engine: risk 3 % at the 16 % stop
(notional 18.75 % of the budget), daily stop 3 %, at most 15 open, one copy per coin, first come first served; fixed budget.

Pre-registered (written before any result was looked at):
 Share of a whale = budget / 7 (LOCK_N); threshold = -10 % of the share = -1.43 % of the budget (LOCK_LOSS, unchanged).
 A whale's desk P&L since he (re)joined = his closed copies + his open copies marked to market. Checked every hour and at every
 entry. Reviews every 3 days on a fixed grid from the first day.
  base «live»   — at the threshold the whale leaves: his open copies are closed at the market (+ costs, funding pro-rated), his new
                  decisions are not copied; at the first review after that he is back (he still passes the bar in this pool —
                  as BTC杰师傅 on 09.11) and his P&L counter restarts from zero.
  B «no rule»   — no loss exit: copies close with the whale, until the review as always.
  C «pause 7 d» — as live, but he comes back only at the first review at least 7 days after he left (the owner's open question,
                  alerts.md 09.11 10:15).
 2 alternatives × 2 sources = 4 looks. An alternative is suggested only if it beats base on total P&L with a paired daily
 t >= 2.5 in BOTH time halves and BOTH sources, and its worst day and max drawdown are not worse than 1.25 × base.
 Anything else = not passed (the live rule stays). Reported, not decisive: how often the rule fires, what the closed copies
 did afterwards (cut result vs their own exit), the mean of decisions skipped while out.
 Simplification (stated): after a return, his positions that are still open are not joined (live joins them within −3…+1 %).
"""
import math, statistics as st
import daystop_sim as D
import book_sim as Bk

DAY, H = D.DAY, 3_600_000
NOTIONAL = D.NOTIONAL
LOCK_N, LOCK_LOSS, REVIEW = 7, 0.10, 3 * DAY


def simulate(trades, mark, cost, mode, X=3.0, max_pos=15, pause=0):
    """mode: 'live' | 'none' | 'pause'. Returns per-day P&L (% of the budget) and rule stats."""
    ev = sorted(trades, key=lambda x: x["t_in"])
    if not ev: return {"days": {}}
    T0 = ev[0]["t_in"] // DAY * DAY
    T1 = max(t["t_out"] for t in ev)
    thr = -LOCK_LOSS * 100.0 / LOCK_N
    openp, day_pnl, closed_today = [], {}, {}
    blocked_day, wpnl, out = set(), {}, {}
    st_ = {"drops": 0, "cut_n": 0, "cut_ret": [], "own_ret": [], "skipped_out": [], "taken": 0, "busy": 0}

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
        for w in [w for w, r in out.items() if t >= r]:
            del out[w]; wpnl[w] = 0.0

    def check(t):
        nonlocal openp
        if mode == "none": return
        ws = set(wpnl) | {p["w"] for p in openp}
        for w in ws:
            if w in out: continue
            mine = [p for p in openp if p["w"] == w]
            if wpnl.get(w, 0.0) + sum(unreal(p, t) for p in mine) > thr: continue
            st_["drops"] += 1
            for p in mine:
                r = Bk._cut(p, t, mark, cost)
                if r is None or not math.isfinite(r): continue        # no price: the copy runs to its own exit
                openp.remove(p); book(t // DAY, r * NOTIONAL)
                st_["cut_n"] += 1; st_["cut_ret"].append(r); st_["own_ret"].append(p["ret"])
            wait = pause if mode == "pause" else 0
            k = (t + wait - T0) // REVIEW + 1
            out[w] = T0 + k * REVIEW
            wpnl[w] = 0.0

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
        openp.append(tr); st_["taken"] += 1
    close_until(float("inf"))
    st_["days"] = day_pnl
    return st_


def _m(xs):
    return round(st.mean(xs), 3) if xs else None


def report(trades, mark, cost, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1, "whales": len({t["w"] for t in trades})}
    sims = {"live": simulate(trades, mark, cost, "live"), "B_none": simulate(trades, mark, cost, "none"),
            "C_pause7": simulate(trades, mark, cost, "pause", pause=7 * DAY)}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "busy": S["busy"], "drops": S["drops"], "cut_n": S["cut_n"],
                "cut_mean": _m(S["cut_ret"]), "own_exit_mean": _m(S["own_ret"]),
                "cut_saved_budget": round(sum(a - b for a, b in zip(S["cut_ret"], S["own_ret"])) * NOTIONAL, 2),
                "skipped_out_n": len(S["skipped_out"]), "skipped_out_mean": _m(S["skipped_out"]),
                "skipped_out_sum_budget": round(sum(S["skipped_out"]) * NOTIONAL, 2)}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        R[k] = cell
        emit(f"::notice title={tag} {k}::taken {cell['taken']} drops {cell['drops']} cut {cell['cut_n']} (cut {cell['cut_mean']}% vs own exit "
             f"{cell['own_exit_mean']}%, saved {cell['cut_saved_budget']}%) skipped-out {cell['skipped_out_n']} mean {cell['skipped_out_mean']}% "
             f"| all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}")
    for alt in ("B_none", "C_pause7"):
        P = {p: D.paired(sims[alt]["days"], sims["live"]["days"], a, b) for p, (a, b) in parts}
        b0, b1 = R["live"]["all"], R[alt]["all"]
        P["risk_ok"] = bool((b1["worst_day"] >= 1.25 * b0["worst_day"]) and (b1["max_dd"] >= 1.25 * b0["max_dd"]))
        P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["all"]["d"] > 0 and P["risk_ok"])
        R[alt + "_minus_live"] = P
        emit(f"::notice title={tag} {alt}−live::{P}")
    return R
