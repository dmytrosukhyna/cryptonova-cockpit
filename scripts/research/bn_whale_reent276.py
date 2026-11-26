"""Backlog 276 (26.11.2026): re-enter a copy after our -16 % stop if the trader still holds and the price is back in
the join window. Research only; real rules are not touched.

Follow-up of 275: in the T-bar about 60 % of our -16 % stops are copies the trader later closed in plus.

Pre-registered before any result was seen:
 Book T exactly as in 274/275 (bn_whale_liq271.lev_trips, >= 20 closed trips per trader, copy in 2 min after the
   trader, out 2 min after him, costs 0.20 % a round trip on EVERY entry). T-bar = win >= 70 %, PF >= 2.
 A = as now: stop -16 % from our entry, no re-entry.
 R (the only candidate): if A was stopped at bar q and the trader still holds, scan the 5m bars after q up to the
   trader's exit + 2 min; at the first bar whose CLOSE is inside the join window (-3 %...+1 % of the trader's own
   entry, in the direction of the trade) enter again at the NEXT bar's open, same size (same risk), stop -16 % from
   the new entry, out with the trader (+2 min) or at that stop. At most 1 re-entry.
 Pass (a demo-desk candidate only): in T-bar R beats A in BOTH halves (time split at the median entry); in the second
   half the t-statistic over DAYS of the re-entry results (sum per UTC day of re-entry) is >= 2; reported worst case:
   the share and mean of «two stops in a row».
 1 candidate x 2 groups (T-bar, T all) noted.
Usage: bn_whale_reent276.py DB OUT.json"""
import json, math, statistics as st, sys, time
from collections import defaultdict
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_liq271 as L
import bn_whale_stops as B

S16 = 16.0
DAY = 86_400_000


def run_from(S, side, i, j, stop):
    """enter at the open of bar i, out at the open of bar j or at the stop -> (ret %, stop bar or None)"""
    s = 1 if side == "LONG" else -1
    entry = S["k"][S["t"][i]][0]
    sp = entry * (1 - s * stop / 100)
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= sp) or (s < 0 and h >= sp):
            px = o if q > i and ((s > 0 and o < sp) or (s < 0 and o > sp)) else sp
            return L.r_(side, entry, px), q
    return L.r_(side, entry, S["k"][S["t"][j]][0]), None


def in_window(side, we, c):
    if side == "LONG":
        return we * 0.97 <= c <= we * 1.01
    return we * 0.99 <= c <= we * 1.03


def one(S, side, t_in, t_out, we):
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    a, q = run_from(S, side, i, j, S16)
    x = {"a": a, "stopped": q is not None, "re": None, "re_t": None, "re_stopped": False, "whale_after": None}
    if q is None:
        return x
    for k in range(q + 1, j - 1):
        c = S["k"][S["t"][k]][3]
        if in_window(side, we, c):
            r2, q2 = run_from(S, side, k + 1, j, S16)
            x.update(re=r2, re_t=S["t"][k + 1], re_stopped=q2 is not None)
            break
    return x


def tstat(d):
    if len(d) < 2 or not st.pstdev(d):
        return None
    return round(st.mean(d) / (st.pstdev(d) / math.sqrt(len(d))), 2)


def report(name, rows):
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        print(f"::notice title=reent276 {name}::empty")
        return {"n": 0}
    mid = rows[len(rows) // 2]["t"]
    O = {"n": len(rows)}
    for half, sel in (("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid), ("all", lambda r: True)):
        hs = [r for r in rows if sel(r)]
        stp = [r for r in hs if r["stopped"]]
        re_ = [r for r in stp if r["re"] is not None]
        A = sum(r["a"] for r in hs)
        R = A + sum(r["re"] for r in re_)
        byday = defaultdict(float)
        for r in re_:
            byday[r["re_t"] // DAY] += r["re"]
        dbl = [r for r in re_ if r["re_stopped"]]
        O[half] = {"n": len(hs), "stops": len(stp), "re_n": len(re_), "A": round(A, 2), "R": round(R, 2),
                   "re_mean": round(st.mean([r["re"] for r in re_]), 3) if re_ else None,
                   "re_win": round(sum(r["re"] > 0 for r in re_) / len(re_), 3) if re_ else None,
                   "t_days": tstat(list(byday.values())), "days": len(byday),
                   "t_copies": tstat([r["re"] for r in re_]),
                   "double_stop_n": len(dbl), "double_stop_share": round(len(dbl) / len(re_), 3) if re_ else None,
                   "double_stop_mean": round(st.mean([r["a"] + r["re"] for r in dbl]), 2) if dbl else None,
                   "re_worst": round(min((r["re"] for r in re_), default=0), 2)}
    h1, h2 = O["h1"], O["h2"]
    O["pass_R"] = bool(h1["R"] > h1["A"] and h2["R"] > h2["A"] and (h2["t_days"] or 0) >= 2)
    print(f"::notice title=reent276 {name}::n {O['n']} | "
          + " | ".join(f"{h}: stops {O[h]['stops']} re {O[h]['re_n']} A {O[h]['A']} R {O[h]['R']} re_mean {O[h]['re_mean']} "
                       f"win {O[h]['re_win']} t_days {O[h]['t_days']} ({O[h]['days']} d) t_copies {O[h]['t_copies']}; "
                       f"double stop {O[h]['double_stop_n']} ({O[h]['double_stop_share']}) mean {O[h]['double_stop_mean']} worst {O[h]['re_worst']}"
                       for h in ("h1", "h2", "all")) + f" | pass R {O['pass_R']}")
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    OUT = {}
    rows = L.lev_trips(db)
    now = time.time() * 1000
    for r in rows:
        if r[6] <= now - 10 * L.MIN:
            L.want(r[3], r[5] - L.H, r[6] + L.H)
    print(f"coins to load: {len(L.SPAN)}", flush=True)
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    T = []
    for tr, lst in by.items():
        if len(lst) < 20:
            continue
        xs = [(1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - L.RT for r in lst if r[8]]
        pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
        bar = bool(xs) and sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2)
        for r in lst:
            if r[6] > now - 10 * L.MIN or not r[8]:
                continue
            S = L.ser(r[3])
            if not S:
                continue
            x = one(S, r[4], r[5] + L.DELAY, r[6] + L.DELAY, r[7])
            if not x:
                continue
            x.update(t=r[5], who=tr, bar=bar, wh=L.r_(r[4], r[7], r[8]))
            T.append(x)
    OUT["T_bar"] = report("T_bar %", [r for r in T if r["bar"]])
    OUT["T"] = report("T %", T)
    print(f"::notice title=reent276 verdict::R passes T-bar: {OUT['T_bar'].get('pass_R')}; T all: {OUT['T'].get('pass_R')}")
    json.dump(OUT, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
