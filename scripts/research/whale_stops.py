"""Stop distance / leverage for whale copies (05.11.2026, owner: «проверь, исследуй и сделай как можно лучше»).
Copy +2 min, exit = whale exit +2 min, costs 0.11 % + funding. Size by risk: a trade risks 1 R = stop distance, so the result in R is
ret / stop (a tighter stop = a bigger position for the same risk). Variants (pre-registered, 6):
 S4 (≈ 20×), S5 (≈ 16×), S8 (≈ 10×, now), S12 (≈ 6×), S16 (≈ 5×),
 SW «как у кита»: stop = 80 / whale leverage % (the widest stop that still comes before liquidation at the whale's own leverage),
    capped 4…16 %; whale leverage unknown → 8 %.
Pools: all whales with ≥ 20 decisions; «good» = whales with PF ≥ 2 and ≥ 70 % wins on their own closed decisions in the FIRST half
(selection on h1, judged on h2 only — walk-forward)."""
import json, math, os, sys, statistics as st
sys.path.insert(0, "/home/claude/cryptonova-cockpit"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0  # = scripts/whale_rules_study.py from branch research/whale-rules-0411, copied next to this file as study.py
D = S0.load_data(sys.argv[1]); now = int(D["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)
VAR = {"S4": 4, "S5": 5, "S8": 8, "S12": 12, "S16": 16, "SW": None}
rows = []
for src, lst in (("hl", D.get("hl") or []), ("okx", D.get("okx") or [])):
    for w in lst:
        trips = S0.hl_trips(w, now) if src == "hl" else S0.okx_trips(w, now)
        name = (w.get("name") or w.get("addr") or w.get("code") or "?")
        by, eps = {}, []
        for t in sorted(trips, key=lambda x: x["open_t"]):
            k = (t["coin"], t["side"]); cur = by.get(k)
            if cur and t["open_t"] - cur["open_t"] <= 4 * S0.H1: cur["g"].append(t)
            else: cur = {"open_t": t["open_t"], "g": [t]}; by[k] = cur; eps.append(cur)
        if len(eps) < 20: continue
        for e in eps:
            g = e["g"]
            if any(x.get("open") for x in g): continue
            Sx = W.get(g[0]["coin"])
            if Sx is None: continue
            t0, tc = g[0]["open_t"] + 120_000, max(x["close_t"] for x in g) + 120_000
            if tc <= t0: continue
            lev = g[0].get("lev")
            wret = sum(x["ret"] * max(x["notional"], 1e-9) for x in g) / sum(max(x["notional"], 1e-9) for x in g)
            r = {"src": src, "w": name, "t": t0, "wret": wret}
            ok = True
            for k, s in VAR.items():
                sv = s if s else (min(16, max(4, 80 / lev)) if lev and lev > 0 else 8)
                v = S0.fwd(Sx, t0, tc, g[0]["side"], stop=sv)
                if v is None or not math.isfinite(v): ok = False; break
                r[k] = v / sv
            if ok: rows.append(r)
rows.sort(key=lambda r: r["t"]); mid = rows[len(rows) // 2]["t"]
# «good» whales chosen on the first half (their own result, after costs), judged on the second half only
h1 = {}
for r in rows:
    if r["t"] < mid: h1.setdefault(r["w"], []).append(r["wret"] - 0.11)
good = {w for w, xs in h1.items() if len(xs) >= 20 and sum(x > 0 for x in xs) / len(xs) >= 0.70
        and (sum(x for x in xs if x > 0) / max(1e-9, -sum(x for x in xs if x < 0))) >= 2}
def stat(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); eq = 0; pk = 0; dd = 0
    for x in xs: eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    return {"n": len(xs), "R": round(m, 4), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None, "sumR": round(sum(xs), 1),
            "ddR": round(dd, 1), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "stopped_like": round(sum(x <= -0.99 for x in xs) / len(xs), 3)}
out = {"n": len(rows), "good_whales": sorted(good), "mid_t": mid}
for part, f in (("all", lambda r: 1), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid), ("okx", lambda r: r["src"] == "okx"),
                ("good_h2", lambda r: r["t"] >= mid and r["w"] in good)):
    rs = [r for r in rows if f(r)]
    out[part] = {k: stat([r[k] for r in rs]) for k in VAR}
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for p in ("all", "h1", "h2", "okx", "good_h2"):
    print(p, {k: (v.get("n"), v.get("R"), v.get("t"), v.get("ddR"), v.get("stopped_like")) for k, v in out[p].items()})
print("good", len(good))
