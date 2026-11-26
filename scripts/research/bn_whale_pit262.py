"""Backlog 262 (26.11.2026): are the «probe» copies (п. 257) better only because the proxy bar was computed on the same trades?

П. 257 found: probes (notional < 5 % of the trader's median of his previous 20 closed trips) of traders that pass the proxy bar
gave ≈ +2 % a copy, while probes of traders WITHOUT the bar ≈ 0.  The bar there used ALL of the trader's decisions, including
the probe itself and everything after it (look-ahead): a lucky small trade can lift a trader over the bar.

Pre-registered (written before any result was seen):
- same data, copy and probe definition as bn_whale_probe257.py (copy +2 min, out close +2 min, stop −16 %, 0.10 % costs + funding).
- point-in-time bar (pit): for each trip opened at t, the trader's decisions CLOSED before t and opened within the 60 days before t;
  need ≥ 20 of them, ≥ 70 % in plus and PF ≥ 2 on own result after costs, positive sum (as the live selection).
- cells: full-sample bar (as 257, reference), pit bar, pit «no bar» (≥ 20 decisions in the window but failing);
  in each: flagged (< 5 %) vs rest, both halves of time, mean t.
- question answered, one test: in the pit bar the flagged mean is > 0 in BOTH halves and its t ≥ 2 → «probes better» survives
  without look-ahead; otherwise it was (at least partly) the bar eating lucky small trades.  Info thresholds 2 % / 10 %.
Usage: bn_whale_pit262.py DB OUT.json"""
import bisect, json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F

B.COST = 0.10
H = 3_600_000
DAY = 24 * H
MIN = 60_000
DELAY = 2 * MIN
STOP = 16.0
WIN = 60 * DAY
THR = (0.02, 0.05, 0.10)


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "sum": round(sum(xs), 2)}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "sum": round(sum(xs), 2), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def passes(xs):
    if len(xs) < 20:
        return None
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2) and sum(xs) > 0


def main():
    db, out = sys.argv[1], sys.argv[2]
    rows = F.trips(db)
    D = F.decisions(rows)
    good, n20 = set(), set()
    hist = {}
    for tr, mine in D.items():
        dec = sorted(((e["close_t"], e["open_t"], e["wret"] - B.COST) for e in mine if e["wret"] is not None), key=lambda x: x[0])
        hist[tr] = (dec, [d[0] for d in dec])
        if len(mine) < 20:
            continue
        n20.add(tr)
        p = passes([d[2] for d in dec])
        if p:
            good.add(tr)

    def pit(tr, t):
        dec, ct = hist.get(tr, ([], []))
        k = bisect.bisect_left(ct, t)
        xs = [d[2] for d in dec[:k] if d[1] >= t - WIN]
        return passes(xs)

    by = defaultdict(list)
    for r in rows:
        if r[4] in ("LONG", "SHORT") and r[6] and r[6] > r[5]:
            by[r[1]].append(r)
    cand = []
    for tr, lst in by.items():
        lst.sort(key=lambda r: r[5])
        closed = sorted(((r[6], r[9]) for r in lst if r[9] and r[9] > 0), key=lambda x: x[0])
        ct = [c[0] for c in closed]
        for r in lst:
            if not r[9] or r[9] <= 0:
                continue
            k = bisect.bisect_left(ct, r[5])
            prev = [c[1] for c in closed[max(0, k - 20):k]]
            if len(prev) < 10:
                continue
            med = st.median(prev)
            if med <= 0:
                continue
            pg = pit(tr, r[5])
            if pg is None and tr not in n20:
                continue
            cand.append((r, r[9] / med, pg))
    print(f"trips with a size history: {len(cand)}, traders20 {len(n20)}, good(full) {len(good)}", flush=True)
    bysym = defaultdict(list)
    for c in cand:
        bysym[F.ALIAS.get(c[0][3], c[0][3]) + "USDT"].append(c)
    res, miss = [], 0
    for n, (sym, lst) in enumerate(sorted(bysym.items(), key=lambda kv: -len(kv[1]))):
        t0 = min(c[0][5] for c in lst) - H; t1 = max(c[0][6] for c in lst) + 2 * H
        S = B.load_sym(sym, t0, t1)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(bysym)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += len(lst); continue
        for r, sh, pg in lst:
            v = B.copy_ret(S, r[4], r[5] + DELAY, r[6] + DELAY, STOP)
            if v is None:
                miss += 1; continue
            res.append({"src": r[0], "trader": r[1], "t": r[5], "share": sh, "ret": v, "full": r[1] in good, "pit": pg})
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    O = {"missing": miss, "good_full": len(good), "traders20": len(n20), "cells": {}}
    unis = {"full": lambda r: r["full"], "pit": lambda r: r["pit"] is True, "pit_no": lambda r: r["pit"] is False,
            "pit_and_full": lambda r: r["pit"] is True and r["full"], "full_not_pit": lambda r: r["full"] and r["pit"] is not True}
    for src in ("okx", "bn", "all"):
        for uni, f in unis.items():
            rs = sorted([r for r in res if (src == "all" or r["src"] == src) and f(r)], key=lambda r: r["t"])
            if len(rs) < 20:
                continue
            mid = rs[len(rs) // 2]["t"]
            cell = {"n": len(rs), "all": stat([r["ret"] for r in rs]), "traders": len({r["trader"] for r in rs}),
                    "from": time.strftime("%Y-%m-%d", time.gmtime(rs[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(rs[-1]["t"] / 1000))}
            for th in THR:
                fl = [r for r in rs if r["share"] < th]
                q = {"flag": stat([r["ret"] for r in fl]), "rest": stat([r["ret"] for r in rs if r["share"] >= th]),
                     "flag_h1": stat([r["ret"] for r in fl if r["t"] < mid]), "flag_h2": stat([r["ret"] for r in fl if r["t"] >= mid]),
                     "rest_h1": stat([r["ret"] for r in rs if r["share"] >= th and r["t"] < mid]),
                     "rest_h2": stat([r["ret"] for r in rs if r["share"] >= th and r["t"] >= mid]),
                     "flag_traders": len({r["trader"] for r in fl})}
                h1, h2 = q["flag_h1"].get("mean"), q["flag_h2"].get("mean")
                q["survives"] = bool(h1 is not None and h2 is not None and h1 > 0 and h2 > 0 and (q["flag"].get("t") or 0) >= 2)
                cell[f"thr{int(th * 100)}"] = q
            O["cells"][f"{src}/{uni}"] = cell
            q = cell["thr5"]
            print(f"::notice title=pit262 {src} {uni}::n {len(rs)} tr {cell['traders']} all mean {cell['all'].get('mean')} t {cell['all'].get('t')} | "
                  f"<5%: n {q['flag']['n']} mean {q['flag'].get('mean')} t {q['flag'].get('t')} win {q['flag'].get('win')} "
                  f"h1 {q['flag_h1'].get('mean')} h2 {q['flag_h2'].get('mean')} tr {q['flag_traders']} | rest mean {q['rest'].get('mean')} "
                  f"h1 {q['rest_h1'].get('mean')} h2 {q['rest_h2'].get('mean')} | <2% {cell['thr2']['flag'].get('n')}/{cell['thr2']['flag'].get('mean')} "
                  f"<10% {cell['thr10']['flag'].get('n')}/{cell['thr10']['flag'].get('mean')} | survives {q['survives']} | {cell['from']}→{cell['to']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=pit262 meta::rows {len(res)} missing {miss}, good(full) {len(good)}, traders20 {len(n20)}")


if __name__ == "__main__":
    main()
