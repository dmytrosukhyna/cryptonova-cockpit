"""Backlog 253 (23.11.2026): RANKING instead of a threshold. Side result of backlog 250: no single bar condition predicts the mean
future copy. Question: if at every lock review (every 3 days) we simply took the 7 traders with ≥ 20 decisions that rank best by
«mean delayed copy over the trailing 60 days», would their NEXT 3 days of copies beat the bar's picks?
Paper calculation only; the selection rules are changed only by the owner.

Data and copy mechanics exactly as backlog 245/249/250 (bn_whale_flip245.py / bn_whale_bar250.py): decision = one trader's entries
in the same coin + side within 4 h; copy = Binance USD-M 5m bars, +2 min delay, stop −16 %, 0.10 % round trip + funding.

Pre-registered (written before any result was seen):
- review points T every 3 days from the first day with ≥ 30 eligible traders; at T a trader's stats use only his decisions CLOSED
  in [T − 60 d, T): n, win, PF, sum, copy60 = mean of our delayed copies of those closed decisions. Eligible: n ≥ 20.
- forward window: decisions of eligible traders OPENED in [T, T + 3 d); their copy result is the outcome (closes may be later).
- groups at each T: BAR = eligible traders passing the live bar (win ≥ 70 %, PF ≥ 2, sum > 0, copy60 > 0) — all of them;
  BAR7 = the 7 best BAR traders by copy60 (closest to a 7-seat lock); R7_copy = top 7 eligible by copy60 (primary);
  R7_sum = top 7 by sum60; R7_pf = top 7 by PF (2 secondary variants). ALL = every eligible trader (baseline).
- metric: mean copy % per decision; the ranking «beats the bar» if R − BAR7 > 0 with a day-clustered t (days where both groups
  have copies) ≥ 2.4 (Bonferroni for 3 ranking variants, one-sided 1 %) AND > 0 in both halves of time. Same reported vs ALL.
- also reported: per-review mean (each review equal weight), share of reviews where R beats BAR7, overlap R7 ∩ BAR7.
Usage: bn_whale_rank253.py DB OUT.json"""
import json, math, os, statistics as st, sys, time
from bisect import bisect_left
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F

