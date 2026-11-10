"""Backlog 123 — «repeatability» of a Binance lead trader in the selection (10.11.2026, research only).

Question: our OKX account can only repeat decisions in coins that have a USDT swap on OKX. Binance traders now also trade
TradFi pairs (stocks/metals) and coins OKX lacks. Their 60-day record then partly comes from decisions we never copy.
Does it help to judge such a trader only by the decisions we can repeat?

Pre-registered (written before any result was looked at):
 Decisions, live bar, copy and book exactly as bn_whale_fresh.py / cohort_sim.simulate (prior 60 d: win >= 70 %, PF >= 2,
 >= 20 decisions, P&L > 0; top-10 by 60-day P&L of bar-passing traders; copy +2 min, stop -16 %, costs 0.11 % + funding;
 daily stop 3 %, max 15 open, one copy per coin). Only repeatable decisions (coin has an OKX USDT swap, current list) are copied.
 Base = live: bar and 60-day P&L from ALL prior decisions.
 Variant (one threshold, fixed in advance): if >= 50 % of the trader's prior-60-day decisions are NOT repeatable, the bar and the
 60-day P&L ranking use only his repeatable decisions (still >= 20 needed); otherwise as base.
 Passes only if: paired daily t (variant − base) >= 2.5 in BOTH time halves and the variant's worst day and max drawdown are not
 worse than 1.25 × the base's. 1 variant × 1 source (OKX/HL whales trade OKX/HL instruments — the problem is Binance's).
 Reported also: share of non-repeatable decisions by month, how many top-10 slots differ.
Usage: bn_whale_norepeat.py DB OUT.json
"""
import bisect, json, math, os, sqlite3, sys, urllib.request
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
import cohort_sim as C
from bn_whale_fresh import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000


def okx_swaps():
    for url in ("https://www.okx.com/api/v5/public/instruments?instType=SWAP", "https://aws.okx.com/api/v5/public/instruments?instType=SWAP"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=30) as r:
                d = json.loads(r.read())["data"]
            s = {x["instId"].split("-")[0] for x in d if x["instId"].endswith("-USDT-SWAP")}
            if len(s) > 100: return s
        except Exception:  # noqa: BLE001
            pass
    return None


def bar_of(pri):
    if len(pri) < 20: return False, sum(d[2] for d in pri)
    wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
    return (wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0), gp - gl


def main():
    db, out = sys.argv[1], sys.argv[2]
    okx = okx_swaps()
    if not okx:
        print("::error title=norepeat::OKX instrument list unavailable"); json.dump({"error": "no okx list"}, open(out, "w")); return
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl FROM ws_trips WHERE src='bn' AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    candA, candB, stA, stB = [], [], {}, {}
    month = defaultdict(lambda: [0, 0]); per_tr = {}
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
            else:
                e = {"w": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        for e in mine:
            e["close_t"] = max(x[4] for x in e["g"]); e["pnl"] = sum(x[5] or 0 for x in e["g"]); e["rep"] = e["coin"] in okx
        mine.sort(key=lambda e: e["open_t"])
        done = []; nr = 0
        for e in mine:
            t0 = e["open_t"]
            m = month[time_tag(t0)]; m[0] += 1; m[1] += (not e["rep"]); nr += (not e["rep"])
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            a, pa = bar_of(pri)
            share = (sum(1 for d in pri if not d[3]) / len(pri)) if pri else 0.0
            if share >= 0.5: b, pb = bar_of([d for d in pri if d[3]])
            else: b, pb = a, pa
            done.append((t0, e["close_t"], e["pnl"], e["rep"]))
            stA.setdefault(tr, []).append((t0, a, pa)); stB.setdefault(tr, []).append((t0, b, pb))
            if e["rep"] and e["close_t"] - t0 > 2 * MIN:
                if a: candA.append(e)
                if b: candB.append(e)
        per_tr[tr] = (len(mine), nr)
    allc = {id(e): e for e in candA + candB}.values()
    print(f"bn decisions {sum(v[0] for v in per_tr.values())}, non-repeatable {sum(v[1] for v in per_tr.values())}; candA {len(candA)} candB {len(candB)}", flush=True)
    syms = defaultdict(list)
    for e in allc: syms[e["coin"] + "USDT"].append(e)
    data = {sym: B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY) for sym, es in syms.items()}
    sim = {}
    for e in allc:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        v = copy2(S, e["side"], e["open_t"] + 2 * MIN, e["close_t"] + 2 * MIN)
        if v is None or not math.isfinite(v[2]): continue
        sim[id(e)] = {"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "t_out": v[1], "a": v[0], "ret": v[2]}
    TA = D.top_n([sim[id(e)] for e in candA if id(e) in sim], stA, 10)
    TB = D.top_n([sim[id(e)] for e in candB if id(e) in sim], stB, 10)

    def mark(coin, t):
        S = data.get(coin + "USDT")
        if not S: return None
        i = bisect.bisect_right(S["t"], t) - 1
        return S["k"][S["t"][i]][3] if i >= 0 else None

    sa, sb = C.simulate(TA, mark), C.simulate(TB, mark)
    allt = TA + TB
    d0 = min(t["t_in"] for t in allt) // DAY; d1 = max(t["t_out"] for t in allt) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"okx_swaps": len(okx), "months": {k: {"n": v[0], "nonrep": v[1]} for k, v in sorted(month.items())},
         "traders_heavy_nonrep": sorted([(w[-6:], n, k) for w, (n, k) in per_tr.items() if n >= 20 and k / n >= 0.3], key=lambda x: -x[2])[:15],
         "base": {p: D.curve_stats(sa["days"], a, b) for p, (a, b) in parts},
         "var": {p: D.curve_stats(sb["days"], a, b) for p, (a, b) in parts},
         "taken": [len(sa["taken"]), len(sb["taken"])],
         "only_base": len({(t["w"], t["t_in"]) for t in TA} - {(t["w"], t["t_in"]) for t in TB}),
         "only_var": len({(t["w"], t["t_in"]) for t in TB} - {(t["w"], t["t_in"]) for t in TA})}
    P = {p: D.paired(sb["days"], sa["days"], a, b) for p, (a, b) in parts}
    ok_risk = (R["var"]["all"]["worst_day"] >= 1.25 * R["base"]["all"]["worst_day"]) and (R["var"]["all"]["max_dd"] >= 1.25 * R["base"]["all"]["max_dd"])
    P["pass"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and ok_risk)
    R["vs"] = P
    print(f"::notice title=bn norepeat::months {R['months']} | heavy {R['traders_heavy_nonrep']}")
    print(f"::notice title=bn norepeat result::base {R['base']} | var {R['var']} | taken {R['taken']} only_base {R['only_base']} only_var {R['only_var']} | vs {P}")
    json.dump(R, open(out, "w"), ensure_ascii=False, indent=1)


def time_tag(t):
    import time
    return time.strftime("%Y-%m", time.gmtime(t / 1000))


if __name__ == "__main__":
    main()
