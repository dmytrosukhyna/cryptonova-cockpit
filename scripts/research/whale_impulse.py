"""Whale copy: are whale SHORTS into a pump knowledge or luck? (backlog 96, owner task 06.11.2026, research only)

Trigger: 05.11 (BTC rally) 8 of 10 whale shorts on the desk ended in profit, longs 3 of 15. Is shorting into an impulse a skill
we should weight, or luck of one day? Data: lab-data-whales (04.11.2026, i.e. 05.11 itself is NOT in the sample) →
whale_rules_convert.py; helpers from whale_rules_study.py next to this file as study.py. Usage: whale_impulse.py <conv>/whales.json.gz OUT.json

Pre-registered (written before looking at outcomes):
 Feature (known at OUR entry t0 + 2 min): coin return over the last 4 h and 24 h of closed 1h bars (Series.ret4 / ret24).
 Impulse = ret4 >= +3 % (window «4h») or ret24 >= +5 % (window «24h»). Thresholds fixed in advance, not tuned.
 Copy: entry open_t + 2 min, exit close_t + 2 min, protective stop −16 %, costs 0.11 % + funding (fwd()).
 Tests per window × pool (A every whale with >= 20 decisions; B only decisions of whales passing the live bar at that moment):
  T1 impulse shorts − other shorts (difference of means, Welch t);
  T2 impulse shorts vs placebo P1: random short of the same coin, entry uniform in ±3.5 days, same hold (300 reps);
  T3 impulse shorts vs placebo P2: random short of the same coin at a random OTHER impulse hour (same threshold) within ±14 days,
     same hold (300 reps) — does the whale's timing add anything beyond the plain rule «fade the pump»?
 4 looks (2 windows × 2 pools) → «knowledge» only if: T1 t >= 2.5, positive in both time halves, T2 p <= 0.0125 and n >= 30.
 Descriptive: the same for LONGS after an impulse (momentum side), and P2 alone (= the plain rule without whales).
"""
import json, math, os, random, sys, statistics as st
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0

DAY, MIN, H1 = 86_400_000, 60_000, 3_600_000
DL = 2
TH = {"4h": ("ret4", 0.03), "24h": ("ret24", 0.05)}
RNG = random.Random(96)
REPS = 300

D = S0.load_data(sys.argv[1]); now = int(D["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)

whales = []
for w in D.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in D.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

rows = []
for src, name, trips in whales:
    eps = S0.episodes(sorted(trips, key=lambda x: x["open_t"]))
    if len(eps) < 20: continue
    done = []
    for e in eps:
        t0 = e["open_t"]
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        done.append((t0, e["close_t"], e["pnl"]))
        Sx = W.get(e["coin"])
        if Sx is None or e.get("open") or e["close_t"] - t0 <= DL * MIN: continue
        te, tx = t0 + DL * MIN, e["close_t"] + DL * MIN
        i = Sx.idx(te)
        if i < 30: continue
        v = S0.fwd(Sx, te, tx, e["side"], stop=16.0)
        if v is None or not math.isfinite(v): continue
        rows.append({"src": src, "w": name, "coin": e["coin"], "side": e["side"], "t": te, "hold": tx - te, "bar": bar, "r": v,
                     "ret4": float(Sx.ret4[i]), "ret24": float(Sx.ret24[i])})

ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]


def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None}


def diff(a, b):
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return {"d": round(st.mean(a) - st.mean(b), 3), "t": round((st.mean(a) - st.mean(b)) / se, 2) if se else None}


_imp_cache = {}
def impulse_hours(coin, feat, thr):
    k = (coin, feat)
    if k not in _imp_cache:
        S = W.get(coin); a = getattr(S, feat)
        _imp_cache[k] = np.flatnonzero(np.nan_to_num(a, nan=-1) >= thr)
    return _imp_cache[k]


def placebo(sel, side, mode, feat=None, thr=None):
    """→ (list of rep means, plain-rule mean over all draws)."""
    means, allr = [], []
    for _ in range(REPS):
        rs = []
        for r in sel:
            S = W.get(r["coin"]); tend = int(S.t[-1]) - r["hold"] - H1; tbeg = int(S.t[0]) + 30 * H1
            if mode == "P1":
                lo, hi = max(tbeg, r["t"] - int(3.5 * DAY)), min(tend, r["t"] + int(3.5 * DAY))
                if hi <= lo: continue
                tt = RNG.randrange(lo, hi)
            else:
                idx = impulse_hours(r["coin"], feat, thr)
                tl = S.t[idx] + H1 + DL * MIN                 # entry just after the bar that showed the impulse
                ok = idx[(np.abs(tl - r["t"]) <= 14 * DAY) & (np.abs(tl - r["t"]) > 6 * H1) & (tl < tend) & (tl > tbeg)]
                if len(ok) == 0: continue
                tt = int(S.t[ok[RNG.randrange(len(ok))]]) + H1 + DL * MIN
            v = S0.fwd(S, tt, tt + r["hold"], side, stop=16.0)
            if v is not None and math.isfinite(v): rs.append(v)
        if rs: means.append(st.mean(rs)); allr += rs
    return means, (round(st.mean(allr), 3) if allr else None)


def pv(actual, means):
    if not means: return None
    return round((sum(1 for m in means if m >= actual) + 1) / (len(means) + 1), 4)


out = {"made": now, "n_rows": len(rows), "mid_t": mid, "whales": len({r['w'] for r in rows}),
       "n_short": sum(r["side"] == "SHORT" for r in rows), "n_long": sum(r["side"] == "LONG" for r in rows)}
for pool, rs0 in (("A_all", rows), ("B_bar", [r for r in rows if r["bar"]])):
    P = {}
    for wn, (feat, thr) in TH.items():
        H = {}
        for side in ("SHORT", "LONG"):
            ss = [r for r in rs0 if r["side"] == side]
            imp = [r for r in ss if r[feat] >= thr]; oth = [r for r in ss if r[feat] < thr]
            x = {"imp": s([r["r"] for r in imp]), "other": s([r["r"] for r in oth]),
                 "imp_minus_other": diff([r["r"] for r in imp], [r["r"] for r in oth])}
            for part, f in (("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid),
                            ("hl", lambda r: r["src"] == "hl"), ("okx", lambda r: r["src"] == "okx")):
                x[part] = {"imp": s([r["r"] for r in imp if f(r)]), "other": s([r["r"] for r in oth if f(r)]),
                           "d": diff([r["r"] for r in imp if f(r)], [r["r"] for r in oth if f(r)])}
            if len(imp) >= 10:
                act = st.mean([r["r"] for r in imp])
                m1, base1 = placebo(imp, side, "P1"); m2, base2 = placebo(imp, side, "P2", feat, thr)
                x["P1"] = {"p": pv(act, m1), "placebo_mean": base1, "q05_95": [round(float(np.percentile(m1, 5)), 3), round(float(np.percentile(m1, 95)), 3)] if m1 else None}
                x["P2_rule"] = {"p": pv(act, m2), "placebo_mean": base2, "q05_95": [round(float(np.percentile(m2, 5)), 3), round(float(np.percentile(m2, 95)), 3)] if m2 else None}
                bw = {}
                for r in imp: bw.setdefault(r["w"], []).append(r["r"])
                x["by_whale"] = sorted(([w, len(v), round(sum(v), 1)] for w, v in bw.items()), key=lambda z: -z[1])[:10]
            H[side] = x
        P[wn] = H
    out[pool] = P
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:12000])
