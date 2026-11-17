"""Backlog 186 (17.11.2026, 03:20 Athens): beta-adjusted forward copies — do the traders lose, or does the market?
Pre-registered before computing (same data/universe/reviews/copies as whale_fwd170.py, backlog 170):
- every repeatable copy (+2 min, stop −16 %, 0.11 % + funding) of POOL / RA8 / SEL from 04.11 17:13 UTC on (OKX/HL);
- market part of a copy = side × β × BTC return over the SAME interval [our entry, our exit incl. stop];
  exactly 2 variants: B1 β = 1 (raw BTC), BE β = OLS of the coin's 1h returns on BTC 1h returns over 30 d before entry
  (same-hour close-to-close, clipped to 0…3; BTC itself β = 1); alpha = copy result − market part (costs stay in the copy result);
- verdict per group: «умение есть» if alpha mean > 0 with t ≥ 2.4 (all copies) in BOTH variants;
  «умение отрицательное» if alpha mean < 0 with t ≤ −2.4 in both; otherwise «умение ≈ 0 — итог объясняет рынок»
  when |market mean| ≥ |alpha mean|, else «не доказано»; also by trader (mean per trader, t).
Added after the first look (description only, not part of the verdict): «passive» = always-long β·BTC over the same
intervals, and the same split by entry day (copies of one day are not independent).
Usage: whale_beta186.py NEW/whales.json.gz OUT.json OLD/whales.json.gz. Research only; real rules change only with Dmytro's yes.
"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0  # noqa: E402
from tradfi import TRADFI
REP, RANK = True, False
FROM_T = int(os.environ.get("FROM_T", 1793812394000))  # 2026-11-04 17:13:13 UTC — publication of the 04.11 asset
# side check (not the main verdict): FROM_T=<ms> on the 04.11 asset itself (NEW = OLD) → the same split on the history before 04.11
def rep(e): return e['coin'] not in TRADFI and ':' not in e['coin']

MIN, DAY = 60_000, 86_400_000
STEP, LOOK = 3 * DAY, 60 * DAY
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.stdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


LAST = [None]
TIMES = {}


def copy(S, t0, t1, side, stop=16.0):
    a, b = S.price(t0), S.price(t1)
    if not a or not b or t1 <= t0:
        return None
    s = 1 if side == "LONG" else -1
    if stop:
        i0, i1 = S.idx(t0 + S0.H1), S.idx(t1 + S0.H1)
        lvl = a * (1 - s * stop / 100)
        for k in range(i0 + 1, i1):
            if (s > 0 and S.l[k] <= lvl) or (s < 0 and S.h[k] >= lvl):
                b = S.o[k] if ((s > 0 and S.o[k] < lvl) or (s < 0 and S.o[k] > lvl)) else lvl
                t1 = int(S.t[k]); break
    v = s * (b / a - 1) * 100 - S0.COST - S.funding_paid(side, t0, t1)
    LAST[0] = (t0, t1)
    return v if math.isfinite(v) else None


def bar_ok(vals):
    if len(vals) < 20:
        return False
    w = [a for a, _ in vals]
    pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
    if sum(x > 0 for x in w) / len(w) < 0.70 or sum(w) <= 0 or (neg and pos / neg < 2):
        return False
    return sum(b for _, b in vals) > 0


OLD = S0.load_data(sys.argv[3])
UNIV = {w.get('name') or w['addr'][:10] for w in OLD.get('hl') or []} | {t.get('name') or t['code'] for t in OLD.get('okx') or []}
del OLD
traders = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    if (w.get("name") or w["addr"][:10]) not in UNIV: continue
    traders.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    if (t.get("name") or t["code"]) not in UNIV: continue
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    traders.append(("okx", t.get("name") or t["code"], tr))

def episodes_g(trips):
    """study.episodes, keeping the sub-trips (needed to mark legs at a review)."""
    by, out = defaultdict(list), []
    for t in trips: by[(t["coin"], t["side"])].append(t)
    for ts in by.values():
        ts.sort(key=lambda x: x["open_t"]); cur = None
        for t in ts:
            if cur and t["open_t"] - cur[0]["open_t"] <= 4 * S0.H1: cur.append(t)
            else:
                if cur: out.append(cur)
                cur = [t]
        if cur: out.append(cur)
    res = []
    for g in out:
        res.append({"coin": g[0]["coin"], "side": g[0]["side"], "open_t": min(x["open_t"] for x in g), "close_t": max(x["close_t"] for x in g),
                    "pnl": sum(x["pnl"] for x in g), "open": any(x.get("open") for x in g), "g": g})
    return res


eps, src_of = [], {}
for src, name, trips in traders:
    src_of[name] = src
    for e in episodes_g(trips):
        S = W.get(e["coin"])
        if S is None: continue
        e["trader"], e["S"], e["s"] = name, S, (1 if e["side"] == "LONG" else -1)
        eps.append(e)
eps.sort(key=lambda e: e["open_t"])
t0, t_end = eps[0]["open_t"], max(e["open_t"] for e in eps)
reviews, T = [], FROM_T
while T <= now - STEP:
    reviews.append(T); T += STEP
by_tr = defaultdict(list)
for e in eps:
    by_tr[e["trader"]].append(e)


def whale_at(e, T):
    tot = 0.0
    for x in e["g"]:
        if x["open_t"] >= T: continue
        if not x.get("open") and x["close_t"] <= T:
            tot += x["pnl"]
        else:
            p = e["S"].price(T)
            if not p: return None
            tot += e["s"] * (p / x["entry"] - 1) * x["notional"]
    return tot


cache = {}


def look_val(e, T, closed_only):
    v = look_val0(e, T, closed_only)
    if v and RANK and not rep(e): return (v[0], 0.0)
    return v


def look_val0(e, T, closed_only):
    done = (not e.get("open")) and e["close_t"] <= T
    if closed_only and not done: return None
    if done:
        if id(e) not in cache:
            cp = copy(e["S"], e["open_t"] + 2 * MIN, e["close_t"] + 2 * MIN, e["side"], stop=None)
            cache[id(e)] = None if cp is None else (e["pnl"], cp)
        return cache[id(e)]
    w = whale_at(e, T); cp = copy(e["S"], e["open_t"] + 2 * MIN, T, e["side"], stop=None)
    return None if (w is None or cp is None) else (w, cp)


fc = {}


def fwd(e, d):
    if REP and not rep(e): return None
    k = (id(e), d)
    if k not in fc:
        t_out = now if e.get("open") else e["close_t"] + d * MIN
        fc[k] = copy(e["S"], e["open_t"] + d * MIN, min(t_out, now), e["side"])
        TIMES[k] = LAST[0]
    return fc[k]


G = defaultdict(list)
per_review, sel_at = [], {}
TOP = 8
for T in reviews:
    sel, pool, sc = set(), set(), {}
    for tr, lst in by_tr.items():
        lk = [e for e in lst if T - LOOK <= e["open_t"] < T]
        if len(lk) < 20: continue
        pool.add(tr)
        vals = [(e["open_t"], v) for e, v in ((e, look_val(e, T, False)) for e in lk) if v]
        if bar_ok([v for _, v in vals]): sel.add(tr)
        w = [v[0] for _, v in vals]
        pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
        sc[tr] = {"A": sum(v[1] for _, v in vals),
                  "B": sum(v[1] for t, v in vals if t >= T - 21 * DAY),
                  "C": ((min(pos / neg, 99.0) if neg else 99.0), sum(w)) if sum(w) > 0 else None}
    ra = set(sorted(pool, key=lambda x: -sc[x]["A"])[:TOP])
    rb = set(sorted(pool, key=lambda x: -sc[x]["B"])[:TOP])
    rc = set(sorted([x for x in pool if sc[x]["C"]], key=lambda x: (-sc[x]["C"][0], -sc[x]["C"][1]))[:TOP])
    sel_at[T] = (sel, pool)
    for tr in pool:
        for e in by_tr[tr]:
            if not (T <= e["open_t"] < T + STEP): continue
            for d in (2, 6):
                r = fwd(e, d)
                if r is None: continue
                row = {"t": e["open_t"], "trader": tr, "src": src_of[tr], "r": r, "T": T, "coin": e["coin"], "s": e["s"],
                       "iv": TIMES[(id(e), d)]}
                G[f"POOL_{d}"].append(row)
                if tr in sel: G[f"SEL_{d}"].append(row)
                if tr in ra: G[f"RA_{d}"].append(row)
                if tr in rb: G[f"RB_{d}"].append(row)
                if tr in rc: G[f"RC_{d}"].append(row)
    per_review.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "sel": sorted(sel), "pool": len(pool),
                       "ra": sorted(ra), "rb": sorted(rb), "rc": sorted(rc)})

BTC = W.get("BTC")
BIX = {int(x): i for i, x in enumerate(BTC.t)}


def beta(coin, t0):
    if coin == "BTC": return 1.0
    S = W.get(coin)
    if S is None: return None
    i1 = S.idx(t0); i0 = max(1, i1 - 30 * 24)
    xs, ys = [], []
    for k in range(i0, i1):
        tt = int(S.t[k])
        if int(S.t[k - 1]) != tt - S0.H1 or tt not in BIX or tt - S0.H1 not in BIX: continue
        pb0, pb1 = BTC.c[BIX[tt - S0.H1]], BTC.c[BIX[tt]]
        if not pb0 or not pb1 or not S.c[k - 1]: continue
        xs.append(pb1 / pb0 - 1); ys.append(S.c[k] / S.c[k - 1] - 1)
    if len(xs) < 200: return None
    mx, my = st.mean(xs), st.mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    if not vx: return None
    return min(3.0, max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx))


BC = {}
O = {"made": now, "decisions": len(eps), "traders": len(by_tr), "reviews": len(reviews), "from_review": time.strftime("%Y-%m-%d %H:%M", time.gmtime(FROM_T / 1000))}
for g in ("POOL_2", "RA_2", "SEL_2", "POOL_6"):
    rs = G.get(g) or []
    out = {"B1": [], "BE": []}
    mk = {"B1": [], "BE": []}
    raw, bt = [], defaultdict(lambda: {"B1": [], "BE": []})
    passive, bd = [], defaultdict(lambda: defaultdict(list))  # passive: always-long β·BTC over the same intervals; bd: by entry day
    for r in rs:
        t0, t1 = r["iv"]
        a, b = BTC.price(t0), BTC.price(t1)
        if not a or not b: continue
        mb = r["s"] * (b / a - 1) * 100
        k = (r["coin"], t0 // S0.H1)
        if k not in BC: BC[k] = beta(r["coin"], t0)
        be = BC[k]
        if be is None: continue
        raw.append(r["r"])
        passive.append(be * mb * r["s"]); dk = r["t"] // DAY
        bd["raw"][dk].append(r["r"]); bd["mkt"][dk].append(be * mb); bd["alpha"][dk].append(r["r"] - be * mb)
        for v, m in (("B1", mb), ("BE", be * mb)):
            mk[v].append(m); out[v].append(r["r"] - m); bt[r["trader"]][v].append(r["r"] - m)
    X = {"passive_long_BE": tstat(passive), "by_day_BE": {k: tstat([st.mean(x) for x in d.values()]) for k, d in bd.items()},
         "raw": tstat(raw), "longs": sum(1 for r in rs if r["s"] > 0), "shorts": sum(1 for r in rs if r["s"] < 0)}
    for v in ("B1", "BE"):
        X[v] = {"market": tstat(mk[v]), "alpha": tstat(out[v]), "by_trader": tstat([st.mean(x[v]) for x in bt.values()])}
    a1, a2 = X["B1"]["alpha"], X["BE"]["alpha"]
    if all((a.get("mean") or 0) > 0 and (a.get("t") or 0) >= 2.4 for a in (a1, a2)): vd = "умение есть"
    elif all((a.get("mean") or 0) < 0 and (a.get("t") or 0) <= -2.4 for a in (a1, a2)): vd = "умение отрицательное"
    elif abs(X["BE"]["market"].get("mean") or 0) >= abs(a2.get("mean") or 0): vd = "умение ≈ 0 — итог объясняет рынок"
    else: vd = "не доказано"
    X["verdict"] = vd
    O[g] = X
    print(g, "raw", X["raw"], "L/S", X["longs"], X["shorts"])
    print("   passive long", X["passive_long_BE"], "| by day:", {k: (v.get("mean"), v.get("t"), v.get("n")) for k, v in X["by_day_BE"].items()})
    for v in ("B1", "BE"): print("  ", v, "market", X[v]["market"], "\n     alpha", X[v]["alpha"], "\n     by_tr", X[v]["by_trader"])
    print("  verdict", vd)
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1, default=float)
