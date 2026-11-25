"""Backlog 263 (25.11.2026): the weak bottom quintile INSIDE the selection bar. Side result of backlog 255: among traders passing the
bar proxy (win ≥ 70 %, PF ≥ 2, sum > 0, copy60 > 0, ≥ 20 decisions in 60 d) the bottom quintile by the 60-day delayed-copy test gave
−0.30 % per copy (1 411 copies, 36 traders) vs +0.57…+1.11 % for Q2–Q5. Strict check before any argument to the owner for a bar
«copy test ≥ median of passers» instead of «> 0». Paper calculation only; the selection bar is changed only by the owner.

Data and copy mechanics exactly as backlog 245/250/253/255 (bn_whale_curse255.py): decision = one trader's entries in the same
coin + side within 4 h; copy = Binance USD-M 5m bars, +2 min delay, stop −16 %, 0.10 % round trip + funding; reviews every 3 days,
trader stats only from decisions CLOSED in [T − 60 d, T), forward = decisions OPENED in [T, T + 3 d).

Pre-registered (written before any result was seen):
- PRIMARY (one test): bar pool split into quintiles by copy60 at each review; Q2–Q5 vs Q1, mean copy % per decision, day-clustered t.
  «Q1 weaker confirmed» if Q2–Q5 − Q1 > 0 with t_day ≥ 2.0 AND > 0 in both halves of time.
- SECONDARY (Bonferroni over 2 → t ≥ 2.24): the candidate rule itself — above-median copy60 within the bar (HI) vs below (LO).
- descriptive: per source (bn / okx / hl) Q1 vs rest; medians; Q1 without its single best and worst trader; per-review sum of
  copies (risk units) for «all passers» vs «only HI» — the rule halves the pool, so the total matters, not only the mean;
  the copy60 value at the median boundary (median over reviews) as the candidate threshold.
Usage: bn_whale_bq263.py DB OUT.json"""
import json, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F
import bn_whale_rank253 as R
from bn_whale_curse255 import buckets