B.COST = 0.10
H = F.H
D = 24 * H
STEP = 3 * D
GROUPS = ["ALL", "BAR", "BAR7", "R7_copy", "R7_sum", "R7_pf"]


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.pstdev(xs)
    return {"n": len(xs), "mean": round(m, 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3),
            "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None}


def pair(rows, a, b):
    """rows: (open_t, set_of_groups, ret). a − b by mean; day-clustered t over days where both have copies"""
    A = [r for r in rows if a in r[1]]; Bq = [r for r in rows if b in r[1]]
    if len(A) < 2 or len(Bq) < 2:
        return {"diff": None, "na": len(A), "nb": len(Bq)}
    out = {"diff": round(st.mean(r[2] for r in A) - st.mean(r[2] for r in Bq), 3), "na": len(A), "nb": len(Bq)}
    da, db = defaultdict(list), defaultdict(list)
    for r in A:
        da[r[0] // D].append(r[2])
    for r in Bq:
        db[r[0] // D].append(r[2])
    ds = [st.mean(da[k]) - st.mean(db[k]) for k in da if k in db]
    out["days"] = len(ds)
    out["t_day"] = round(st.mean(ds) / (st.pstdev(ds) / math.sqrt(len(ds))), 2) if len(ds) > 2 and st.pstdev(ds) else None
    return out


def judge(rows, a, b, tmin=2.4):
    rows = sorted(rows, key=lambda r: r[0])
    O = pair(rows, a, b)
    if len(rows) > 10:
        mid = rows[len(rows) // 2][0]
        O["h1"] = pair([r for r in rows if r[0] < mid], a, b)
        O["h2"] = pair([r for r in rows if r[0] >= mid], a, b)
    d, t = O.get("diff"), O.get("t_day") or 0
    both = ((O.get("h1") or {}).get("diff") or -1) > 0 and ((O.get("h2") or {}).get("diff") or -1) > 0
    O["verdict"] = "better" if (d is not None and d > 0 and t >= tmin and both) else ("worse" if (d is not None and d < 0 and t <= -tmin) else "no")
    return O


def snap(mine, T):
    """trader stats from decisions closed in [T-60d, T)"""
    xs, cs = [], []
    for e in mine:
        if T - 60 * D <= e["close_t"] < T:
            if e["wret"] is not None:
                xs.append(e["wret"] - B.COST)
            if e.get("fw") is not None:
                cs.append(e["fw"])
    n = len(xs)
    if n < 20:
        return None
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": n, "win": sum(x > 0 for x in xs) / n, "pf": pos / neg if neg else 99.0, "sum": sum(xs),
            "copy": st.mean(cs) if len(cs) >= 5 else None}


def main():
    db, out = sys.argv[1], sys.argv[2]
    Dd = F.decisions(F.trips(db))
    Dd = {k: v for k, v in Dd.items() if len(v) >= 6}
    allx = [e for mine in Dd.values() for e in mine]
    print(f"decisions: {len(allx)}, traders {len(Dd)}", flush=True)
    syms = defaultdict(list)
    for e in allx:
        syms[F.ALIAS.get(e["coin"], e["coin"]) + "USDT"].append(e)
    miss = 0
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = B.load_sym(sym, min(e["open_t"] for e in es) - H, max(e["close_t"] for e in es) + H)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        for e in es:
            e["fw"] = B.copy_ret(S, e["side"], e["open_t"] + F.DELAY, e["close_t"] + F.DELAY, F.STOP) if S else None
            miss += e["fw"] is None
    t0 = (min(e["open_t"] for e in allx) // D) * D
    t_end = max(e["open_t"] for e in allx)
    rows, reviews = [], []
    T = t0 + 20 * D
    while T < t_end:
        stats = {tr: s for tr, mine in Dd.items() if (s := snap(mine, T))}
        if len(stats) >= 30:
            withc = {tr: s for tr, s in stats.items() if s["copy"] is not None}
            bar = {tr for tr, s in withc.items() if s["win"] >= 0.70 and s["pf"] >= 2.0 and s["sum"] > 0 and s["copy"] > 0}
            top = lambda pool, key: set(sorted(pool, key=lambda tr: -withc[tr][key])[:7])  # noqa: E731
            G = {"ALL": set(stats), "BAR": bar, "BAR7": top(bar, "copy"), "R7_copy": top(withc, "copy"), "R7_sum": top(withc, "sum"),
                 "R7_pf": top(withc, "pf")}
            rv = {"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "eligible": len(stats), "bar": len(bar),
                  "overlap_R7_BAR7": len(G["R7_copy"] & G["BAR7"])}
            per = defaultdict(list)
            for tr in stats:
                for e in Dd[tr]:
                    if T <= e["open_t"] < T + STEP and e.get("fw") is not None:
                        gs = {g for g, m in G.items() if tr in m}
                        rows.append((e["open_t"], gs, e["fw"], tr))
                        for g in gs:
                            per[g].append(e["fw"])
            rv.update({g: [len(per[g]), round(st.mean(per[g]), 3) if per[g] else None] for g in GROUPS})
            reviews.append(rv)
        T += STEP
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    O = {"n_all": len(allx), "missing": miss, "reviews": len(reviews), "rows": len(rows),
         "from": reviews[0]["T"], "to": reviews[-1]["T"], "groups": {}, "vs_BAR7": {}, "vs_ALL": {}, "review_level": {}}
    for g in GROUPS:
        O["groups"][g] = stat([r[2] for r in rows if g in r[1]]) | {"traders": len({r[3] for r in rows if g in r[1]})}
    for g in ["R7_copy", "R7_sum", "R7_pf", "BAR"]:
        O["vs_BAR7"][g] = judge(rows, g, "BAR7")
    for g in ["BAR", "BAR7", "R7_copy", "R7_sum", "R7_pf"]:
        O["vs_ALL"][g] = judge(rows, g, "ALL")
    for g in ["R7_copy", "R7_sum", "R7_pf"]:
        both = [(r[g][1], r["BAR7"][1]) for r in reviews if r[g][1] is not None and r["BAR7"][1] is not None]
        O["review_level"][g] = {"reviews": len(both), "beats_BAR7": sum(a > b for a, b in both),
                                "mean_R": round(st.mean(a for a, _ in both), 3) if both else None,
                                "mean_BAR7": round(st.mean(b for _, b in both), 3) if both else None}
    O["overlap_mean"] = round(st.mean(r["overlap_R7_BAR7"] for r in reviews), 2)
    O["reviews_detail"] = reviews
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=rank253 n::decisions {O['n_all']} missing {miss} reviews {O['reviews']} {O['from']}→{O['to']} rows {O['rows']} overlap R7∩BAR7 {O['overlap_mean']}")
    print("::notice title=rank253 groups::" + json.dumps(O["groups"]))
    for g, q in O["vs_BAR7"].items():
        print(f"::notice title=rank253 {g} vs BAR7::diff {q.get('diff')} t_day {q.get('t_day')} days {q.get('days')} na {q.get('na')} nb {q.get('nb')} | "
              f"h1 {(q.get('h1') or {}).get('diff')} h2 {(q.get('h2') or {}).get('diff')} | {q['verdict']}")
    for g, q in O["vs_ALL"].items():
        print(f"::notice title=rank253 {g} vs ALL::diff {q.get('diff')} t_day {q.get('t_day')} | h1 {(q.get('h1') or {}).get('diff')} h2 {(q.get('h2') or {}).get('diff')} | {q['verdict']}")
    print("::notice title=rank253 reviews::" + json.dumps(O["review_level"]))


if __name__ == "__main__":
    main()
