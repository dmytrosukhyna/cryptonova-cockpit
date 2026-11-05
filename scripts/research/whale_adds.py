"""H3 (side finding of the conviction study, so strict: t >= 3, both halves, OKX): when the whale ADDS (2nd sub-position of
the same coin+side within 4 h), is the rest of the trade (add+2 min -> close+2 min) negative for a copy that already holds the
first entry? Split: add after price moved AGAINST (averaging down) vs WITH. Negative mean => exit (or at least don't add) on add."""
import json, math, os, sys, statistics as st
sys.path.insert(0, "/home/claude/cryptonova-cockpit"); sys.path.insert(0, os.path.dirname(__file__))
import study as S0  # = scripts/whale_rules_study.py from branch research/whale-rules-0411, copied next to this file as study.py
D = S0.load_data(sys.argv[1]); now = int(D["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)
rows = []
for src, lst in (("hl", D.get("hl") or []), ("okx", D.get("okx") or [])):
    for w in lst:
        trips = S0.hl_trips(w, now) if src == "hl" else S0.okx_trips(w, now)
        by, eps = {}, []
        for t in sorted(trips, key=lambda x: x["open_t"]):
            k = (t["coin"], t["side"]); cur = by.get(k)
            if cur and t["open_t"] - cur["open_t"] <= 4 * S0.H1: cur["g"].append(t)
            else: cur = {"open_t": t["open_t"], "g": [t]}; by[k] = cur; eps.append(cur)
        for e in eps:
            g = e["g"]
            if len(g) < 2 or any(x.get("open") for x in g): continue
            Sx = W.get(g[0]["coin"])
            if Sx is None: continue
            t1, t2, tc = g[0]["open_t"], g[1]["open_t"], max(x["close_t"] for x in g)
            d2 = 2 * 60_000
            if t2 + d2 >= tc + d2 or t2 - t1 < 5 * 60_000: continue      # same-minute slices are one entry, not an add
            p1, p2 = Sx.price(t1 + d2), Sx.price(t2 + d2)
            if not p1 or not p2: continue
            s = 1 if g[0]["side"] == "LONG" else -1
            moved = s * (p2 / p1 - 1) * 100
            rest = S0.fwd(Sx, t2 + d2, tc + d2, g[0]["side"])
            if rest is None or not math.isfinite(rest): continue
            rest += S0.COST   # gross of trading costs: holding on costs nothing extra (funding still counted)
            rows.append({"src": src, "t": t2, "moved": moved, "rest": rest})
ts = sorted(r["t"] for r in rows); mid = ts[len(ts)//2] if ts else 0
def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2), "neg": round(sum(x < 0 for x in xs)/len(xs), 3)}
out = {}
for part, f in (("all", lambda r: 1), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid), ("okx", lambda r: r["src"] == "okx"), ("hl", lambda r: r["src"] == "hl")):
    rs = [r for r in rows if f(r)]
    out[part] = {"any": s([r["rest"] for r in rs]), "against": s([r["rest"] for r in rs if r["moved"] < -0.5]),
                 "flat": s([r["rest"] for r in rs if -0.5 <= r["moved"] <= 0.5]), "with": s([r["rest"] for r in rs if r["moved"] > 0.5])}
print(json.dumps(out, ensure_ascii=False, indent=0)); json.dump(out, open(sys.argv[2], "w"))
