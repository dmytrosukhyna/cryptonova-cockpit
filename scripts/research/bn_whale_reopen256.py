"""Backlog 256 (23.11.2026): the lead trader closes a position and opens the same coin + side again within ≤ 60 min —
should our copy hold through instead of closing and re-entering?
Research only, calculation for the owner; real rules are not touched.

Why: 23.11 03:09 UTC 0xRubi closed UNI/USELESS/PUMP shorts and re-opened them 5–55 min later; the desk paid a closed
−$1.90, double fees, and re-entered 0.3–1.9 % worse.

Data: ws_trips (Actions cache) + data/whale_trades «closed» rows (as bn_whale_flip245.py). Unit = one trader position (trip).
Copy: first Binance USD-M 5m bar ≥ time, stop −16 % from our entry, costs 0.10 % a round trip + funding (B.copy_ret).

Pre-registered (written before any result was seen):
- (a) as now: every trip copied on its own: in at open + 2 min, out at close + 2 min.
- (w) implementable «wait W»: when the trader closes, we keep the copy W minutes; if he re-opens the same coin + side
  within W of his close, the copy simply continues; otherwise we exit at close + W.  A chain = trips linked by gaps
  0 ≤ gap ≤ W; the copy runs from the first open + 2 min to the last close + max(W, 2 min) — one round trip of costs.
  W ∈ {10, 30, 60} min.  This costs on EVERY close (exit delayed W), so it is compared over all trips, not only pairs.
- (o) oracle info only: hold through only the pairs (gap ≤ 60), exit at close + 2 min otherwise (not implementable,
  shows the most a perfect rule could gain).
- universe (primary): traders with ≥ 20 decisions and the proxy bar (≥ 70 % plus, PF ≥ 2 on own result after costs);
  info: all traders with ≥ 20 decisions.
- pass: some W with (w) − (a) > 0 in BOTH halves of time (by chain start), t of the per-day difference ≥ 2 after
  Bonferroni over 3 W (≈ t ≥ 2.4), and ≥ 30 re-open pairs in the universe.
Usage: bn_whale_reopen256.py DB OUT.json"""
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
WS = (10, 30, 60)


