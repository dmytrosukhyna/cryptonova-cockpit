"""Backlog 249 (22.11.2026): «reverse leader» — mirror the WORST lead traders (paper calculation only, real rules untouched).
Idea from the 22.11 tech scout (MEXC has a «reverse leader» copy mode: https://www.mexc.com/futures/copyTrade/reverseLeader).

Data and copy mechanics exactly as backlog 245 (bn_whale_flip245.py): ws_trips (Actions cache) + data/whale_trades «closed»,
decision = one trader's entries in the same coin + side within 4 h; copy = first Binance USD-M 5m bar ≥ whale open + 2 min,
exit at the first bar ≥ whale close + 2 min, stop −16 % on OUR side, costs 0.10 % a round trip + funding (B.copy_ret).
A reverse copy = the same, but on the OPPOSITE side of the trader.

Pre-registered (written before any result was seen):
- walk-forward selection, no look-ahead: a decision opened at time t is mirrored only if the trader, on decisions he CLOSED in
  the L days before t, had ≥ 20 decisions and the selection test below is true at t.
- 4 variants: L ∈ {60, 30} × test ∈ {«neg»: his own sum after costs < 0; «bad»: win < 50 % AND PF < 0.7}.
- primary = L60/neg (mirrors the live bar «60 days, positive P&L», inverted).
- metric per reverse copy, % of the copy, equal weight. Because copies cluster in time, the gate uses a DAY-clustered t
  (mean of each UTC day's copies → t over days).
- pass (all of): ≥ 150 reverse copies; day-clustered t ≥ 2.5 (Bonferroni for 4 variants, one-sided ≈ 0.0125 → 2.24, rounded up);
  mean > 0 in BOTH halves of time; still > 0 without the single trader with the largest contribution.
- reference rows (not gates): the same decisions copied FORWARD (should be negative if the idea works), and reverse copies by source.
Usage: bn_whale_rev249.py DB OUT.json"""
import json, math, os, statistics as st, sys, time
from bisect import bisect_left
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F

B.COST = 0.10
H = F.H
D = 24 * H
DELAY = F.DELAY
STOP = F.STOP
VARIANTS = {"L60_neg": (60, "neg"), "L60_bad": (60, "bad"), "L30_neg": (30, "neg"), "L30_bad": (30, "bad")}


def test(xs, kind):
    if len(xs) < 20:
        return False
    if kind == "neg":
        return sum(xs) < 0
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    pf = pos / neg if neg else 99.0
    return sum(x > 0 for x in xs) / len(xs) < 0.5 and pf < 0.7


def mark(mine):
    """for each decision: which variants select the trader at its open time (only decisions closed before it count)"""
    closed = sorted((e["close_t"], e["wret"] - B.COST) for e in mine if e["wret"] is not None)
    ct = [c[0] for c in closed]
    for e in mine:
        for name, (L, kind) in VARIANTS.items():
            lo, hi = bisect_left(ct, e["open_t"] - L * D), bisect_left(ct, e["open_t"])
            e[name] = test([c[1] for c in closed[lo:hi]], kind)


def dstat(rs, key="rv"):
    xs = [r[key] for r in rs]
    if len(xs) < 2:
        return {"n": len(xs)}
    m, sd = st.mean(xs), st.pstdev(xs)
    days = defaultdict(list)
    for r in rs:
        days[r["t"] // D].append(r[key])
    dm = [st.mean(v) for v in days.values()]
    dt = (st.mean(dm) / (st.pstdev(dm) / math.sqrt(len(dm)))) if len(dm) > 1 and st.pstdev(dm) else None
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None, "days": len(dm),
            "t_day": round(dt, 2) if dt is not None else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3), "sum": round(sum(xs), 1)}


def judge(rs):
    rs = sorted(rs, key=lambda r: r["t"])
    O = {"all": dstat(rs), "fwd": dstat(rs, "fw")}
    if len(rs) < 2:
        O["pass"] = False; return O
    mid = rs[len(rs) // 2]["t"]
    O["h1"] = dstat([r for r in rs if r["t"] < mid]); O["h2"] = dstat([r for r in rs if r["t"] >= mid])
    contrib = defaultdict(float)
    for r in rs:
        contrib[r["trader"]] += r["rv"]
    top = max(contrib, key=contrib.get)
    O["top_trader"] = [top, round(contrib[top], 1), sum(1 for r in rs if r["trader"] == top)]
    O["ex_top"] = dstat([r for r in rs if r["trader"] != top])
    O["traders"] = len(contrib)
    O["by_src"] = {s: dstat([r for r in rs if r["src"] == s]) for s in sorted({r["src"] for r in rs})}
    a = O["all"]
    O["pass"] = bool(a["n"] >= 150 and (a.get("t_day") or 0) >= 2.5 and O["h1"].get("mean", -1) > 0 and O["h2"].get("mean", -1) > 0
                     and O["ex_top"].get("mean", -1) > 0)
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    Dd = F.decisions(F.trips(db))
    allx = []
    for tr, mine in Dd.items():
        if len(mine) < 20:
            continue
        mark(mine)
        allx += [e for e in mine if any(e[v] for v in VARIANTS)]
    print(f"decisions selected by any variant: {len(allx)}, traders {len({e['trader'] for e in allx})}", flush=True)
    syms = defaultdict(list)
    for e in allx:
        syms[F.ALIAS.get(e["coin"], e["coin"]) + "USDT"].append(e)
    res, miss = [], 0
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = B.load_sym(sym, min(e["open_t"] for e in es) - H, max(e["close_t"] for e in es) + H)
        if n % 20 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        if not S:
            miss += len(es); continue
        for e in es:
            opp = "SHORT" if e["side"] == "LONG" else "LONG"
            rv = B.copy_ret(S, opp, e["open_t"] + DELAY, e["close_t"] + DELAY, STOP)
            fw = B.copy_ret(S, e["side"], e["open_t"] + DELAY, e["close_t"] + DELAY, STOP)
            if rv is None or fw is None:
                miss += 1; continue
            r = {"trader": e["trader"], "src": e["src"], "t": e["open_t"], "rv": rv, "fw": fw}
            for v in VARIANTS:
                r[v] = e[v]
            res.append(r)
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    res.sort(key=lambda r: r["t"])
    O = {"n": len(res), "missing": miss, "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)),
         "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    for v in VARIANTS:
        O[v] = judge([r for r in res if r[v]])
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=rev249 n::{O['n']} copies {O['from']} → {O['to']}, missing {miss}")
    for v in VARIANTS:
        q = O[v]
        print(f"::notice title=rev249 {v}::rev {q['all']} | fwd {q['fwd'].get('mean')} | h1 {q.get('h1', {}).get('mean')} h2 {q.get('h2', {}).get('mean')} | "
              f"ex_top {q.get('ex_top', {}).get('mean')} top {q.get('top_trader')} traders {q.get('traders')} | pass {q['pass']}")
        print(f"::notice title=rev249 {v} src::" + json.dumps({s: [x.get('n'), x.get('mean'), x.get('t_day')] for s, x in q.get('by_src', {}).items()}))


if __name__ == "__main__":
    main()
