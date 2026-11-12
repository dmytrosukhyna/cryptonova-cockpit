"""Backlog 144 (side result of 135): early exit «it did not go» — research only, reads ws_trips (Actions cache), writes nothing to it.
Same machinery as bn_whale_exitanat.py (Binance 5m candles, copy +2 min, exit +2 min after the whale, stop −16 %, costs 0.11 % a
round trip + funding, result in R with a 16 % risk unit).

Pre-registered before any result was seen (12.11 03:30 Athens), ONE variant:
 EARLY — at the first 5-min bar at/after entry + 12 h, if the whale is still in, the best move since our entry (bar highs/lows)
   never reached +0.5 % AND that bar's close is below −3 % from our entry → exit at that bar's close. Otherwise as BASE.
 BASE = exit with the whale (+2 min) / stop −16 %.
 Sample: ONLY traders outside the locked list (the 5 Binance list whales excluded; the rule's numbers came from them) with ≥ 20
   decisions — out of sample by trader. Slices: all, time halves, walk-forward «good» traders in h2 (passed bar in h1), last 30 days.
 Pass: paired t ≥ 2.5 vs BASE in BOTH halves, ≥ 0 on good_h2 and on last 30 days, worst run not worse than 1.25× BASE, and the
   decisions it cuts have a mean BASE result < 0. A pass means OKX/HL check next, then a DEMO desk only; real money — owner's decision.
Usage: bn_whale_early.py DB OUT.json"""
import json, math, os, sqlite3, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B  # noqa: E402
from bn_whale_trail import paired  # noqa: E402
from bn_whale_exitanat import LIST, key  # noqa: E402

COST, H, DAY = B.COST, B.H, B.DAY
UNIT, CHECK_H, MFE_MIN, DOWN = 16.0, 12, 0.5, -3.0


def path_ret(S, side, t_in, t_out, early):
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None, False
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]
    stop = a * (1 - s * 0.16); best = 0.0
    exit_px, t_end, cut = None, S["t"][j], False
    t_chk = S["t"][i] + CHECK_H * H
    for q in range(i, j):
        tq = S["t"][q]
        o, h, l, c = S["k"][tq]
        adverse, fav = (l, h) if s > 0 else (h, l)
        if (adverse <= stop) if s > 0 else (adverse >= stop):
            gap = (o < stop) if s > 0 else (o > stop)
            exit_px = o if (q > i and gap) else stop; t_end = tq; break
        best = max(best, s * (fav / a - 1) * 100)
        if early and tq >= t_chk:
            if best < MFE_MIN and s * (c / a - 1) * 100 < DOWN:
                exit_px = c; t_end = tq; cut = True
            early = False
            if cut:
                break
    if exit_px is None:
        exit_px = S["k"][S["t"][j]][0]
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= t_end) * 100 * s
    return s * (exit_px / a - 1) * 100 - COST - f, cut


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='bn' "
                     "AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        if key(tr) in LIST:
            continue
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[8] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[4] for x in g)
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[6] / x[5] - 1) * 100 * wi for x, wi in zip(g, w) if x[5] and x[6]) / sum(w)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY)
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        b, _ = path_ret(S, e["side"], e["open_t"] + 120_000, e["close_t"] + 120_000, False)
        x, cut = path_ret(S, e["side"], e["open_t"] + 120_000, e["close_t"] + 120_000, True)
        if b is None or x is None or not (math.isfinite(b) and math.isfinite(x)):
            continue
        res.append({"trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "BASE": b / UNIT, "EARLY": x / UNIT, "cut": cut})
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]; last30 = res[-1]["t"] - 30 * DAY
    h1 = defaultdict(list)
    for r in res:
        if r["t"] < mid:
            h1[r["trader"]].append(r["wret"] - COST)
    good = {w for w, xs in h1.items() if len(xs) >= 20 and sum(x > 0 for x in xs) / len(xs) >= 0.70
            and sum(x for x in xs if x > 0) / max(1e-9, -sum(x for x in xs if x < 0)) >= 2}
    O = {"n": len(res), "traders": len({r["trader"] for r in res}), "good_n": len(good),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    parts = {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid,
             "good_h2": lambda r: r["t"] >= mid and r["trader"] in good, "last30": lambda r: r["t"] >= last30}
    for p, f in parts.items():
        rs = [r for r in res if f(r)]
        cutr = [r for r in rs if r["cut"]]
        O[p] = {k: B.stat([r[k] for r in rs]) for k in ("BASE", "EARLY")}
        O[p]["vs_base"] = paired([r["EARLY"] for r in rs], [r["BASE"] for r in rs])
        O[p]["cut"] = {"n": len(cutr), "base_R": B.stat([r["BASE"] for r in cutr]).get("R") if len(cutr) > 1 else None,
                       "early_R": B.stat([r["EARLY"] for r in cutr]).get("R") if len(cutr) > 1 else None,
                       "base_win": round(sum(r["BASE"] > 0 for r in cutr) / len(cutr), 3) if cutr else None}
    t1, t2 = O["h1"]["vs_base"].get("t") or 0, O["h2"]["vs_base"].get("t") or 0
    ok = (t1 >= 2.5 and t2 >= 2.5 and (O["good_h2"]["vs_base"].get("dR") or 0) >= 0 and (O["last30"]["vs_base"].get("dR") or 0) >= 0
          and (O["all"]["EARLY"].get("ddR") or 0) >= 1.25 * (O["all"]["BASE"].get("ddR") or 0) and (O["all"]["cut"]["base_R"] or 0) < 0)
    O["verdict"] = "прошло" if ok else "не прошло"
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=early n::{O['n']} decisions, {O['traders']} traders (list excluded), {O['from']} → {O['to']}, good(h1)={len(good)}")
    for p in parts:
        v = O[p]
        print(f"::notice title=early {p}::BASE {v['BASE'].get('R')}/t{v['BASE'].get('t')}/dd{v['BASE'].get('ddR')}/w{v['BASE'].get('win')} "
              f"EARLY {v['EARLY'].get('R')}/t{v['EARLY'].get('t')}/dd{v['EARLY'].get('ddR')}/w{v['EARLY'].get('win')} || "
              f"vs BASE {v['vs_base'].get('dR')}/t{v['vs_base'].get('t')}/ch{v['vs_base'].get('changed')} || cut {v['cut']}")
    print(f"::notice title=early verdict::{O['verdict']}")


if __name__ == "__main__":
    main()
