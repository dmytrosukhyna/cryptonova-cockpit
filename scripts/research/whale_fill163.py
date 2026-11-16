"""Backlog 163 (16.11.2026, 15:30 Athens): «пустое место в A» — OKX/HL, lab-data-whales (08.08–04.11). Research only.
Pre-registered before any result was seen (1 variant):
- At review T (every 3 days) the seats are exactly whale_repeat_okx.py BASE: bar on all decisions (unrepeatable: whale P&L counts,
  copy test 0), ranked by 60-d whale P&L; A = top-8, B = ranks 9+ in order.
- An A seat is EMPTY if its trader opened no repeatable decision in [T - 3 d, T) (the previous window: what the desk can know at T).
- TOP8 = forward copies of A only. FILL = TOP8 plus, for each empty A seat, the next B trader in rank order (the A trader stays
  followed — the lock keeps him); the B trader is copied only for [T, T + 3 d). No activity requirement on B.
- Forward exactly as whale_repeat_okx.py: repeatable decisions opened in [T, T+3 d), copy +2 min (robust +6 min), whale exit +2 min,
  stop -16 %, 0.11 % round trip + funding; open at data end marked at the end.
- Pass: FILL sum > TOP8 sum in both halves of active reviews AND paired per-review diff t >= 2 AND mean of the added copies > 0
  AND FILL mean/copy >= TOP8 mean/copy - 0.1 pp; +6 min must not flip the sign of the total difference.
Machinery copied from whale_repeat_okx.py (backlog 153). Usage: whale_fill163.py <conv>/whales.json.gz OUT.json
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


def fwd_tr(tr, T, d):
    return [r for e in by_tr[tr] if T <= e["open_t"] < T + STEP and rep(e) for r in [fwd(e, d)] if r is not None]

def active_prev(tr, T):
    return any(T - STEP <= e["open_t"] < T and rep(e) for e in by_tr[tr])

R = []
for T in reviews:
    cand = []
    for tr, lst in by_tr.items():
        s = seat(lst, T, False)
        if s: cand.append((s[0], tr))
    cand.sort(reverse=True)
    A = [t for _, t in cand[:SEATS]]; B = [t for _, t in cand[SEATS:]]
    emp = [t for t in A if not active_prev(t, T)]
    add = B[:len(emp)]
    row = {"T": T, "seats": len(A), "empty": len(emp), "added": add, "b_len": len(B)}
    for d in (2, 6):
        base = [r for t in A for r in fwd_tr(t, T, d)]
        extra = [r for t in add for r in fwd_tr(t, T, d)]
        row[d] = {"base": base, "extra": extra}
        # descriptive: did the empty A seats trade forward anyway?
        row[f"emp_fwd_{d}"] = [r for t in emp for r in fwd_tr(t, T, d)]
    R.append(row)
act = [r for r in R if r["seats"] >= 3]
mid = len(act) // 2
def pt(xs):
    if len(xs) < 2: return None
    m, sd = st.mean(xs), st.stdev(xs); return round(m / (sd / math.sqrt(len(xs))), 2) if sd else None
O = {"reviews": len(reviews), "active": len(act), "empty_seats": sum(r["empty"] for r in act),
     "reviews_with_empty": sum(1 for r in act if r["empty"]), "replacements": sum(len(r["added"]) for r in act)}
for d in (2, 6):
    for part, rr in (("all", act), ("h1", act[:mid]), ("h2", act[mid:])):
        b = [x for r in rr for x in r[d]["base"]]; ex = [x for r in rr for x in r[d]["extra"]]
        diff = [sum(r[d]["extra"]) for r in rr]
        O[f"{part}_{d}"] = {"top8_sum": round(sum(b), 1), "fill_sum": round(sum(b) + sum(ex), 1), "top8": tstat(b), "added": tstat(ex),
                            "fill_mean": round((sum(b) + sum(ex)) / max(1, len(b) + len(ex)), 3), "diff_t": pt(diff),
                            "emp_fwd": tstat([x for r in rr for x in r[f"emp_fwd_{d}"]])}
A_, h1, h2 = O["all_2"], O["h1_2"], O["h2_2"]
ok = (h1["fill_sum"] > h1["top8_sum"] and h2["fill_sum"] > h2["top8_sum"] and (A_["diff_t"] or 0) >= 2.0
      and (A_["added"].get("mean") or -1) > 0 and A_["fill_mean"] >= (A_["top8"].get("mean") or 0) - 0.1
      and (O["all_6"]["fill_sum"] - O["all_6"]["top8_sum"]) * (A_["fill_sum"] - A_["top8_sum"]) > 0)
O["verdict"] = "прошло" if ok else "не прошло"
O["rows"] = [{"T": time.strftime("%Y-%m-%d", time.gmtime(r["T"] / 1000)), "empty": r["empty"], "added": r["added"],
              "d": round(sum(r[2]["extra"]), 1), "n": len(r[2]["extra"])} for r in act]
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for k, v in O.items():
    if k != "rows": print(k, v)
for c in O["rows"]: print(c)
