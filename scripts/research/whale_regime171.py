"""Backlog 171 (15.11.2026, 15:20 Athens): BTC regime at entry and REPEATABLE copies on OKX/HL (lab-data-whales, 08.08–04.11).
Pre-registered before any result (one cut, no other variants): regime = last completed UTC daily close of BTC before the copy
entry vs the simple mean of the 50 daily closes ending there (SMA50); «ниже» = close < SMA50. Copies exactly as whale_rep162_selrank.py
with REP=1 (repeatable only, copy +2 min, stop −16 %, 0.11 % + funding; +6 min as a check). Groups POOL (primary), SEL, RA.
Split: longs and shorts separately × above/below. Verdict «ниже хуже» for a side: mean(below) < mean(above) with Welch t ≥ 2 AND
below < above in both halves (halves by review, as the source script). Only if longs pass here AND on Binance data after 07.11 —
propose a DEMO rule «half risk for longs below SMA50». Research only; real-money rules change only with Dmytro's yes.
Usage: REP=1 whale_regime171.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0  # noqa: E402
from tradfi import TRADFI
REP, RANK = os.environ.get('REP') == '1', os.environ.get('RANK') == '1'
def rep(e): return e['coin'] not in TRADFI and ':' not in e['coin']

MIN, DAY = 60_000, 86_400_000
STEP, LOOK = 3 * DAY, 60 * DAY
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)
import numpy as _np
_b = _np.load(os.path.join(os.path.dirname(sys.argv[1]), "mkt", "BTC.npz"))["k15"]
_dd = {}
for _r in _b: _dd[int(_r[0] // DAY)] = float(_r[4])  # last 15m close of each UTC day
_days = sorted(_dd)


def regime(t):
    d = int(t // DAY) - 1  # last completed UTC day
    xs = [_dd[k] for k in range(d - 49, d + 1) if k in _dd]
    if d not in _dd or len(xs) < 45: return None
    return "above" if _dd[d] >= sum(xs) / len(xs) else "below"


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.stdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


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
    return v if math.isfinite(v) else None


def bar_ok(vals):
    if len(vals) < 20:
        return False
    w = [a for a, _ in vals]
    pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
    if sum(x > 0 for x in w) / len(w) < 0.70 or sum(w) <= 0 or (neg and pos / neg < 2):
        return False
    return sum(b for _, b in vals) > 0


traders = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    traders.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
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
reviews, T = [], t0 + LOOK
while T <= min(t_end, now) - STEP:
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
                row = {"t": e["open_t"], "trader": tr, "src": src_of[tr], "r": r, "T": T, "side": e["side"], "reg": regime(e["open_t"])}
                G[f"POOL_{d}"].append(row)
                if tr in sel: G[f"SEL_{d}"].append(row)
                if tr in ra: G[f"RA_{d}"].append(row)
                if tr in rb: G[f"RB_{d}"].append(row)
                if tr in rc: G[f"RC_{d}"].append(row)
    per_review.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "sel": sorted(sel), "pool": len(pool),
                       "ra": sorted(ra), "rb": sorted(rb), "rc": sorted(rc)})

# dropout within the next 7 reviews
drop = {"n": 0, "fails": 0, "checked": 0}
for row in G["SEL_2"]:
    nxt = [R for R in reviews if row["T"] < R <= row["T"] + 21 * DAY]
    drop["n"] += 1
    if not nxt: continue
    drop["checked"] += 1
    drop["fails"] += any(row["trader"] in sel_at[R][1] and row["trader"] not in sel_at[R][0] for R in nxt)


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb else None

act = [R for R in reviews if len(sel_at[R][1]) >= 5]
midT = act[len(act) // 2] if act else 0
O = {"made": now, "decisions": len(eps), "reviews": len(reviews), "midT": time.strftime("%Y-%m-%d", time.gmtime(midT / 1000)),
     "regime_days": {}, "groups": {}, "verdict": {}}
seen = {}
for r in G["POOL_2"]: seen[time.strftime("%Y-%m-%d", time.gmtime(r["t"] / 1000))] = r["reg"]
O["regime_days"] = {"above": sum(v == "above" for v in seen.values()), "below": sum(v == "below" for v in seen.values()),
                    "none": sum(v is None for v in seen.values())}
for g in ("POOL_2", "SEL_2", "RA_2", "POOL_6", "SEL_6", "RA_6"):
    rs = G.get(g, []); X = {}
    for side in ("LONG", "SHORT"):
        for reg in ("above", "below"):
            xs = [r["r"] for r in rs if r["side"] == side and r["reg"] == reg]
            X[f"{side}_{reg}"] = tstat(xs)
            for h, f in (("h1", lambda r: r["T"] < midT), ("h2", lambda r: r["T"] >= midT)):
                X[f"{side}_{reg}_{h}"] = tstat([r["r"] for r in rs if r["side"] == side and r["reg"] == reg and f(r)])
            X[f"{side}_{reg}_months"] = {m: tstat([r["r"] for r in rs if r["side"] == side and r["reg"] == reg and time.strftime("%Y-%m", time.gmtime(r["t"] / 1000)) == m])["n"]
                                         for m in sorted({time.strftime("%Y-%m", time.gmtime(r["t"] / 1000)) for r in rs})}
        a = [r["r"] for r in rs if r["side"] == side and r["reg"] == "below"]
        b = [r["r"] for r in rs if r["side"] == side and r["reg"] == "above"]
        X[f"{side}_t_below_minus_above"] = welch(a, b)
        m = lambda k: (X[k].get("mean") if X[k].get("n") else None)
        halves = all(m(f"{side}_below_{h}") is not None and m(f"{side}_above_{h}") is not None and m(f"{side}_below_{h}") < m(f"{side}_above_{h}") for h in ("h1", "h2"))
        t = X[f"{side}_t_below_minus_above"]
        O["verdict"][f"{g}_{side}"] = "ниже хуже" if (t is not None and t <= -2 and halves) else "не доказано"
    O["groups"][g] = X
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print("decisions", O["decisions"], "reviews", O["reviews"], "mid", O["midT"], "days", O["regime_days"])
for g, X in O["groups"].items():
    for side in ("LONG", "SHORT"):
        print(g, side, "above", X[f"{side}_above"], "| below", X[f"{side}_below"], "| t", X[f"{side}_t_below_minus_above"])
        print("    h1 above/below", X[f"{side}_above_h1"].get("mean"), X[f"{side}_above_h1"]["n"], "/", X[f"{side}_below_h1"].get("mean"), X[f"{side}_below_h1"]["n"],
              " h2", X[f"{side}_above_h2"].get("mean"), X[f"{side}_above_h2"]["n"], "/", X[f"{side}_below_h2"].get("mean"), X[f"{side}_below_h2"]["n"])
print("verdict", O["verdict"])
