"""Backlog 255 (23.11.2026): «winner's curse». Side result of backlog 253: the 7 traders with the best mean delayed copy over the
trailing 60 days were WORSE than the pool over the next 3 days. Question: is this monotonic across deciles of copy60, does it hold
per source (Binance / OKX / HL), and does the top decile lose in both halves of time? If so, a cap «copy test not above X» could be
offered to the owner as protection against lucky streaks. Paper calculation only; selection rules are changed only by the owner.

Data and copy mechanics exactly as backlog 245/250/253 (bn_whale_flip245.py / bn_whale_rank253.py): decision = one trader's entries
in the same coin + side within 4 h; copy = Binance USD-M 5m bars, +2 min delay, stop −16 %, 0.10 % round trip + funding.

Pre-registered (written before any result was seen):
- review points T every 3 days (same grid as backlog 253); at T a trader's stats use only his decisions CLOSED in [T − 60 d, T);
  eligible: n ≥ 20 and ≥ 5 copies (copy60 defined).
- at each T eligible traders are split into 10 deciles by copy60 (rank within that T). Forward: decisions OPENED in [T, T + 3 d).
- primary test: TOP decile (D10) vs the middle (D2–D9), mean copy % per decision, day-clustered t. «Curse confirmed» if
  D10 − mid < 0 with t_day ≤ −2.0 AND < 0 in both halves (one test, no correction needed; secondary splits are descriptive).
- also: the same within the BAR pool only (win ≥ 70 %, PF ≥ 2, sum > 0, copy60 > 0; quintiles because the pool is smaller);
  per source (bn / okx / hl) D10 vs mid; Spearman of decile index vs decile mean (monotonicity); the copy60 value at the D10
  boundary (median over reviews) as the candidate X.
Usage: bn_whale_curse255.py DB OUT.json"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F
import bn_whale_rank253 as R

B.COST = 0.10
D = R.D
STEP = R.STEP


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    rk = lambda v: {i: r for r, i in enumerate(sorted(range(n), key=lambda i: v[i]))}  # noqa: E731
    a, b = rk(xs), rk(ys)
    return round(1 - 6 * sum((a[i] - b[i]) ** 2 for i in range(n)) / (n * (n * n - 1)), 3)


def buckets(pool, k):
    """pool: {trader: copy60}; returns {trader: bucket 1..k} by rank (k = top)"""
    order = sorted(pool, key=lambda tr: pool[tr])
    n = len(order)
    return {tr: min(k, 1 + (i * k) // n) for i, tr in enumerate(order)}


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
        if len(stats) >= 30:
            withc = {tr: s["copy"] for tr, s in stats.items() if s["copy"] is not None}
            dec = buckets(withc, 10)
            bar = {tr: withc[tr] for tr, s in stats.items() if s["copy"] is not None and s["win"] >= 0.70 and s["pf"] >= 2.0
                   and s["sum"] > 0 and s["copy"] > 0}
            quin = buckets(bar, 5) if len(bar) >= 10 else {}
            top = sorted(withc.values())
            bounds.append(top[(9 * len(top)) // 10])
            nrv = 0
            for tr in withc:
                src = Dd[tr][0]["src"]
                for e in Dd[tr]:
                    if T <= e["open_t"] < T + STEP and e.get("fw") is not None:
                        gs = {f"D{dec[tr]}", "ALL", f"{src}:D{dec[tr]}"}
                        gs.add("TOP" if dec[tr] == 10 else ("MID" if dec[tr] >= 2 else "BOT"))
                        gs.add(f"{src}:" + ("TOP" if dec[tr] == 10 else ("MID" if dec[tr] >= 2 else "BOT")))
                        if tr in quin:
                            gs.add(f"BQ{quin[tr]}")
                            gs.add("BTOP" if quin[tr] == 5 else "BREST")
                        rows.append((e["open_t"], gs, e["fw"], tr))
                        nrv += 1
            reviews.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "eligible": len(withc), "bar": len(bar),
                            "d10_bound": round(bounds[-1], 3), "rows": nrv})
        T += STEP
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return

    def g(name):
        xs = [r[2] for r in rows if name in r[1]]
        return R.stat(xs) | {"traders": len({r[3] for r in rows if name in r[1]})}

    O = {"n_all": len(allx), "missing": miss, "reviews": len(reviews), "rows": len(rows), "from": reviews[0]["T"],
         "to": reviews[-1]["T"], "d10_bound_median": round(st.median(bounds), 3)}
    O["deciles"] = {f"D{i}": g(f"D{i}") for i in range(1, 11)}
    O["spearman_dec"] = spearman(list(range(1, 11)), [O["deciles"][f"D{i}"].get("mean") or 0 for i in range(1, 11)])
    O["primary_TOP_vs_MID"] = R.judge(rows, "TOP", "MID", tmin=2.0)
    O["BOT_vs_MID"] = R.judge(rows, "BOT", "MID", tmin=2.0)
    O["bar_quintiles"] = {f"BQ{i}": g(f"BQ{i}") for i in range(1, 6)}
    O["bar_spearman"] = spearman(list(range(1, 6)), [O["bar_quintiles"][f"BQ{i}"].get("mean") or 0 for i in range(1, 6)])
    O["bar_TOP_vs_REST"] = R.judge(rows, "BTOP", "BREST", tmin=2.0)
    O["by_src"] = {}
    for src in sorted({Dd[tr][0]["src"] for tr in {r[3] for r in rows}}):
        O["by_src"][src] = {"TOP": g(f"{src}:TOP"), "MID": g(f"{src}:MID"), "BOT": g(f"{src}:BOT"),
                            "TOP_vs_MID": R.judge(rows, f"{src}:TOP", f"{src}:MID", tmin=2.0)}
    O["reviews_detail"] = reviews
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    p = O["primary_TOP_vs_MID"]
    print(f"::notice title=curse255 n::decisions {O['n_all']} missing {miss} reviews {O['reviews']} {O['from']}→{O['to']} rows {O['rows']} "
          f"D10 bound median {O['d10_bound_median']}")
    print("::notice title=curse255 deciles::" + json.dumps({k: [v.get('n'), v.get('mean'), v.get('t'), v.get('traders')] for k, v in O['deciles'].items()})
          + f" spearman {O['spearman_dec']}")
    print(f"::notice title=curse255 PRIMARY TOP vs MID::diff {p.get('diff')} t_day {p.get('t_day')} days {p.get('days')} na {p.get('na')} nb {p.get('nb')} | "
          f"h1 {(p.get('h1') or {}).get('diff')} h2 {(p.get('h2') or {}).get('diff')} | {p['verdict']}")
    b = O["BOT_vs_MID"]
    print(f"::notice title=curse255 BOT vs MID::diff {b.get('diff')} t_day {b.get('t_day')} | h1 {(b.get('h1') or {}).get('diff')} h2 {(b.get('h2') or {}).get('diff')}")
    q = O["bar_TOP_vs_REST"]
    print("::notice title=curse255 bar quintiles::" + json.dumps({k: [v.get('n'), v.get('mean'), v.get('traders')] for k, v in O['bar_quintiles'].items()})
          + f" spearman {O['bar_spearman']} | BTOP vs BREST diff {q.get('diff')} t_day {q.get('t_day')} h1 {(q.get('h1') or {}).get('diff')} h2 {(q.get('h2') or {}).get('diff')} {q['verdict']}")
    for src, v in O["by_src"].items():
        j = v["TOP_vs_MID"]
        print(f"::notice title=curse255 src {src}::TOP {[v['TOP'].get('n'), v['TOP'].get('mean')]} MID {[v['MID'].get('n'), v['MID'].get('mean')]} "
              f"BOT {[v['BOT'].get('n'), v['BOT'].get('mean')]} | diff {j.get('diff')} t_day {j.get('t_day')} h1 {(j.get('h1') or {}).get('diff')} h2 {(j.get('h2') or {}).get('diff')}")


if __name__ == "__main__":
    main()
