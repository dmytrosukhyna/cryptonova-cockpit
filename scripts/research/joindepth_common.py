"""Backlog 164 (14.11.2026, research only): does the depth of the whale's own loss at the moment we join change the join's result?
Pre-registered (written before any result was seen):
- a join = entering a whale's decision that is already open, at lag L ∈ {60, 240, 720, 1440} min after his first entry, only while the
  price is inside the live window −3 % … +1 % of his notional-weighted entry known at L (as backlog 103);
- PRIMARY sample: one join per decision — the earliest lag inside the window (independent decisions); pooled (decision, lag) pairs are
  reported only as a check;
- gap = side-adjusted price / whale entry − 1 (% ; negative = the whale is in loss). Buckets fixed in advance:
  P «+0…+1» (whale in profit), G0 «0…−0.5», G1 «−0.5…−1.5», G3 «−1.5…−3»;
- copy: entry at L, exit 2 min after the whale's last close, stop −16 %, 0.11 % a round trip + funding;
- verdict «deep joins are worse» (candidate rule «join only while the whale's loss ≤ 1.5 %», money only with Dmytro's yes):
  in the primary sample mean(G3) < mean(G0) and < mean(G1) in BOTH halves of time, AND Welch t(G3 − G0∪G1) ≤ −2.2
  (2 sources × the one rule). Anything else: not passed."""
import math, statistics as st

LAGS = [60, 240, 720, 1440]
BUCKETS = [("P", 0.0, 1.0), ("G0", -0.5, 0.0), ("G1", -1.5, -0.5), ("G3", -3.0, -1.5)]


def bucket(gap):
    for name, lo, hi in BUCKETS:
        if lo <= gap <= hi if name == "P" else lo <= gap < hi:
            return name
    return None


def stat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.pstdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3), "sum": round(sum(xs), 1)}


def welch(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb > 0 else None


def report(joins, label):
    """joins: list of dicts {t, dec, L, gap, ret}. Returns the block and the verdict."""
    joins = sorted(({**r, "gap": float(r["gap"]), "ret": float(r["ret"])} for r in joins), key=lambda r: r["t"])
    first = {}
    for j in joins:
        if j["dec"] not in first or j["L"] < first[j["dec"]]["L"]:
            first[j["dec"]] = j
    prim = sorted(first.values(), key=lambda r: r["t"])
    O = {"label": label, "n_pairs": len(joins), "decisions": len(prim)}
    if len(prim) < 20:
        O["verdict"] = "мало данных"; return O
    mid = prim[len(prim) // 2]["t"]
    for samp, rs0 in (("primary", prim), ("pairs", joins)):
        blk = {}
        for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
            rs = [r for r in rs0 if f(r)]
            q = {b: stat([r["ret"] for r in rs if bucket(r["gap"]) == b]) for b, _, _ in BUCKETS}
            g3 = [r["ret"] for r in rs if bucket(r["gap"]) == "G3"]
            g01 = [r["ret"] for r in rs if bucket(r["gap"]) in ("G0", "G1")]
            q["t_G3_vs_G01"] = welch(g3, g01)
            q["byL"] = {str(L): {b: stat([r["ret"] for r in rs if r["L"] == L and bucket(r["gap"]) == b]).get("mean") for b, _, _ in BUCKETS}
                        for L in LAGS}
            blk[p] = q
        O[samp] = blk
    pb = O["primary"]
    worse = all((pb[h]["G3"].get("mean") is not None and pb[h]["G0"].get("mean") is not None and pb[h]["G1"].get("mean") is not None
                 and pb[h]["G3"]["mean"] < pb[h]["G0"]["mean"] and pb[h]["G3"]["mean"] < pb[h]["G1"]["mean"]) for h in ("h1", "h2"))
    t = pb["all"]["t_G3_vs_G01"]
    O["verdict"] = "прошло" if worse and t is not None and t <= -2.2 else "не прошло"
    O["why"] = f"G3 хуже G0 и G1 в обеих половинах: {worse}; t {t}"
    return O


def lines(O):
    out = [f"{O['label']}: решений {O.get('decisions')}, пар {O.get('n_pairs')} — {O.get('verdict')} ({O.get('why', '')})"]
    for samp in ("primary", "pairs"):
        if samp not in O:
            continue
        for p in ("all", "h1", "h2"):
            q = O[samp][p]
            out.append(f"  {samp} {p}: " + " ".join(f"{b}:{q[b].get('mean')}/n{q[b].get('n')}/pf{q[b].get('pf')}" for b, _, _ in BUCKETS)
                       + f" t(G3−G01) {q['t_G3_vs_G01']}")
    return out
