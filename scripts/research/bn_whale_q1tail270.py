"""Backlog 270 (26.11.2026): where does the tail of the bottom quintile (Q1) inside the selection bar come from, and does a
closer stop cure it for Q1 only? Side result of backlog 263: Q1 by the 60-day copy test (copy60) had a positive median copy
(+0.40 %) but a negative mean (−0.30 %) — the minus comes from the tail. Paper calculation only; real rules are not touched.

Data, reviews, bar proxy and quintiles exactly as bn_whale_bq263.py (decision = one trader's entries in the same coin + side
within 4 h; copy = Binance USD-M 5m bars, +2 min, 0.10 % round trip + funding; reviews every 3 days, trader stats from decisions
closed in [T − 60 d, T), forward = decisions opened in [T, T + 3 d)). copy60 itself always uses the −16 % stop (as live).

Pre-registered (written before any result was seen):
- DESCRIPTIVE decomposition Q1 vs Q2–Q5: share of copies stopped at −16 %, mean loss of a stopped copy, mean of the copies that
  were not stopped, median trader leverage (ws_trips.lever where known), share of BTC/ETH copies, top coins.
- CANDIDATE (one test, out of sample by time): stop S for Q1 only at the SAME money at risk (size × 16/S, so a result is
  compared in risk units R = copy % / stop %). S is CHOSEN on the first half of time among {8, 10, 12} % by the best mean R of
  Q1 copies; then TESTED on the second half only: paired difference R(S) − R(16) over Q1 copies of the second half,
  day-clustered t ≥ 2.0 and > 0. Three variants are tried only on the first half (noted).
- Info (not a candidate): the same S applied to Q2–Q5 in the second half — if it helps everyone equally, it is not a Q1 cure.
Usage: bn_whale_q1tail270.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F
import bn_whale_rank253 as R
from bn_whale_curse255 import buckets

B.COST = 0.10
D = R.D
STEP = R.STEP
S0 = 16.0
ALT = (8.0, 10.0, 12.0)


def levers(db):
    out = {}
    try:
        c = sqlite3.connect(db)
        for r in c.execute("SELECT src, trader, id, lever FROM ws_trips WHERE close_t > 0"):
            if r[3]:
                out[(r[0], r[1], str(r[2]))] = float(r[3])
    except Exception as e:  # noqa: BLE001
        print(f"::warning::lever not read: {e}")
    return out


def tday(diffs):
    """diffs: [(open_t, x)] → mean, day-clustered t"""
    by = defaultdict(list)
    for t, x in diffs:
        by[t // D].append(x)
    ds = [st.mean(v) for v in by.values()]
    if len(ds) < 3 or not st.pstdev(ds):
        return {"n": len(diffs), "days": len(ds), "mean": round(st.mean(x for _, x in diffs), 4) if diffs else None, "t_day": None}
    return {"n": len(diffs), "days": len(ds), "mean": round(st.mean(x for _, x in diffs), 4),
            "t_day": round(st.mean(ds) / (st.pstdev(ds) / math.sqrt(len(ds))), 2)}


def main():
    db, out = sys.argv[1], sys.argv[2]
    lev = levers(db)
    Dd = F.decisions(F.trips(db))
    Dd = {k: v for k, v in Dd.items() if len(v) >= 6}
    allx = [e for mine in Dd.values() for e in mine]
    print(f"decisions: {len(allx)}, traders {len(Dd)}, levers {len(lev)}", flush=True)
    syms = defaultdict(list)
    for e in allx:
        syms[F.ALIAS.get(e["coin"], e["coin"]) + "USDT"].append(e)
        ls = [lev.get((x[0], x[1], str(x[2]))) for x in e["g"]]
        ls = [x for x in ls if x]
        e["lev"] = max(ls) if ls else None
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = B.load_sym(sym, min(e["open_t"] for e in es) - F.H, max(e["close_t"] for e in es) + F.H)
        if n % 25 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if S else 'none'}", flush=True)
        for e in es:
            a, b = e["open_t"] + F.DELAY, e["close_t"] + F.DELAY
            e["fw"] = B.copy_ret(S, e["side"], a, b, S0) if S else None
            e["alt"] = {s: B.copy_ret(S, e["side"], a, b, s) for s in ALT} if S and e["fw"] is not None else None
    t0 = (min(e["open_t"] for e in allx) // D) * D
    t_end = max(e["open_t"] for e in allx)
    rows = []   # (open_t, grp 'Q1'/'REST', e, trader)
    T, nrev = t0 + 20 * D, 0
    while T < t_end:
        stats = {tr: s for tr, mine in Dd.items() if (s := R.snap(mine, T))}
        bar = {tr: s["copy"] for tr, s in stats.items() if s["copy"] is not None and s["win"] >= 0.70 and s["pf"] >= 2.0
               and s["sum"] > 0 and s["copy"] > 0}
        if len(bar) >= 10:
            nrev += 1
            quin = buckets(bar, 5)
            for tr in bar:
                for e in Dd[tr]:
                    if T <= e["open_t"] < T + STEP and e.get("fw") is not None and e.get("alt"):
                        rows.append((e["open_t"], "Q1" if quin[tr] == 1 else "REST", e, tr))
        T += STEP
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    rows.sort(key=lambda r: r[0])
    mid = rows[len(rows) // 2][0]

    def desc(g):
        rr = [r for r in rows if r[1] == g]
        xs = [r[2]["fw"] for r in rr]
        stp = [x for x in xs if x <= -15.5]   # stop fill ≈ −16.1 % (costs + funding); natural closes this deep are rare
        ns = [x for x in xs if x not in stp]
        lv = [r[2]["lev"] for r in rr if r[2]["lev"]]
        coins = Counter(r[2]["coin"] for r in rr)
        big = sum(coins[c] for c in ("BTC", "ETH"))
        return {"n": len(xs), "traders": len({r[3] for r in rr}), "mean": round(st.mean(xs), 3), "median": round(st.median(xs), 3),
                "stop_share": round(len(stp) / len(xs), 4), "stop_mean": round(st.mean(stp), 2) if stp else None,
                "stop_sum_pct_of_total_loss": round(sum(stp) / sum(x for x in xs if x < 0), 3) if any(x < 0 for x in xs) else None,
                "nostop_mean": round(st.mean(ns), 3) if ns else None, "lev_median": round(st.median(lv), 1) if lv else None,
                "lev_known": len(lv), "lev_ge20": round(sum(x >= 20 for x in lv) / len(lv), 3) if lv else None,
                "btc_eth_share": round(big / len(xs), 3), "top_coins": coins.most_common(8),
                "short_share": round(sum(r[2]["side"] == "SHORT" for r in rr) / len(xs), 3),
                "hold_med_h": round(st.median((r[2]["close_t"] - r[2]["open_t"]) / 3.6e6 for r in rr), 1)}

    O = {"reviews": nrev, "rows": len(rows), "from": time.strftime("%Y-%m-%d", time.gmtime(rows[0][0] / 1000)),
         "to": time.strftime("%Y-%m-%d", time.gmtime(rows[-1][0] / 1000)), "mid": time.strftime("%Y-%m-%d", time.gmtime(mid / 1000)),
         "Q1": desc("Q1"), "REST": desc("REST")}
    # risk units R = copy % / stop %
    q1h1 = [r for r in rows if r[1] == "Q1" and r[0] < mid]
    q1h2 = [r for r in rows if r[1] == "Q1" and r[0] >= mid]
    reh2 = [r for r in rows if r[1] == "REST" and r[0] >= mid]
    h1 = {str(int(s)): round(st.mean(r[2]["alt"][s] / s for r in q1h1 if r[2]["alt"][s] is not None), 4) for s in ALT}
    h1["16"] = round(st.mean(r[2]["fw"] / S0 for r in q1h1), 4)
    best = max(ALT, key=lambda s: h1[str(int(s))])
    O["choice_h1"] = {"R_by_stop": h1, "chosen": best, "n": len(q1h1)}

    def paired(rr, s):
        return tday([(r[0], r[2]["alt"][s] / s - r[2]["fw"] / S0) for r in rr if r[2]["alt"][s] is not None])
    test = paired(q1h2, best)
    worst_a = min(r[2]["fw"] / S0 for r in q1h2) if q1h2 else None
    worst_w = min(r[2]["alt"][best] / best for r in q1h2 if r[2]["alt"][best] is not None) if q1h2 else None
    test |= {"R16": round(st.mean(r[2]["fw"] / S0 for r in q1h2), 4) if q1h2 else None,
             "Rs": round(st.mean(r[2]["alt"][best] / best for r in q1h2), 4) if q1h2 else None,
             "worst_R16": round(worst_a, 3) if worst_a is not None else None, "worst_Rs": round(worst_w, 3) if worst_w is not None else None}
    test["verdict"] = "pass" if (test.get("mean") or 0) > 0 and (test.get("t_day") or 0) >= 2.0 else "no"
    O["test_h2_Q1"] = test
    O["info_h2_REST"] = paired(reh2, best)
    O["info_all_stops_h2_Q1"] = {str(int(s)): paired(q1h2, s) for s in ALT}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    for g in ("Q1", "REST"):
        v = O[g]
        print(f"::notice title=q1tail270 {g}::" + json.dumps({k: v[k] for k in v if k != "top_coins"}, ensure_ascii=False))
        print(f"::notice title=q1tail270 {g} coins::{v['top_coins']}")
    print(f"::notice title=q1tail270 span::reviews {nrev} rows {len(rows)} {O['from']}→{O['to']} mid {O['mid']} choice {O['choice_h1']}")
    print(f"::notice title=q1tail270 TEST h2 Q1::stop {best} {json.dumps(test)}")
    print(f"::notice title=q1tail270 info::REST h2 {json.dumps(O['info_h2_REST'])} | Q1 h2 all stops {json.dumps(O['info_all_stops_h2_Q1'])}")


if __name__ == "__main__":
    main()