B.COST = 0.10
D = R.D
STEP = R.STEP


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
        S = B.load_sym(sym, min(e["open_t"] for e in es) - F.H, max(e["close_t"] for e in es) + F.H)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        for e in es:
            e["fw"] = B.copy_ret(S, e["side"], e["open_t"] + F.DELAY, e["close_t"] + F.DELAY, F.STOP) if S else None
            miss += e["fw"] is None
    t0 = (min(e["open_t"] for e in allx) // D) * D
    t_end = max(e["open_t"] for e in allx)
    rows, reviews, bounds = [], [], []
    T = t0 + 20 * D
    while T < t_end:
        stats = {tr: s for tr, mine in Dd.items() if (s := R.snap(mine, T))}
        bar = {tr: s["copy"] for tr, s in stats.items() if s["copy"] is not None and s["win"] >= 0.70 and s["pf"] >= 2.0
               and s["sum"] > 0 and s["copy"] > 0}
        if len(bar) >= 10:
            quin = buckets(bar, 5)
            med = st.median(bar.values())
            bounds.append(med)
            sums = {"ALL": 0.0, "HI": 0.0}
            nrv = 0
            for tr in bar:
                src = Dd[tr][0]["src"]
                q = quin[tr]
                hi = bar[tr] >= med
                for e in Dd[tr]:
                    if T <= e["open_t"] < T + STEP and e.get("fw") is not None:
                        gs = {f"Q{q}", "ALL", "HI" if hi else "LO", "Q1" if q == 1 else "REST",
                              f"{src}:" + ("Q1" if q == 1 else "REST"), f"{src}:" + ("HI" if hi else "LO")}
                        rows.append((e["open_t"], gs, e["fw"], tr))
                        sums["ALL"] += e["fw"]
                        if hi:
                            sums["HI"] += e["fw"]
                        nrv += 1
            reviews.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "bar": len(bar), "median_copy60": round(med, 3),
                            "rows": nrv, "sum_all": round(sums["ALL"], 2), "sum_hi": round(sums["HI"], 2)})
        T += STEP
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return

    def g(name, rr=None):
        rr = rows if rr is None else rr
        xs = [r[2] for r in rr if name in r[1]]
        o = R.stat(xs) | {"traders": len({r[3] for r in rr if name in r[1]})}
        if xs:
            o["median"] = round(st.median(xs), 3)
        return o

    O = {"n_all": len(allx), "missing": miss, "reviews": len(reviews), "rows": len(rows), "from": reviews[0]["T"],
         "to": reviews[-1]["T"], "median_bound_median": round(st.median(bounds), 3)}
    O["quintiles"] = {f"Q{i}": g(f"Q{i}") for i in range(1, 6)}
    O["primary_REST_vs_Q1"] = R.judge(rows, "REST", "Q1", tmin=2.0)
    O["secondary_HI_vs_LO"] = R.judge(rows, "HI", "LO", tmin=2.24)
    O["HI"], O["LO"], O["ALL"] = g("HI"), g("LO"), g("ALL")
    # robustness: Q1 without its single best and single worst trader by total contribution
    q1 = defaultdict(float)
    for r in rows:
        if "Q1" in r[1]:
            q1[r[3]] += r[2]
    if len(q1) >= 3:
        drop = {max(q1, key=q1.get), min(q1, key=q1.get)}
        O["Q1_drop_best_worst"] = g("Q1", [r for r in rows if r[3] not in drop])
    O["by_src"] = {}
    for src in sorted({Dd[tr][0]["src"] for tr in {r[3] for r in rows}}):
        O["by_src"][src] = {"Q1": g(f"{src}:Q1"), "REST": g(f"{src}:REST"),
                            "REST_vs_Q1": R.judge(rows, f"{src}:REST", f"{src}:Q1", tmin=2.0),
                            "HI_vs_LO": R.judge(rows, f"{src}:HI", f"{src}:LO", tmin=2.24)}
    sa = [rv["sum_all"] for rv in reviews]
    sh = [rv["sum_hi"] for rv in reviews]
    O["per_review_sum"] = {"all_total": round(sum(sa), 1), "hi_total": round(sum(sh), 1),
                           "hi_better_reviews": sum(h > a for h, a in zip(sh, sa)), "n": len(sa)}
    O["reviews_detail"] = reviews
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    p, s = O["primary_REST_vs_Q1"], O["secondary_HI_vs_LO"]
    print(f"::notice title=bq263 n::decisions {O['n_all']} missing {miss} reviews {O['reviews']} {O['from']}→{O['to']} rows {O['rows']} "
          f"median copy60 boundary {O['median_bound_median']}")
    print("::notice title=bq263 quintiles::" + json.dumps({k: [v.get('n'), v.get('mean'), v.get('median'), v.get('t'), v.get('traders')]
                                                         for k, v in O['quintiles'].items()}))
    print(f"::notice title=bq263 PRIMARY REST vs Q1::diff {p.get('diff')} t_day {p.get('t_day')} days {p.get('days')} na {p.get('na')} nb {p.get('nb')} | "
          f"h1 {(p.get('h1') or {}).get('diff')} h2 {(p.get('h2') or {}).get('diff')} | {p['verdict']}")
    print(f"::notice title=bq263 SECONDARY HI vs LO::HI {[O['HI'].get('n'), O['HI'].get('mean')]} LO {[O['LO'].get('n'), O['LO'].get('mean')]} "
          f"diff {s.get('diff')} t_day {s.get('t_day')} | h1 {(s.get('h1') or {}).get('diff')} h2 {(s.get('h2') or {}).get('diff')} | {s['verdict']}")
    print(f"::notice title=bq263 totals::per-review sum all {O['per_review_sum']} | Q1 drop best/worst "
          f"{[O.get('Q1_drop_best_worst', {}).get(k) for k in ('n', 'mean', 'traders')]}")
    for src, v in O["by_src"].items():
        j, h = v["REST_vs_Q1"], v["HI_vs_LO"]
        print(f"::notice title=bq263 src {src}::Q1 {[v['Q1'].get('n'), v['Q1'].get('mean')]} REST {[v['REST'].get('n'), v['REST'].get('mean')]} "
              f"| diff {j.get('diff')} t_day {j.get('t_day')} h1 {(j.get('h1') or {}).get('diff')} h2 {(j.get('h2') or {}).get('diff')} "
              f"| HI-LO {h.get('diff')} t {h.get('t_day')}")


if __name__ == "__main__":
    main()
