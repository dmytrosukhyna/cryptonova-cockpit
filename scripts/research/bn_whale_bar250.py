"""Backlog 250 (22.11.2026): does each condition of the live selection bar carry information about FUTURE copy results?
Side result of backlog 249: copies «as is» of traders whose 60-day result before entry was NEGATIVE made +1.00 % a copy.
Paper calculation only; selection rules are changed only by the owner.

Data and copy mechanics exactly as backlog 245/249 (bn_whale_flip245.py): ws_trips + data/whale_trades «closed», decision = one
trader's entries in the same coin + side within 4 h; copy = first Binance USD-M 5m bar ≥ whale open + 2 min, exit ≥ whale close
+ 2 min, stop −16 % on our side, costs 0.10 % a round trip + funding (B.copy_ret).

Pre-registered (written before any result was seen):
- walk-forward, no look-ahead: for a decision opened at t, the trader's trailing stats use only HIS decisions CLOSED in the 60 days
  before t: n, win share, PF and sum of his own result after 0.10 % costs, and «copy test» = mean of OUR delayed copies of those
  closed decisions.
- universe U = decisions whose trader has ≥ 5 closed decisions in the trailing 60 days; U20 = those with ≥ 20.
- 5 single-condition tests (pass group minus fail group, mean copy %):
  C_n (n ≥ 20 vs 5–19, in U); C_win (win ≥ 70 %), C_pf (PF ≥ 2), C_pnl (sum > 0), C_copy (copy test > 0) — each in U20;
  plus the full bar (all five) vs the rest of U20 (reference, 6th test).
- metric: difference of means; significance by a DAY-clustered t: for each UTC day with both groups present,
  d = mean(pass) − mean(fail) that day; t over those days.
- a condition «carries information» if diff > 0, day t ≥ 2.6 (Bonferroni for 6 tests, one-sided ≈ 0.0083 → 2.4, rounded up),
  and diff > 0 in BOTH halves of time. A condition with diff ≤ 0 and t ≤ −2.6 is reported as «inverse».
Usage: bn_whale_bar250.py DB OUT.json"""
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
CONDS = ["C_n", "C_win", "C_pf", "C_pnl", "C_copy", "BAR"]


def trail(mine):
    """attach trailing-60d stats (closed before open) to each decision; needs e['fw'] (our copy) where available"""
    closed = sorted((e["close_t"], e["wret"] - B.COST if e["wret"] is not None else None, e.get("fw")) for e in mine)
    ct = [c[0] for c in closed]
    for e in mine:
        lo, hi = bisect_left(ct, e["open_t"] - 60 * D), bisect_left(ct, e["open_t"])
        xs = [c[1] for c in closed[lo:hi] if c[1] is not None]
        cs = [c[2] for c in closed[lo:hi] if c[2] is not None]
        n = len(xs)
        pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
        e["n60"] = n
        e["win60"] = sum(x > 0 for x in xs) / n if n else None
        e["pf60"] = (pos / neg if neg else 99.0) if n else None
        e["sum60"] = sum(xs) if n else None
        e["copy60"] = st.mean(cs) if len(cs) >= 5 else None


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "mean": round(m, 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None}