def chains(lst, w_min):
    """lst = trips of one trader/coin/side sorted by open; link j to the chain if 0 ≤ open_j − close_chain ≤ W"""
    out = []
    for r in lst:
        if out and 0 <= r[5] - out[-1]["close"] <= w_min * MIN:
            c = out[-1]; c["trips"].append(r); c["close"] = max(c["close"], r[6])
        elif out and r[5] < out[-1]["close"]:
            c = out[-1]; c["trips"].append(r); c["close"] = max(c["close"], r[6]); c["overlap"] = True  # add-on while open: same copy
        else:
            out.append({"open": r[5], "close": r[6], "trips": [r]})
    return out


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "sum": round(sum(xs), 2)}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "sum": round(sum(xs), 2), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None}


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
    grp = defaultdict(list)
    for r in rows:
        if r[1] in n20 and r[4] in ("LONG", "SHORT") and r[6] and r[6] > r[5]:
            grp[(r[1], r[3], r[4])].append(r)
    for v in grp.values():
        v.sort(key=lambda r: r[5])
    print(f"trips of ≥20-decision traders: {sum(len(v) for v in grp.values())}, groups {len(grp)}, good {len(good)}", flush=True)
    bysym = defaultdict(list)
    for k, v in grp.items():
        bysym[F.ALIAS.get(k[1], k[1]) + "USDT"].append(k)
    res, miss = [], 0   # one row per base chain (overlaps merged, W = 0) with a / w10 / w30 / w60 / oracle
    for n, (sym, keys) in enumerate(sorted(bysym.items(), key=lambda kv: -sum(len(grp[k]) for k in kv[1]))):
        t0 = min(grp[k][0][5] for k in keys) - H; t1 = max(max(r[6] for r in grp[k]) for k in keys) + 2 * H
        S = B.load_sym(sym, t0, t1)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(bysym)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += sum(len(grp[k]) for k in keys); continue
        for k in keys:
            base = chains(grp[k], -1)                 # W < 0: only overlapping add-ons merged → «as now» copies
            a = []
            for c in base:
                v = B.copy_ret(S, k[2], c["open"] + DELAY, c["close"] + DELAY, STOP)
                a.append(v)
            if any(v is None for v in a):
                miss += len(base); continue
            bo = [c["open"] for c in base]
            rows_k, bad = [], False
            for W in WS:
                for c in chains(grp[k], W):
                    v = B.copy_ret(S, k[2], c["open"] + DELAY, c["close"] + max(W * MIN, DELAY), STOP)
                    idx = [i for i, x in enumerate(bo) if c["open"] <= x <= c["close"]]
                    vo = B.copy_ret(S, k[2], c["open"] + DELAY, c["close"] + DELAY, STOP) if (W == 60 and len(idx) >= 2) else None
                    if v is None or (W == 60 and len(idx) >= 2 and vo is None):
                        bad = True; break
                    av = sum(a[i] for i in idx)
                    rows_k.append({"W": W, "t": c["open"], "trader": k[0], "coin": k[1], "side": k[2], "good": k[0] in good,
                                   "n_base": len(idx), "a": av, "w": v, "o": vo if vo is not None else av})
                if bad:
                    break
            if bad:
                miss += len(base); continue
            res += rows_k
            # oracle: pairs with gap ≤ 60 held through, others as now (same as W=60 on chains with ≥2 base, else a)
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    O = {"missing": miss, "good_traders": len(good), "traders20": len(n20)}
    for uni, f in (("good", lambda r: r["good"]), ("all20", lambda r: True)):
        O[uni] = {}
        for W in WS:
            rs = sorted([r for r in res if r["W"] == W and f(r)], key=lambda r: r["t"])
            if not rs:
                continue
            mid = rs[len(rs) // 2]["t"]
            pairs = [r for r in rs if r["n_base"] >= 2]
            day = defaultdict(float)
            for r in rs:
                day[int(r["t"] // (24 * H))] += r["w"] - r["a"]
            q = {"chains": len(rs), "base_copies": sum(r["n_base"] for r in rs), "pairs": len(pairs),
                 "pair_trips": sum(r["n_base"] for r in pairs),
                 "a": round(sum(r["a"] for r in rs), 2), "w": round(sum(r["w"] for r in rs), 2),
                 "h1": [round(sum(r["a"] for r in rs if r["t"] < mid), 2), round(sum(r["w"] for r in rs if r["t"] < mid), 2)],
                 "h2": [round(sum(r["a"] for r in rs if r["t"] >= mid), 2), round(sum(r["w"] for r in rs if r["t"] >= mid), 2)],
                 "pairs_a": round(sum(r["a"] for r in pairs), 2), "pairs_w": round(sum(r["w"] for r in pairs), 2),
                 "singles_cost": round(sum(r["w"] - r["a"] for r in rs if r["n_base"] < 2), 2),
                 "oracle": round(sum(r["o"] for r in rs), 2) if W == 60 else None,
                 "per_day": tstat(list(day.values())),
                 "from": time.strftime("%Y-%m-%d", time.gmtime(rs[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(rs[-1]["t"] / 1000))}
            q["pass"] = (q["h1"][1] > q["h1"][0] and q["h2"][1] > q["h2"][0] and (q["per_day"].get("t") or 0) >= 2.4 and q["pairs"] >= 30)
            O[uni][W] = q
            print(f"::notice title=reopen256 {uni} W{W}::chains {q['chains']} base {q['base_copies']} pairs {q['pairs']} ({q['pair_trips']} trips) | "
                  f"a {q['a']} w {q['w']} | h1 {q['h1']} h2 {q['h2']} | pairs a/w {q['pairs_a']}/{q['pairs_w']} | singles cost {q['singles_cost']} | "
                  f"oracle {q['oracle'] if W == 60 else '-'} | per-day {q['per_day']} | {q['from']}→{q['to']} | pass {q['pass']}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=reopen256 meta::missing {miss}, good {len(good)}, traders20 {len(n20)}")


if __name__ == "__main__":
    main()
