"""Backlog 139 — leave column A when the whale himself stops passing the selection bar (11.11.2026, research only).

Shared by whale_rotbar.py (OKX/HL, lab-data-whales) and bn_whale_rotbar.py (Binance). Copy trades are built as in backlog 117
(copy +2 min, stop -16 %, costs 0.11 % + funding) but for EVERY decision of every whale, not only bar-passing ones, because a
whale already in column A is copied whether or not he still passes.

Pre-registered (written before any result was looked at):
 Daily check at 00:00 UTC. Bar at day T = live 60-day bar on decisions closed before T and opened in the last 60 days:
 >= 20 decisions, win >= 70 %, PF >= 2, P&L > 0 (open positions marked to market are NOT included — stated simplification,
 same as backlogs 101–132).
 Column A = 7 slots. A whale enters a free slot when he passed the bar on the last 3 daily checks in a row (newcomer rule);
 several candidates → the highest 60-day P&L first. Copied: every decision whose entry falls while the whale is in A.
 Open copies of a leaving whale live to the whale's own exit (owner decision 09.11).
  (a) «v1»   — a whale leaves A only when his 60-day P&L is <= 0 (copy-loss / hidden / no-data exits are not modeled — same
               in both arms).
  (b) «bar2» — as (a) + he leaves when he failed the bar on 2 daily checks in a row; the slot goes to the next candidate;
               he may come back after passing 3 days in a row again.
 Book = live engine: risk 3 % at the 16 % stop, daily stop 3 %, <= 15 open, one copy per coin (lock_sim.simulate 'none').
 1 alternative × 2 sources = 2 looks. (b) passes only if its total beats (a) with a paired daily t >= 2.5 in BOTH halves and
 BOTH sources, and its worst day and max drawdown are not worse than 1.25 × (a). Anything else = not passed.
 Reported, not decisive: copies per arm, mean return and PF of the copies, list changes, and the decisions of whales that are in
 A under (a) while failing the bar 2+ days (what (b) drops).
"""
import math, statistics as st
import daystop_sim as D
import lock_sim as K

DAY = 86_400_000
SLOTS, ENTER_N, LEAVE_N = 7, 3, 2


def bar_at(dl, T):
    pri = [d for d in dl if d[1] < T and d[0] >= T - 60 * DAY]
    p = sum(d[2] for d in pri)
    if len(pri) < 20: return False, p
    wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
    return (wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and p > 0), p


def membership(decs, d0, d1, arm):
    """decs: whale -> [(open_t, close_t, pnl)]. Returns {day: frozenset(A)} (A valid during that day), changes, fail-days set."""
    s, f, A, out, changes, failing = {}, {}, set(), {}, 0, {}
    for d in range(d0, d1 + 1):
        T = d * DAY; stat = {}
        for w, dl in decs.items():
            ok, p = bar_at(dl, T); stat[w] = (ok, p)
            s[w] = s.get(w, 0) + 1 if ok else 0
            f[w] = 0 if ok else f.get(w, 0) + 1
        for w in list(A):
            if stat[w][1] <= 0 or (arm == "bar2" and f[w] >= LEAVE_N):
                A.discard(w); changes += 1
        cand = sorted((stat[w][1], w) for w in decs if w not in A and s[w] >= ENTER_N)
        while len(A) < SLOTS and cand:
            A.add(cand.pop()[1]); changes += 1
        out[d] = frozenset(A)
        failing[d] = frozenset(w for w in A if f[w] >= LEAVE_N)
    return out, changes, failing


def _pf(xs):
    gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return round(gp / gl, 2) if gl > 0 else None


def report(decs, trades, mark, cost, tag, emit=print):
    if not trades: return {"error": "no trades"}
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY
    R = {"n_decisions": len(trades), "whales": len(decs)}
    sims, sel_all = {}, {}
    for arm in ("v1", "bar2"):
        M, ch, failing = membership(decs, d0, d1, arm)
        sel = [t for t in trades if t["w"] in M.get(t["t_in"] // DAY, ())]
        sel_all[arm] = sel
        if arm == "v1":
            drop = [t for t in sel if t["w"] in failing.get(t["t_in"] // DAY, ())]
            R["v1_failing_copied"] = {"n": len(drop), "mean": K._m([t["ret"] for t in drop]), "pf": _pf([t["ret"] for t in drop])}
        S = K.simulate(sel, mark, cost, "none"); sims[arm] = S
        R[arm] = {"changes": ch, "selected": len(sel), "sel_mean": K._m([t["ret"] for t in sel]), "sel_pf": _pf([t["ret"] for t in sel]),
                  "taken": S["taken"], "busy": S["busy"]}
    sel_days = [t["t_in"] // DAY for t in sel_all["v1"] + sel_all["bar2"]]
    if not sel_days: return R
    a0 = min(sel_days); mid = (a0 + d1) // 2
    parts = (("all", (a0, d1)), ("h1", (a0, mid)), ("h2", (mid + 1, d1)))
    for arm in ("v1", "bar2"):
        for p, (a, b) in parts:
            R[arm][p] = {k: (float(v) if v is not None else None) for k, v in D.curve_stats(sims[arm]["days"], a, b).items()}
        emit(f"::notice title={tag} {arm}::{R[arm]}")
    P = {p: D.paired(sims["bar2"]["days"], sims["v1"]["days"], a, b) for p, (a, b) in parts}
    b0, b1 = R["v1"]["all"], R["bar2"]["all"]
    P["risk_ok"] = bool(b1["worst_day"] >= 1.25 * b0["worst_day"] and b1["max_dd"] >= 1.25 * b0["max_dd"])
    P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["all"]["d"] > 0 and P["risk_ok"])
    R["bar2_minus_v1"] = P
    emit(f"::notice title={tag} bar2−v1::{P} | v1 copied while failing: {R['v1_failing_copied']}")
    return R
