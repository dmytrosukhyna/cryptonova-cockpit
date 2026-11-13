"""Backlog 151, 13.11.2026: backlog 149 («select at a review → copy until the next review») on OKX/HL (lab-data-whales, 08.08–04.11).
Research only. Data: lab-data-whales → whale_rules_convert.py; whale_rules_study.py (branch research/whale-rules-0411) next to this
file as study.py. Usage: whale_selroll.py <conv>/whales.json.gz OUT.json

Pre-registered before any result was seen (13.11 03:35 Athens), same design as bn_whale_selroll.py (backlog 149):
- Decision = study.episodes (same coin + side, entries within 4 h). Traders with ≥ 8 trips (as whale_lockloss.py).
- Reviews every 3 days, the first 60 days after the first decision in the data, the last 3 days before the data end.
- At review T, per trader, lookback = decisions opened in [T − 60 d, T). Closed by T → realised P&L ($); open at T → legs opened
  before T marked to market at T (MTM, our live bar). Copy test = copy +2 min, exit at min(close, T) + 2 min (no stop).
- Bar: ≥ 20 decisions, win ≥ 70 %, PF ≥ 2, sum > 0, copy-test sum > 0. CLOSED = the same on closed-by-T decisions only.
- Forward: decisions opened in [T, T + 3 d) by a trader selected at T; copy +2 min (OKX/HL live lag ≈ 30 s poll + order), exit at the
  whale's close + 2 min, stop −16 %, 0.11 % a round trip + funding; open at the data end → marked at the end. Robustness: +6 min.
  POOL = every trader with ≥ 20 lookback decisions (no bar).
- Primary: pooled mean copy result SEL with t; SEL − POOL; by trader (equal weight); by source; first vs second half of reviews.
- Also: share of SEL forward copies whose trader fails the bar at ≥ 1 of the next 7 reviews (≈ 3 weeks) that exist in the data.
- Verdict «отбор держится вперёд»: SEL mean > 0 with t ≥ 2 AND SEL > POOL AND SEL > 0 in both halves. Information only.
"""
import json, math, os, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0  # noqa: E402

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
    k = (id(e), d)
    if k not in fc:
        t_out = now if e.get("open") else e["close_t"] + d * MIN
        fc[k] = copy(e["S"], e["open_t"] + d * MIN, min(t_out, now), e["side"])
    return fc[k]


G = defaultdict(list)
per_review, sel_at = [], {}
for T in reviews:
    sel, selc, pool = set(), set(), set()
    for tr, lst in by_tr.items():
        lk = [e for e in lst if T - LOOK <= e["open_t"] < T]
        if len(lk) < 20: continue
        pool.add(tr)
        if bar_ok([x for x in (look_val(e, T, False) for e in lk) if x]): sel.add(tr)
        if bar_ok([x for x in (look_val(e, T, True) for e in lk) if x]): selc.add(tr)
    sel_at[T] = (sel, pool)
    for tr in pool:
        for e in by_tr[tr]:
            if not (T <= e["open_t"] < T + STEP): continue
            for d in (2, 6):
                r = fwd(e, d)
                if r is None: continue
                row = {"t": e["open_t"], "trader": tr, "src": src_of[tr], "r": r, "T": T}
                G[f"POOL_{d}"].append(row)
                if tr in sel: G[f"SEL_{d}"].append(row)
                if tr in selc: G[f"CLOSED_{d}"].append(row)
    per_review.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "sel": sorted(sel), "closed": len(selc), "pool": len(pool)})

# dropout within the next 7 reviews
drop = {"n": 0, "fails": 0, "checked": 0}
for row in G["SEL_2"]:
    nxt = [R for R in reviews if row["T"] < R <= row["T"] + 21 * DAY]
    drop["n"] += 1
    if not nxt: continue
    drop["checked"] += 1
    drop["fails"] += any(row["trader"] in sel_at[R][1] and row["trader"] not in sel_at[R][0] for R in nxt)

O = {"made": now, "decisions": len(eps), "traders": len(by_tr), "reviews": len(reviews), "per_review": per_review, "dropout": drop,
     "from": time.strftime("%Y-%m-%d", time.gmtime(t0 / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(now / 1000))}
act = [R for R in reviews if len(sel_at[R][1]) >= 5]  # halves over reviews with a real pool (≥ 5 traders)
midT = act[len(act) // 2] if act else 0
O["active_reviews"] = [time.strftime("%Y-%m-%d", time.gmtime(act[0] / 1000)), len(act)] if act else None
for g, rs in sorted(G.items()):
    bt = defaultdict(list)
    for r in rs: bt[r["trader"]].append(r["r"])
    O[g] = {"all": tstat([r["r"] for r in rs]), "traders": len(bt), "by_trader": tstat([st.mean(v) for v in bt.values()]),
            "half1": tstat([r["r"] for r in rs if r["T"] < midT]), "half2": tstat([r["r"] for r in rs if r["T"] >= midT]),
            "months": {m: tstat([r["r"] for r in rs if time.strftime("%Y-%m", time.gmtime(r["t"] / 1000)) == m])
                       for m in sorted({time.strftime("%Y-%m", time.gmtime(r["t"] / 1000)) for r in rs})},
            "okx": tstat([r["r"] for r in rs if r["src"] == "okx"]), "hl": tstat([r["r"] for r in rs if r["src"] == "hl"]),
            "per_trader": {k: tstat(v) for k, v in sorted(bt.items(), key=lambda kv: -len(kv[1]))}}
S_, P_ = O.get("SEL_2", {}), O.get("POOL_2", {})
sm, pm = (S_.get("all") or {}).get("mean"), (P_.get("all") or {}).get("mean")
holds = (sm is not None and sm > 0 and (S_["all"].get("t") or 0) >= 2 and pm is not None and sm > pm
         and (S_["half1"].get("mean") or -1) > 0 and (S_["half2"].get("mean") or -1) > 0)
O["verdict"] = "отбор держится вперёд" if holds else "отбор вперёд не доказан"
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(f"decisions {O['decisions']} traders {O['traders']} reviews {O['reviews']} {O['from']}→{O['to']} dropout {drop}")
for g in sorted(G):
    x = O[g]
    print(g, "all", x["all"], "| by_tr", x["by_trader"], "tr", x["traders"], "| h1", x["half1"].get("mean"), x["half1"].get("n"),
          "h2", x["half2"].get("mean"), x["half2"].get("n"), "| okx", x["okx"].get("mean"), x["okx"].get("n"), "hl", x["hl"].get("mean"), x["hl"].get("n"))
for g in ("SEL_2", "POOL_2"): print(g, " ".join(f"{m} {v.get('mean')}/{v['n']}" for m, v in O[g]["months"].items()))
for p in per_review[-12:]: print(p["T"], len(p["sel"]), p["closed"], p["pool"], p["sel"][:6])
print("verdict", O["verdict"])