def diff(rows):
    """rows: (t, group bool, ret). pass − fail; day-clustered t over days with both groups"""
    P = [r[2] for r in rows if r[1]]; Q = [r[2] for r in rows if not r[1]]
    out = {"pass": stat(P), "fail": stat(Q)}
    if len(P) < 2 or len(Q) < 2:
        out["diff"] = None; return out
    out["diff"] = round(st.mean(P) - st.mean(Q), 3)
    days = defaultdict(lambda: ([], []))
    for t, g, r in rows:
        days[t // D][0 if g else 1].append(r)
    ds = [st.mean(a) - st.mean(b) for a, b in days.values() if a and b]
    out["days"] = len(ds)
    out["t_day"] = round(st.mean(ds) / (st.pstdev(ds) / math.sqrt(len(ds))), 2) if len(ds) > 2 and st.pstdev(ds) else None
    return out


def judge(rows):
    rows = sorted(rows, key=lambda r: r[0])
    O = diff(rows)
    if len(rows) > 10:
        mid = rows[len(rows) // 2][0]
        O["h1"] = diff([r for r in rows if r[0] < mid]); O["h2"] = diff([r for r in rows if r[0] >= mid])
        O["h1"] = {k: O["h1"].get(k) for k in ("diff", "t_day")} | {"np": O["h1"]["pass"]["n"], "nf": O["h1"]["fail"]["n"]}
        O["h2"] = {k: O["h2"].get(k) for k in ("diff", "t_day")} | {"np": O["h2"]["pass"]["n"], "nf": O["h2"]["fail"]["n"]}
    d, t = O.get("diff"), O.get("t_day") or 0
    both = (O.get("h1", {}).get("diff") or -1) > 0 and (O.get("h2", {}).get("diff") or -1) > 0
    O["verdict"] = "info" if (d is not None and d > 0 and t >= 2.6 and both) else ("inverse" if (d is not None and d < 0 and t <= -2.6) else "no")
    return O


def main():
    db, out = sys.argv[1], sys.argv[2]
    Dd = F.decisions(F.trips(db))
    allx = [e for mine in Dd.values() if len(mine) >= 6 for e in mine]
    print(f"decisions: {len(allx)}, traders {len({e['trader'] for e in allx})}", flush=True)
    syms = defaultdict(list)
    for e in allx:
        syms[F.ALIAS.get(e["coin"], e["coin"]) + "USDT"].append(e)
    miss = 0
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = B.load_sym(sym, min(e["open_t"] for e in es) - H, max(e["close_t"] for e in es) + H)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        for e in es:
            e["fw"] = B.copy_ret(S, e["side"], e["open_t"] + DELAY, e["close_t"] + DELAY, STOP) if S else None
            miss += e["fw"] is None
    for mine in Dd.values():
        if len(mine) >= 6:
            trail(mine)
    U = [e for e in allx if e.get("fw") is not None and e["n60"] >= 5]
    U20 = [e for e in U if e["n60"] >= 20]
    if not U20:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    g = {
        "C_win": lambda e: e["win60"] >= 0.70, "C_pf": lambda e: e["pf60"] >= 2.0, "C_pnl": lambda e: e["sum60"] > 0,
        "C_copy": lambda e: (e["copy60"] or -1) > 0,
    }
    bar = lambda e: all(f(e) for f in g.values())
    O = {"n_all": len(allx), "missing": miss, "U": len(U), "U20": len(U20), "traders_U20": len({e["trader"] for e in U20}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(min(e["open_t"] for e in U) / 1000)),
         "to": time.strftime("%Y-%m-%d", time.gmtime(max(e["open_t"] for e in U) / 1000))}
    O["C_n"] = judge([(e["open_t"], e["n60"] >= 20, e["fw"]) for e in U])
    for k, f in g.items():
        O[k] = judge([(e["open_t"], f(e), e["fw"]) for e in U20])
    O["BAR"] = judge([(e["open_t"], bar(e), e["fw"]) for e in U20])
    O["BAR_by_src"] = {s: judge([(e["open_t"], bar(e), e["fw"]) for e in U20 if e["src"] == s]) for s in sorted({e["src"] for e in U20})}
    O["U20_all"] = stat([e["fw"] for e in U20])
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=bar250 n::all {O['n_all']} U {O['U']} U20 {O['U20']} traders {O['traders_U20']} {O['from']} → {O['to']}, missing {miss}, U20 mean {O['U20_all']}")
    for k in CONDS:
        q = O[k]
        print(f"::notice title=bar250 {k}::pass {q['pass']} | fail {q['fail']} | diff {q.get('diff')} t_day {q.get('t_day')} days {q.get('days')} | "
              f"h1 {q.get('h1')} h2 {q.get('h2')} | {q['verdict']}")
    print("::notice title=bar250 BAR src::" + json.dumps({s: [x["pass"]["n"], x["pass"].get("mean"), x["fail"]["n"], x["fail"].get("mean"), x.get("diff"), x.get("t_day")]
                                                            for s, x in O["BAR_by_src"].items()}))


if __name__ == "__main__":
    main()
