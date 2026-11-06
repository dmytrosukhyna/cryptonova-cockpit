"""Whale copy: are SMALL whales worse to copy? (backlog 92, 06.11.2026, research only)

Trigger: 05.11 Michael123456789 (positions $0.6–2.2k, smaller than our copy) gave 12 real copies −$66 (73 % of the day's loss).

Data: lab-data-whales (whale_rules_data.json.gz, 04.11.2026) → whale_rules_convert.py; functions from whale_rules_study.py
(branch research/whale-rules-0411) next to this file as study.py. Usage: whale_size.py <conv>/whales.json.gz OUT.json

Pre-registered (before looking at outcomes):
 Feature (known at OUR entry): median notional of the same whale's decisions opened in the prior 30 days (>= 5 of them).
 Buckets: small < $5k, mid $5–50k, large > $50k. Copy delays 2 and 6 min (live median 341 s). 1 hypothesis («small is worse»)
 × 2 delays × 2 pools = 4 looks → pass bar t <= −2.5 on the difference small − rest.
 A filter «skip whales whose 30-day median position < $5k» passes only if: small − rest < 0 with t <= −2.5, negative in both
 time halves and (where n >= 50) both sources, and small's own mean <= 0 or its PF < 1.2 in pool B.
 Pools: A every whale with >= 20 decisions; B only decisions of whales passing the live bar at that moment.
 Copy: enter open_t + delay, exit close_t + delay, protective stop −16 %, costs 0.11 % + funding (fwd()).
 Also reported: share of decisions held < 15 min per bucket (flips), PF, per-source split.
"""
import json, math, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0

DAY, MIN = 86_400_000, 60_000
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

def bucket(v):
    return None if v is None else ("small" if v < 5_000 else "mid" if v < 50_000 else "large")

rows = []
for src, name, trips in whales:
    eps = S0.episodes(sorted(trips, key=lambda x: x["open_t"]))
    if len(eps) < 20: continue
    done = []
    for e in eps:
        t0 = e["open_t"]
        prev = [d for d in done if t0 - 30 * DAY <= d[0] < t0]
        med = st.median([d[3] for d in prev]) if len(prev) >= 5 else None
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        done.append((t0, e["close_t"], e["pnl"], e["notional"]))
        Sx = W.get(e["coin"])
        if Sx is None or e.get("open") or med is None: continue
        r = {"src": src, "w": name, "t": t0, "b": bucket(med), "med": med, "own": bucket(e["notional"]), "bar": bar,
             "short": (e["close_t"] - t0) < 15 * MIN}
        ok = True
        for dl in (2, 6):
            if e["close_t"] - t0 <= dl * MIN: ok = False; break
            v = S0.fwd(Sx, t0 + dl * MIN, e["close_t"] + dl * MIN, e["side"], stop=16.0)
            if v is None or not math.isfinite(v): ok = False; break
            r[f"r{dl}"] = v
        if ok: rows.append(r)

ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]

def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None, "sum": round(sum(xs), 1)}

def diff(a, b):
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return {"d": round(st.mean(a) - st.mean(b), 3), "t": round((st.mean(a) - st.mean(b)) / se, 2) if se else None}

out = {"made": now, "n_rows": len(rows), "mid_t": mid, "whales": len({r['w'] for r in rows}),
       "whales_by_bucket": {b: len({r["w"] for r in rows if r["b"] == b}) for b in ("small", "mid", "large")}}
for pool, rs0 in (("A_all", rows), ("B_bar", [r for r in rows if r["bar"]])):
    P = {}
    for dl in (2, 6):
        k = f"r{dl}"; H = {}
        for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid),
                        ("hl", lambda r: r["src"] == "hl"), ("okx", lambda r: r["src"] == "okx")):
            rs = [r for r in rs0 if f(r)]
            H[part] = {b: s([r[k] for r in rs if r["b"] == b]) for b in ("small", "mid", "large")}
            H[part]["small_minus_rest"] = diff([r[k] for r in rs if r["b"] == "small"], [r[k] for r in rs if r["b"] != "small"])
        P[k] = H
    P["short_share"] = {b: round(sum(r["short"] for r in rs0 if r["b"] == b) / max(1, sum(1 for r in rs0 if r["b"] == b)), 3)
                        for b in ("small", "mid", "large")}
    P["own_size_r2"] = {b: s([r["r2"] for r in rs0 if r["own"] == b]) for b in ("small", "mid", "large")}
    sw = {}
    for r in rs0:
        if r["b"] == "small": sw.setdefault(r["w"], []).append(r["r2"])
    P["small_by_whale"] = sorted(([w, len(v), round(sum(v), 1)] for w, v in sw.items()), key=lambda x: -x[1])[:12]
    out[pool] = P
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:9000])
