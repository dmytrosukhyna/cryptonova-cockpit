"""Backlog 257 (23.11.2026): a «probe» entry of the lead trader — a position far smaller than his usual one.
Copy it at 1/4 size, or skip it?  Research only, calculation for the owner; real rules are not touched.

Why: 23.11 0xRubi opened a USELESS short of $29 notional (≈ 0.4 % of his ≈ $7 500 median); the desk copies full size.

Data: ws_trips (Actions cache) + data/whale_trades «closed» rows (as bn_whale_flip245.py). Unit = one trader position (trip).
Copy: first Binance USD-M 5m bar ≥ open + 2 min, out at close + 2 min, stop −16 %, costs 0.10 % a round trip + funding.

Size measure (known limitation, written before results):
- okx: notional = margin × lever of the SUB-position = its entry size → clean (primary).
- bn: notional = maxOpenInterest × entry = the position's MAX size over its life → a small value also means «never added»,
  which is partly future information (biased; info only).
- hl: as stored by whale_study (info only).

Pre-registered (written before any result was seen):
- share = notional / median notional of the trader's previous 20 trips that were CLOSED before this trip opened
  (need ≥ 10 such trips).  flag = share < 5 %;  info thresholds 2 % and 10 %.
- universe (primary): traders with ≥ 20 decisions and the proxy bar (≥ 70 % plus, PF ≥ 2 on own result after costs);
  info: all traders with ≥ 20 decisions.
- variants, equal weight per copy (% of the copy): (a) as now; (b) flagged at 1/4 size = a − 0.75·Σflagged;
  (c) skip flagged = a − Σflagged.
- pass: (b) or (c) better than (a) in BOTH halves of time (split by open time) on the okx primary universe AND flagged
  copies in plus ≤ 5 % of all plus copies; flagged mean t ≤ −2 shown (b and c share one sign → no further correction).
Usage: bn_whale_probe257.py DB OUT.json"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F

B.COST = 0.10
H = 3_600_000
MIN = 60_000
DELAY = 2 * MIN
STOP = 16.0
THR = (0.02, 0.05, 0.10)


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "sum": round(sum(xs), 2)}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "sum": round(sum(xs), 2), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def main():
    db, out = sys.argv[1], sys.argv[2]
    rows = F.trips(db)
    D = F.decisions(rows)
    good, n20 = set(), set()
    for tr, mine in D.items():
        if len(mine) < 20:
            continue
        n20.add(tr)
        xs = [e["wret"] - B.COST for e in mine if e["wret"] is not None]
        if xs:
            pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
            if sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2):
                good.add(tr)
    by = defaultdict(list)
    for r in rows:
        if r[1] in n20 and r[4] in ("LONG", "SHORT") and r[6] and r[6] > r[5]:
            by[r[1]].append(r)
    cand = []   # (row, share)
    for tr, lst in by.items():
        lst.sort(key=lambda r: r[5])
        closed = sorted(((r[6], r[9]) for r in lst if r[9] and r[9] > 0), key=lambda x: x[0])
        import bisect
        ct = [c[0] for c in closed]
        for r in lst:
            if not r[9] or r[9] <= 0:
                continue
            k = bisect.bisect_left(ct, r[5])
            prev = [c[1] for c in closed[max(0, k - 20):k]]
            if len(prev) < 10:
                continue
            med = st.median(prev)
            if med > 0:
                cand.append((r, r[9] / med))
    print(f"trips with a size history: {len(cand)}, traders20 {len(n20)}, good {len(good)}", flush=True)
    bysym = defaultdict(list)
    for r, sh in cand:
        bysym[F.ALIAS.get(r[3], r[3]) + "USDT"].append((r, sh))
    res, miss = [], 0
    for n, (sym, lst) in enumerate(sorted(bysym.items(), key=lambda kv: -len(kv[1]))):
        t0 = min(r[5] for r, _ in lst) - H; t1 = max(r[6] for r, _ in lst) + 2 * H
        S = B.load_sym(sym, t0, t1)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(bysym)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += len(lst); continue
        for r, sh in lst:
            v = B.copy_ret(S, r[4], r[5] + DELAY, r[6] + DELAY, STOP)
            if v is None:
                miss += 1; continue
            res.append({"src": r[0], "trader": r[1], "t": r[5], "share": sh, "ret": v, "good": r[1] in good, "coin": r[3]})
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    O = {"missing": miss, "good_traders": len(good), "traders20": len(n20), "cells": {}}
    for src in ("okx", "bn", "hl", "all"):
        for uni in ("good", "all20"):
            rs = sorted([r for r in res if (src == "all" or r["src"] == src) and (uni == "all20" or r["good"])], key=lambda r: r["t"])
            if len(rs) < 20:
                continue
            mid = rs[len(rs) // 2]["t"]
            allplus = sum(r["ret"] > 0 for r in rs)
            cell = {"n": len(rs), "a": round(sum(r["ret"] for r in rs), 2), "from": time.strftime("%Y-%m-%d", time.gmtime(rs[0]["t"] / 1000)),
                    "to": time.strftime("%Y-%m-%d", time.gmtime(rs[-1]["t"] / 1000)), "median_share": round(st.median(r["share"] for r in rs), 3)}
            for th in THR:
                fl = [r for r in rs if r["share"] < th]
                h1 = sum(r["ret"] for r in fl if r["t"] < mid); h2 = sum(r["ret"] for r in fl if r["t"] >= mid)
                plus_share = (sum(r["ret"] > 0 for r in fl) / allplus) if allplus else None
                q = {"flag": stat([r["ret"] for r in fl]), "rest": stat([r["ret"] for r in rs if r["share"] >= th]),
                     "flag_h1": round(h1, 2), "flag_h2": round(h2, 2), "flag_plus_of_all_plus": round(plus_share, 3) if plus_share is not None else None,
                     "b_minus_a": round(-0.75 * (h1 + h2), 2), "c_minus_a": round(-(h1 + h2), 2),
                     "traders": len({r["trader"] for r in fl})}
                q["pass"] = bool(fl) and h1 < 0 and h2 < 0 and (plus_share or 0) <= 0.05
                cell[f"thr{int(th * 100)}"] = q
            O["cells"][f"{src}/{uni}"] = cell
            parts = []
            for th in THR:
                q = cell[f"thr{int(th * 100)}"]; f_ = q["flag"]
                parts.append(f"<{int(th * 100)}%: n {f_['n']} mean {f_.get('mean')} t {f_.get('t')} win {f_.get('win')} h1/h2 {q['flag_h1']}/{q['flag_h2']} "
                             f"plus {q['flag_plus_of_all_plus']} tr {q['traders']} pass {q['pass']}")
            print(f"::notice title=probe257 {src} {uni}::n {len(rs)} a {cell['a']} rest5 mean {cell['thr5']['rest'].get('mean')} | " + " | ".join(parts) +
                  f" | {cell['from']}→{cell['to']} med share {cell['median_share']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=probe257 meta::rows {len(res)} missing {miss}, good {len(good)}, traders20 {len(n20)}")


if __name__ == "__main__":
    main()
