"""Binance lead traders: are SHORTS into a pump knowledge or luck? (backlog 96, owner task 06.11.2026, Binance half, research only)

Same pre-registered design as scripts/research/whale_impulse.py (OKX/HL): impulse = coin return over the last 4 h >= +3 % or over
the last 24 h >= +5 %, measured on closed 5m Binance bars before OUR entry (open_t + 2 min). Copy exit close_t + 2 min, stop −16 %,
costs 0.11 % + funding (bn_whale_stops.copy_ret). Tests: T1 impulse shorts − other shorts; T2 placebo P1 (random short of the
same coin, entry uniform ±3.5 days, same hold, 300 reps); T3 placebo P2 (random other impulse moment of the same coin within
±14 days). 4 looks (2 windows × 2 pools) → «knowledge» only if T1 t >= 2.5, positive in both halves, P1 p <= 0.0125, n >= 30.
Pools: A all traders with >= 20 decisions, B walk-forward bar (prior 60 d: win >= 70 %, PF >= 2, >= 20, P&L > 0).
Longs after an impulse reported the same way (descriptive). Read-only. Usage: bn_whale_impulse.py DB OUT.json"""
import bisect, json, math, os, random, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
DL, REPS = 2, 300
TH = {"4h": (4 * H, 0.03), "24h": (24 * H, 0.05)}
RNG = random.Random(96)


def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None}


def diff(a, b):
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return {"d": round(st.mean(a) - st.mean(b), 3), "t": round((st.mean(a) - st.mean(b)) / se, 2) if se else None}


def past_ret(S, t, look):
    """return over (t − look, t) on closed 5m bars: close of the last bar ending <= t vs close of the bar ending <= t − look"""
    i = bisect.bisect_right(S["t"], t - 5 * MIN) - 1; j = bisect.bisect_right(S["t"], t - look - 5 * MIN) - 1
    if i < 0 or j < 0 or S["t"][j] < t - look - 30 * MIN: return None
    return S["k"][S["t"][i]][3] / S["k"][S["t"][j]][3] - 1


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl FROM ws_trips WHERE src='bn' AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) < 20: continue
        for e in mine:
            g = e["g"]; e["close_t"] = max(x[4] for x in g); e["pnl"] = sum(x[5] or 0 for x in g)
        mine.sort(key=lambda e: e["open_t"])
        done = []
        for e in mine:
            t0 = e["open_t"]
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            e["bar"] = bar
            done.append((t0, e["close_t"], e["pnl"]))
            if e["close_t"] - t0 > DL * MIN: eps.append(e)
    print(f"bn decisions: {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps: syms[e["coin"] + "USDT"].append(e)
    data = {}
    for sym, es in syms.items():
        data[sym] = B.load_sym(sym, min(x["open_t"] for x in es) - 16 * DAY, max(x["close_t"] for x in es) + 15 * DAY)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        te, tx = e["open_t"] + DL * MIN, e["close_t"] + DL * MIN
        v = B.copy_ret(S, e["side"], te, tx, 16)
        if v is None or not math.isfinite(v): continue
        r = {"w": e["trader"], "sym": e["coin"] + "USDT", "side": e["side"], "t": te, "hold": tx - te, "bar": e["bar"], "r": v}
        for wn, (look, _thr) in TH.items(): r[wn] = past_ret(S, te, look)
        res.append(r)
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = sorted(r["t"] for r in res)[len(res) // 2]
    imp_cache = {}

    def imp_times(sym, wn):
        if (sym, wn) not in imp_cache:
            S = data[sym]; look, thr = TH[wn]; t = S["t"]; outl = []
            for i, ti in enumerate(t):
                j = bisect.bisect_right(t, ti - look) - 1
                if j >= 0 and S["k"][ti][3] / S["k"][t[j]][3] - 1 >= thr:
                    outl.append(ti + 5 * MIN + DL * MIN)                    # entry 2 min after the bar closes
            imp_cache[(sym, wn)] = outl
        return imp_cache[(sym, wn)]

    def placebo(sel, side, mode, wn):
        means, allr = [], []
        for _ in range(REPS):
            rs = []
            for r in sel:
                S = data[r["sym"]]; tbeg = S["t"][0] + 25 * H; tend = S["t"][-1] - r["hold"] - H
                if mode == "P1":
                    lo, hi = max(tbeg, r["t"] - int(3.5 * DAY)), min(tend, r["t"] + int(3.5 * DAY))
                    if hi <= lo: continue
                    tt = RNG.randrange(lo, hi)
                else:
                    al = imp_times(r["sym"], wn)
                    lo_i, hi_i = bisect.bisect_left(al, max(tbeg, r["t"] - 14 * DAY)), bisect.bisect_right(al, min(tend, r["t"] + 14 * DAY))
                    cand = [x for x in al[lo_i:hi_i] if abs(x - r["t"]) > 6 * H]
                    if len(cand) == 0: continue
                    tt = int(cand[RNG.randrange(len(cand))])
                v = B.copy_ret(S, side, tt, tt + r["hold"], 16)
                if v is not None and math.isfinite(v): rs.append(v)
            if rs: means.append(st.mean(rs)); allr += rs
        return means, (round(st.mean(allr), 3) if allr else None)

    O = {"n": len(res), "traders": len({r["w"] for r in res}), "n_short": sum(r["side"] == "SHORT" for r in res)}
    print(f"::notice title=bn impulse n::{O['n']} decisions, {O['traders']} traders, shorts {O['n_short']}")
    for pool, rs0 in (("A", res), ("B", [r for r in res if r["bar"]])):
        for wn, (_look, thr) in TH.items():
            for side in ("SHORT", "LONG"):
                ss = [r for r in rs0 if r["side"] == side and r[wn] is not None]
                imp = [r for r in ss if r[wn] >= thr]; oth = [r for r in ss if r[wn] < thr]
                cell = {"imp": s([r["r"] for r in imp]), "other": s([r["r"] for r in oth]),
                        "d": diff([r["r"] for r in imp], [r["r"] for r in oth])}
                for part, f in (("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid)):
                    cell[part] = {"imp": s([r["r"] for r in imp if f(r)]), "d": diff([r["r"] for r in imp if f(r)], [r["r"] for r in oth if f(r)])}
                if len(imp) >= 10:
                    act = st.mean([r["r"] for r in imp])
                    for mode in ("P1", "P2"):
                        m, base = placebo(imp, side, mode, wn)
                        cell[mode] = {"p": round((sum(1 for x in m if x >= act) + 1) / (len(m) + 1), 4) if m else None, "mean": base}
                O[f"{pool}_{wn}_{side}"] = cell
                i = cell["imp"]; o = cell["other"]
                print(f"::notice title=bn impulse {pool} {wn} {side}::imp {i.get('n')} {i.get('mean')} t{i.get('t')} pf{i.get('pf')} | other {o.get('n')} {o.get('mean')} "
                      f"| d {cell['d']} | h1 {cell['h1']['d']} h2 {cell['h2']['d']} | P1 {cell.get('P1')} P2 {cell.get('P2')}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
