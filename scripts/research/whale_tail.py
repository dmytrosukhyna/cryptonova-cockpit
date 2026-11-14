"""Backlog 166, 14.11.2026 21:20 Athens: the whale's «tail» (p95 MAE) as a selection filter — OKX/HL (lab-data-whales, 08.08–04.11).
Research only. Data: lab-data-whales → whale_rules_convert.py; whale_rules_study.py (branch research/whale-rules-0411) next to this
file as study.py. Usage: whale_tail.py <conv>/whales.json.gz OUT.json

Pre-registered before any result was seen (14.11 21:20 Athens). Same rolling design as whale_selroll.py (backlog 151):
- Reviews every 3 days; lookback 60 d with open decisions marked to market (our live bar); SEL = the live bar (≥ 20 decisions,
  win ≥ 70 %, PF ≥ 2, sum > 0, copy-test +2 min > 0).
- New: per decision in the lookback, MAE = the worst adverse move of price vs the whale's notional-weighted entry (legs opened so
  far), on 1-h highs/lows of the bars from the entry hour to min(close, T). Tail = p95 of MAE over the lookback decisions (nearest rank).
- TAIL = SEL minus traders with tail > 10 % (ONE variant, threshold fixed in the backlog item, no tuning).
- Forward: decisions opened in [T, T + 3 d) by a selected trader; copy +2 min, exit at the whale's close + 2 min, stop −16 %,
  0.11 % + funding; open at the data end → marked at the end. Robustness +6 min.
- Pass («хвост-фильтр помогает»): mean(TAIL) > mean(SEL) in both halves of active reviews AND the excluded copies (SEL∖TAIL) are
  worse than kept ones with Welch t ≤ −2 AND the sum of copy results (account level, equal size) is higher for TAIL in both halves.
  Information only; real money — owner decision.
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
mae_c = {}


def mae(e, T):
    end = min(T, e["close_t"]) if not e.get("open") else T
    k = (id(e), end)
    if k in mae_c: return mae_c[k]
    legs = [x for x in e["g"] if x["open_t"] < end]
    nt = sum(abs(x["notional"]) for x in legs)
    if not legs or nt <= 0: mae_c[k] = None; return None
    ent = sum(x["entry"] * abs(x["notional"]) for x in legs) / nt
    S, s = e["S"], e["s"]
    i0, i1 = S.idx(e["open_t"]), S.idx(end)
    worst = 0.0
    for j in range(max(i0 + 1, 0), max(i1, i0 + 1) + 1):
        if j >= len(S.t): break
        adv = (1 - S.l[j] / ent) if s > 0 else (S.h[j] / ent - 1)
        worst = max(worst, adv * 100)
    mae_c[k] = worst
    return worst


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)] if xs else None


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
tails = []
per_review, sel_at = [], {}
for T in reviews:
    sel, selc, pool, tail = set(), set(), set(), set()
    for tr, lst in by_tr.items():
        lk = [e for e in lst if T - LOOK <= e["open_t"] < T]
        if len(lk) < 20: continue
        pool.add(tr)
        if bar_ok([x for x in (look_val(e, T, False) for e in lk) if x]): sel.add(tr)
        if tr in sel:
            mx = [m for m in (mae(e, T) for e in lk) if m is not None]
            pv = p95(mx)
            tails.append({"T": T, "trader": tr, "p95": round(pv, 2) if pv is not None else None})
            if pv is not None and pv <= 10.0: tail.add(tr)
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
                if tr in tail: G[f"TAIL_{d}"].append(row)
                elif tr in sel: G[f"EXCL_{d}"].append(row)
    per_review.append({"T": time.strftime("%Y-%m-%d", time.gmtime(T / 1000)), "sel": sorted(sel), "tail": sorted(tail), "pool": len(pool)})

# dropout within the next 7 reviews (SEL)
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
O["tails"] = tails


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = st.variance(a), st.variance(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va / len(a) + vb / len(b)), 2)


V = {}
for d in (2, 6):
    ex = G[f"EXCL_{d}"]; kp = G[f"TAIL_{d}"]
    V[d] = {"t_excl_minus_kept": welch([r["r"] for r in ex], [r["r"] for r in kp]),
            "sum_sel": [round(sum(r["r"] for r in G[f"SEL_{d}"] if (r["T"] < midT) == h), 1) for h in (True, False)],
            "sum_tail": [round(sum(r["r"] for r in kp if (r["T"] < midT) == h), 1) for h in (True, False)]}
O["verdict_parts"] = V
S_, T_ = O.get("SEL_2", {}), O.get("TAIL_2", {})
ok = all((T_.get(h, {}).get("mean") or -99) > (S_.get(h, {}).get("mean") or 99) for h in ("half1", "half2"))
ok = ok and V[2]["t_excl_minus_kept"] is not None and V[2]["t_excl_minus_kept"] <= -2
ok = ok and all(a > b for a, b in zip(V[2]["sum_tail"], V[2]["sum_sel"]))
O["verdict"] = "хвост-фильтр помогает" if ok else "хвост-фильтр не доказан"
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
