"""Whale copy: does copy size scaled by the whale's conviction beat equal size? (05.11.2026, research only)

Data: lab-data-whales (whale_rules_data.json.gz, 04.11.2026) converted by whale_rules_convert.py; functions from
whale_rules_study.py (branch research/whale-rules-0411). BTC regime not needed (btc=None).

Pre-registered (before looking at outcomes), 2 hypotheses:
 H1 size:  conviction = notional of the FIRST sub-position of a decision / median of the same whale's previous 20 decisions
           (only known at entry, no look-ahead). Buckets: low < 0.67, mid, high >= 1.5.
 H2 lev:   leverage of the first sub-position / median of the previous 20 (OKX + HL where known). Same buckets.
Pass bar: high − low > 0 with t >= 2.5 (≈ 2 tests × 3 buckets, Bonferroni-ish), same sign in both time halves and both sources
(where n >= 50), and the scaled-size portfolio beats equal size by P&L / sd in both halves.
Copy: enter open_t + 2 min, exit close_t + 2 min, protective stop −8 %, costs 0.11 % + funding (fwd()).
Pools: A — every whale with >= 20 decisions; B — only decisions of whales that pass the live bar at that moment
(prior 60 d, decisions already closed by then: win >= 70 %, PF >= 2, >= 20, P&L > 0; live bar also marks open ones — so B is looser).
"""
import gzip, json, math, os, sys, statistics as st
sys.path.insert(0, "/home/claude/cryptonova-cockpit"); sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import study as S0  # = scripts/whale_rules_study.py from branch research/whale-rules-0411, copied next to this file as study.py

DAY = 86_400_000
D = S0.load_data(sys.argv[1]); now = int(D["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)

whales = []
for w in D.get("hl") or []:
    if len(w["fills"]) < 8: continue
    tr = S0.hl_trips(w, now)
    whales.append(("hl", w.get("name") or w["addr"][:10], tr))
for t in D.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

def ratio(xs, v):
    xs = [x for x in xs if x and x > 0]
    if len(xs) < 10 or not v or v <= 0: return None
    return v / st.median(xs)

rows = []
for src, name, trips in whales:
    # episodes, but keep the first sub-position's size/lev (known at entry)
    by = {}
    eps = []
    for t in sorted(trips, key=lambda x: x["open_t"]):
        k = (t["coin"], t["side"]); cur = by.get(k)
        if cur and t["open_t"] - cur["open_t"] <= 4 * S0.H1:
            cur["g"].append(t)
        else:
            cur = {"open_t": t["open_t"], "g": [t]}; by[k] = cur; eps.append(cur)
    eps.sort(key=lambda e: e["open_t"])
    if len(eps) < 20: continue
    hist_n, hist_l, done = [], [], []   # done = (close_t, whale ret, pnl) for the live-bar check
    for e in eps:
        g = e["g"]; f0 = g[0]
        close_t = max(x["close_t"] for x in g)
        wret = sum(x["ret"] * max(x["notional"], 1e-9) for x in g) / sum(max(x["notional"], 1e-9) for x in g)
        pnl = sum(x["pnl"] for x in g)
        rn = ratio(hist_n[-20:], f0["notional"]); rl = ratio(hist_l[-20:], f0.get("lev"))
        # live bar at entry: decisions opened in the prior 60 d and already closed before this entry (no look-ahead)
        pri = [d for d in done if d[1] < e["open_t"] and d[0] >= e["open_t"] - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        hist_n.append(f0["notional"]); hist_l.append(f0.get("lev"))
        done.append((e["open_t"], close_t, pnl))
        Sx = W.get(f0["coin"])
        if Sx is None or e.get("open") or any(x.get("open") for x in g): continue
        D2 = 2 * 60_000
        if close_t - e["open_t"] <= D2: continue
        r = S0.fwd(Sx, e["open_t"] + D2, close_t + D2, f0["side"], stop=8.0)
        if r is None or not math.isfinite(r): continue
        rows.append({"src": src, "w": name, "t": e["open_t"], "rn": rn, "rl": rl, "bar": bar, "r": r, "n_sub": len(g)})

ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
def bk(v): return None if v is None else ("low" if v < 0.67 else "high" if v >= 1.5 else "mid")
def diff(hi, lo):
    if len(hi) < 10 or len(lo) < 10: return None
    d = st.mean(hi) - st.mean(lo); se = math.sqrt(st.pvariance(hi) / len(hi) + st.pvariance(lo) / len(lo))
    return {"d": round(d, 3), "t": round(d / se, 2) if se else None}
def scaled(rs, key):
    # equal size vs size ∝ clip(ratio, 0.5, 2) (ratio unknown → 1): total and P&L/sd
    eq = [r["r"] for r in rs]; sc = [r["r"] * min(2, max(0.5, r[key] or 1)) for r in rs]
    def q(x): return {"sum": round(sum(x), 1), "per_sd": round(st.mean(x) / st.pstdev(x), 4) if len(x) > 1 and st.pstdev(x) else None}
    return {"equal": q(eq), "scaled": q(sc)}

out = {"made": now, "n_rows": len(rows), "mid_t": mid, "whales": len({r['w'] for r in rows})}
for pool, rs0 in (("A_all", rows), ("B_bar", [r for r in rows if r["bar"]])):
    P = {}
    for key in ("rn", "rl"):
        H = {}
        for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid),
                        ("hl", lambda r: r["src"] == "hl"), ("okx", lambda r: r["src"] == "okx")):
            rs = [r for r in rs0 if f(r)]
            b = {x: [r["r"] for r in rs if bk(r[key]) == x] for x in ("low", "mid", "high")}
            H[part] = {"buckets": {x: s(v) for x, v in b.items()}, "hi_lo": diff(b["high"], b["low"]), "scale": scaled(rs, key)}
        P[key] = H
    # bonus (not a hypothesis): adds within 4 h (n_sub >= 2) vs single
    P["adds_info"] = {"single": s([r["r"] for r in rs0 if r["n_sub"] == 1]), "multi": s([r["r"] for r in rs0 if r["n_sub"] >= 2])}
    P["all"] = s([r["r"] for r in rs0])
    out[pool] = P
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:6000])
