"""Whale copy: should we skip a whale's reversals and bursts? (backlog 89, 05.11.2026, research only)

Trigger: 05.11 Michael123456789 (okx:7F2323E7F25F4D68) reversed ADA/DOT/LINK/FIL/ARB 38 times in ~6 h (median hold 8 min,
60-day median 18 h). Demo desk +$4 on those 38, real account −$100+ in 15 min (copies paid entry+exit on each flip).

Data: lab-data-whales (whale_rules_data.json.gz, 04.11.2026) → whale_rules_convert.py; functions from whale_rules_study.py
(branch research/whale-rules-0411) next to this file as study.py.

Pre-registered (before looking at outcomes), 2 hypotheses, both known at OUR entry time (no look-ahead):
 H1 flip:  the decision opens within 30 min after the SAME whale closed the OPPOSITE side of the SAME coin.
 H2 burst: the whale opened >= max(6, 3 × his own median trips/day over the prior 60 d) trips in the 24 h before this decision.
A rule «skip such copies» passes only if: the group's mean copy return < 0 with t <= −2.5 (2 tests), negative in both time halves
and in both sources where n >= 50, and skipping raises total P&L in both halves. Two pools: A every whale with >= 20 decisions,
B only decisions of whales passing the live bar at that moment (prior 60 d closed decisions: win >= 70 %, PF >= 2, >= 20, P&L > 0).
Copy: enter open_t + 2 min, exit close_t + 2 min, protective stop −16 % (live since 05.11), costs 0.11 % + funding (fwd()).
Info only (not a rule — not known at entry): decisions the whale held < 15 min.
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

rows = []
for src, name, trips in whales:
    trips = sorted(trips, key=lambda x: x["open_t"])
    eps = S0.episodes(trips)
    if len(eps) < 20: continue
    opens = [t["open_t"] for t in trips]
    closes = sorted((t["close_t"], t["coin"], t["side"]) for t in trips if not t.get("open"))
    done = []
    for e in eps:
        t0 = e["open_t"]
        # H1: opposite side of the same coin closed in (t0 − 30 min, t0]
        opp = "SHORT" if e["side"] == "LONG" else "LONG"
        flip = any(c == e["coin"] and s == opp and t0 - 30 * MIN <= ct <= t0 + MIN for ct, c, s in closes)
        # H2: trips opened in the prior 24 h vs his own median per day over the prior 60 d
        n24 = sum(1 for o in opens if t0 - DAY <= o < t0)
        prior = [o for o in opens if t0 - 60 * DAY <= o < t0 - DAY]
        burst = None
        if prior and t0 - min(opens) >= 20 * DAY:
            days = {}
            for o in prior: days[o // DAY] = days.get(o // DAY, 0) + 1
            span = max(1, int((t0 - DAY - max(min(opens), t0 - 60 * DAY)) // DAY))
            per_day = sorted(list(days.values()) + [0] * max(0, span - len(days)))
            med = per_day[len(per_day) // 2] if per_day else 0
            burst = n24 >= max(6, 3 * med)
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        done.append((t0, e["close_t"], e["pnl"]))
        Sx = W.get(e["coin"])
        if Sx is None or e.get("open"): continue
        D2 = 2 * MIN
        if e["close_t"] - t0 <= D2: continue
        r = S0.fwd(Sx, t0 + D2, e["close_t"] + D2, e["side"], stop=16.0)
        if r is None or not math.isfinite(r): continue
        rows.append({"src": src, "w": name, "t": t0, "flip": flip, "burst": burst, "bar": bar, "r": r,
                     "short": (e["close_t"] - t0) < 15 * MIN})

ts = sorted(r["t"] for r in rows); mid = ts[len(ts) // 2]
def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "sum": round(sum(xs), 1)}

out = {"made": now, "n_rows": len(rows), "mid_t": mid, "whales": len({r['w'] for r in rows})}
for pool, rs0 in (("A_all", rows), ("B_bar", [r for r in rows if r["bar"]])):
    P = {"all": s([r["r"] for r in rs0])}
    for key in ("flip", "burst", "short"):
        H = {}
        for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid),
                        ("hl", lambda r: r["src"] == "hl"), ("okx", lambda r: r["src"] == "okx")):
            rs = [r for r in rs0 if f(r)]
            H[part] = {"yes": s([r["r"] for r in rs if r[key] is True]), "no": s([r["r"] for r in rs if r[key] is False])}
        P[key] = H
    # who carries the flips
    fw = {}
    for r in rs0:
        if r["flip"]: fw.setdefault(r["w"], []).append(r["r"])
    P["flip_by_whale"] = sorted(([w, len(v), round(sum(v), 1)] for w, v in fw.items()), key=lambda x: -x[1])[:12]
    out[pool] = P
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:8000])
