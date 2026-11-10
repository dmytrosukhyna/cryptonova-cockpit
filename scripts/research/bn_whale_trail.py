"""Backlog 130 (owner 10.11: «перевод в безубыток, первый/второй тейк… прими решение, как лучше»): moving the stop of a whale copy.
Research only — reads ws_trips from the whale study's database (Actions cache), writes nothing to it. Reuses the data loading and
decision grouping of bn_whale_stops.py (Binance lead traders, 5m candles from data.binance.vision, copy +2 min, exit +2 min after the
whale, costs 0.11 % a round trip + funding).

Pre-registered before any result was seen (6 variants, base = the live rule):
 BASE   stop −16 %, exit with the whale (live);
 BE3    after a +3 % move in our favour the stop goes to breakeven (entry ± costs);
 BE5    the same after +5 %;
 TR8    after +8 % a trailing stop 6 % behind the best price (never below breakeven);
 LAD    take 1/3 at +5 % and 1/3 at +10 %, stop to breakeven after the first take, the rest exits with the whale;
 BE5TR  BE5, and after +10 % a trailing stop 5 % behind the best price.
Intrabar order is conservative: in each 5m bar the current stop is checked against the adverse extreme first; the favourable extreme
moves the stop only from the next bar on. Result in R with the same risk unit for all (16 %), so the variants are directly comparable.
Pass rule: paired per-decision difference vs BASE with t ≥ 2.5 in BOTH halves and ≥ 0 on the walk-forward «good» traders (h2),
worst run (ddR) not worse than 1.25× BASE. Six variants → the t threshold is already strict.
Usage: bn_whale_trail.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B  # noqa: E402

COST, H, DAY = B.COST, B.H, B.DAY
UNIT = 16.0
VAR = ["BASE", "BE3", "BE5", "TR8", "LAD", "BE5TR"]


def path_ret(S, side, t_in, t_out, var):
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]
    be = a * (1 + s * COST / 100)          # breakeven including costs
    stop = a * (1 - s * 0.16)
    best = a
    left, booked = 1.0, 0.0                # share of the position still open, P&L (in %) already taken
    takes = [(5.0, 1 / 3), (10.0, 1 / 3)] if var == "LAD" else []
    exit_px, t_end = None, S["t"][j]
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        adverse, fav = (l, h) if s > 0 else (h, l)
        hit = (adverse <= stop) if s > 0 else (adverse >= stop)
        if hit:
            gap = (o < stop) if s > 0 else (o > stop)
            exit_px = o if (q > i and gap) else stop
            t_end = S["t"][q]
            break
        # favourable extreme → takes and stop moves, effective from the next bar
        best = max(best, fav) if s > 0 else min(best, fav)
        move = s * (best / a - 1) * 100
        while takes and move >= takes[0][0]:
            pct, part = takes.pop(0)
            booked += part * pct; left -= part
            stop = be if s * (be - stop) > 0 else stop
        if var in ("BE3", "BE5", "BE5TR") and move >= (3 if var == "BE3" else 5):
            if s * (be - stop) > 0:
                stop = be
        if var == "TR8" and move >= 8:
            tr = best * (1 - s * 0.06)
            tr = tr if s * (tr - be) > 0 else be
            if s * (tr - stop) > 0:
                stop = tr
        if var == "BE5TR" and move >= 10:
            tr = best * (1 - s * 0.05)
            if s * (tr - stop) > 0:
                stop = tr
    if exit_px is None:
        exit_px = S["k"][S["t"][j]][0]
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= t_end) * 100 * s
    return booked + left * s * (exit_px / a - 1) * 100 - COST - f


def paired(xs, ys):
    d = [x - y for x, y in zip(xs, ys)]
    if len(d) < 2:
        return {"n": len(d)}
    m = st.mean(d); sd = st.pstdev(d)
    return {"n": len(d), "dR": round(m, 4), "t": round(m / (sd / math.sqrt(len(d))), 2) if sd else None,
            "changed": round(sum(abs(x) > 1e-9 for x in d) / len(d), 3)}


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    names = {}
    for a, lb in c.execute("SELECT addr, lb FROM ws_whale WHERE addr LIKE 'bn:%'"):
        try:
            names[a] = (json.loads(lb or "{}") or {}).get("name") or a
        except Exception:  # noqa: BLE001
            names[a] = a
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='bn' "
                     "AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
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
        e["hold_h"] = (e["close_t"] - e["open_t"]) / H
    print(f"decisions (traders with ≥ 20): {len(eps)}", flush=True)
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
        r = {"trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "hold_h": e["hold_h"]}
        ok = True
        for k in VAR:
            v = path_ret(S, e["side"], e["open_t"] + 120_000, e["close_t"] + 120_000, k)
            if v is None or not math.isfinite(v):
                ok = False; break
            r[k] = v / UNIT
        if ok:
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    h1 = defaultdict(list)
    for r in res:
        if r["t"] < mid:
            h1[r["trader"]].append(r["wret"] - COST)
    good = {w for w, xs in h1.items() if len(xs) >= 20 and sum(x > 0 for x in xs) / len(xs) >= 0.70
            and sum(x for x in xs if x > 0) / max(1e-9, -sum(x for x in xs if x < 0)) >= 2}
    O = {"n": len(res), "traders": len({r["trader"] for r in res}), "good_n": len(good),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    parts = {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid,
             "good_h2": lambda r: r["t"] >= mid and r["trader"] in good, "long_hold": lambda r: r["hold_h"] >= 48}
    for p, f in parts.items():
        rs = [r for r in res if f(r)]
        O[p] = {k: B.stat([r[k] for r in rs]) for k in VAR}
        O[p]["vs_base"] = {k: paired([r[k] for r in rs], [r["BASE"] for r in rs]) for k in VAR if k != "BASE"}
    verdict = {}
    for k in VAR[1:]:
        t1, t2 = O["h1"]["vs_base"][k].get("t") or 0, O["h2"]["vs_base"][k].get("t") or 0
        g = O["good_h2"]["vs_base"][k].get("dR") or 0
        dd_ok = (O["all"][k].get("ddR") or 0) >= 1.25 * (O["all"]["BASE"].get("ddR") or 0)
        verdict[k] = "прошло" if (t1 >= 2.5 and t2 >= 2.5 and g >= 0 and dd_ok) else "не прошло"
    O["verdict"] = verdict
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=trail n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']}, good(h1)={len(good)}")
    for p in parts:
        line = " ".join(f"{k}:{v.get('R')}/{v.get('t')}/{v.get('ddR')}" for k, v in O[p].items() if k in VAR)
        vb = " ".join(f"{k}:{v.get('dR')}/t{v.get('t')}" for k, v in O[p]["vs_base"].items())
        print(f"::notice title=trail {p}::{line} || vs BASE {vb}")
    print(f"::notice title=trail verdict::{verdict}")


if __name__ == "__main__":
    main()
