"""Backlog 153 (14.11.2026, 03:40 Athens): a seat in column A only by repeatable decisions — OKX/HL, lab-data-whales. Research only.
Pre-registered: repeatable = not a TradFi ticker (tradfi.py: stocks/ETFs/metals/oil — no Hyperliquid main-dex price, the live desk
cannot copy them). BASE = live bar on all decisions (unrepeatable: whale P&L counts, copy test 0); VAR = bar on repeatable decisions
only AND repeatable share >= 50 %. Top-8 seats by 60-d whale P&L; forward = repeatable decisions in [T, T+3 d), copy +2 min (robust
+6 min), stop -16 %, costs. Pass: VAR sum > BASE in both halves, paired per-review t >= 2, mean/copy not worse by > 0.1 pp. 1 variant.
Usage as whale_selroll.py (study.py next to this file). Machinery below copied from whale_selroll.py (backlog 151).
Backlog 151, 13.11.2026: backlog 149 («select at a review → copy until the next review») on OKX/HL (lab-data-whales, 08.08–04.11).
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



from tradfi import TRADFI
def rep(e): return e["coin"] not in TRADFI and ":" not in e["coin"]
SEATS = 8
def seat(lst, T, repeat_only):
    lk = [e for e in lst if T - LOOK <= e["open_t"] < T]
    lk_r = [e for e in lk if rep(e)]
    if repeat_only:
        if len(lk) == 0 or len(lk_r) / len(lk) < 0.5: return None
        use = lk_r
    else:
        use = lk
    if len(use) < 20: return None
    vals = []
    for e in use:
        x = look_val(e, T, False)
        if x is None: continue
        vals.append(x if rep(e) else (x[0], 0.0))   # unrepeatable: whale P&L counts, copy test 0 (no price live)
    if not bar_ok(vals): return None
    return sum(a for a, _ in vals), len(lk_r) / len(lk)

R = []
for T in reviews:
    row = {"T": T}
    for mode in ("BASE", "VAR"):
        cand = []
        for tr, lst in by_tr.items():
            s = seat(lst, T, mode == "VAR")
            if s: cand.append((s[0], tr, s[1]))
        cand.sort(reverse=True); top = cand[:SEATS]
        for d in (2, 6):
            rs = []; empty = 0
            for _, tr, sh in top:
                k = 0
                for e in by_tr[tr]:
                    if T <= e["open_t"] < T + STEP and rep(e):
                        r = fwd(e, d)
                        if r is not None: rs.append(r); k += 1
                empty += (k == 0)
            row[f"{mode}_{d}"] = {"sum": sum(rs), "n": len(rs), "empty": empty, "seats": len(top),
                                  "low": sum(1 for _, _, sh in top if sh < 0.5), "who": [t for _, t, _ in top]}
    R.append(row)
act = [r for r in R if r["BASE_2"]["seats"] >= 3 or r["VAR_2"]["seats"] >= 3]
mid = len(act) // 2
def pt(xs):
    if len(xs) < 2: return None
    m, sd = st.mean(xs), st.stdev(xs); return round(m / (sd / math.sqrt(len(xs))), 2) if sd else None
O = {"reviews": len(reviews), "active": len(act)}
for d in (2, 6):
    for part, rr in (("all", act), ("h1", act[:mid]), ("h2", act[mid:])):
        b = [r[f"BASE_{d}"] for r in rr]; v = [r[f"VAR_{d}"] for r in rr]
        diff = [y["sum"] - x["sum"] for x, y in zip(b, v)]
        O[f"{part}_{d}"] = {"base_sum": round(sum(x["sum"] for x in b), 1), "var_sum": round(sum(x["sum"] for x in v), 1),
            "base_n": sum(x["n"] for x in b), "var_n": sum(x["n"] for x in v),
            "base_mean": round(sum(x["sum"] for x in b) / max(1, sum(x["n"] for x in b)), 3),
            "var_mean": round(sum(x["sum"] for x in v) / max(1, sum(x["n"] for x in v)), 3),
            "base_empty": sum(x["empty"] for x in b), "var_empty": sum(x["empty"] for x in v),
            "seats_b": sum(x["seats"] for x in b), "seats_v": sum(x["seats"] for x in v),
            "base_low_share_seats": sum(x["low"] for x in b), "diff_t": pt(diff), "reviews_differ": sum(1 for x in diff if abs(x) > 1e-9)}
A = O["all_2"]; h1, h2 = O["h1_2"], O["h2_2"]
O["verdict"] = "прошло" if (h1["var_sum"] > h1["base_sum"] and h2["var_sum"] > h2["base_sum"] and (A["diff_t"] or 0) >= 2.0
                            and A["var_mean"] >= A["base_mean"] - 0.1) else "не прошло"
O["changed"] = [{"T": time.strftime("%Y-%m-%d", time.gmtime(r["T"] / 1000)), "out": sorted(set(r["BASE_2"]["who"]) - set(r["VAR_2"]["who"])),
                 "in": sorted(set(r["VAR_2"]["who"]) - set(r["BASE_2"]["who"])), "d": round(r["VAR_2"]["sum"] - r["BASE_2"]["sum"], 1)} for r in act]
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for k, v in O.items():
    if k != "changed": print(k, v)
for c in O["changed"]: print(c)
# extra (descriptive): BASE forward copies split by the seat's repeatable share
LOWr, HIr = [], []
for T in [r["T"] for r in act]:
    cand = []
    for tr, lst in by_tr.items():
        s = seat(lst, T, False)
        if s: cand.append((s[0], tr, s[1]))
    cand.sort(reverse=True)
    for _, tr, sh in cand[:SEATS]:
        for e in by_tr[tr]:
            if T <= e["open_t"] < T + STEP and rep(e):
                r = fwd(e, 2)
                if r is not None: (LOWr if sh < 0.5 else HIr).append(r)
print("BASE seats share<50%:", tstat(LOWr), "share>=50%:", tstat(HIr))
