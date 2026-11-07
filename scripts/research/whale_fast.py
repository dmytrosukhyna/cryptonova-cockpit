"""Backlog 91 (07.11.2026, research only): can a whale's FAST decision (hold < 15 min) be recognised at the open, and does a
minimum hold on the copy help for whales that flip fast? Pre-registered (written before looking at results):
- Population as whale_join.py: decisions (entries of one coin+side within 4 h) of OKX/HL whales passing the live 60-day bar at
  the open (≥ 20 decisions, win ≥ 70 %, PF ≥ 2, P&L > 0). Copy: entry +2 min, exit 2 min after the whale's last close, stop −16 %,
  0.11 % + funding; positions open at the data end marked at the last price.
- Fast = whale hold < 15 min. Predictors known at the open (4): P1 whale's share of fast decisions among his decisions closed
  in the prior 30 days (≥ 10 closed) > 30 %; P2 leverage of the first slice ≥ 20×; P3 coin not BTC/ETH/SOL; P4 open hour 00–07 UTC.
  A predictor is useful if flagged copies have mean ≤ 0 in both halves AND (flagged − rest) Welch t ≤ −2.5 with the difference
  negative in both halves.
- Remedy for P1-flagged decisions (2 variants): minimum copy hold 60 / 240 min (exit at max(whale close + 2 min, entry + H)).
  Pass: paired gain vs base > 0 with t ≥ 2.5 and > 0 in both halves, variant mean > 0 in both halves. 6 variants in total.
Data: lab-data-whales → whale_rules_convert.py; whale_rules_study.py (branch research/whale-rules-0411) next to this file as study.py.
Usage: whale_fast.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, statistics as st, sys
from collections import defaultdict
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0

MIN, H, DAY = 60_000, 3_600_000, 86_400_000
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)
MAJ = {"BTC", "ETH", "SOL"}


# fwd2 / groups / w_entry_at / stat: copied unchanged from whale_join.py
def fwd2(S, t0, t1, side, stop=16.0):
    a, b = S.price(t0), S.price(t1)
    if not a or not b: return None
    s = 1 if side == "LONG" else -1
    i0, i1 = S.idx(t0 + S0.H1), S.idx(t1 + S0.H1)
    lvl = a * (1 - s * stop / 100)
    for k in range(i0 + 1, i1):
        if (s > 0 and S.l[k] <= lvl) or (s < 0 and S.h[k] >= lvl):
            b = S.o[k] if ((s > 0 and S.o[k] < lvl) or (s < 0 and S.o[k] > lvl)) else lvl
            t1 = int(S.t[k]); break
    return a, s * (b / a - 1) * 100 - S0.COST - S.funding_paid(side, t0, t1)


def groups(trips):                      # same merge as S0.episodes, but keeps the slices (entry known at time t)
    by = defaultdict(list)
    for t in trips:
        by[(t["coin"], t["side"])].append(t)
    out = []
    for ts in by.values():
        ts.sort(key=lambda x: x["open_t"]); cur = None
        for t in ts:
            if cur and t["open_t"] - cur[0]["open_t"] <= 4 * H: cur.append(t)
            else:
                if cur: out.append(cur)
                cur = [t]
        if cur: out.append(cur)
    res = []
    for g in out:
        w = [max(x["notional"], 1e-9) for x in g]
        res.append({"coin": g[0]["coin"], "side": g[0]["side"], "open_t": min(x["open_t"] for x in g), "close_t": max(x["close_t"] for x in g),
                    "pnl": sum(x["pnl"] for x in g), "open": any(x.get("open") for x in g), "g": g})
    return sorted(res, key=lambda x: x["open_t"])


def w_entry_at(g, t):
    xs = [(x["entry"], max(x["notional"], 1e-9)) for x in g if x["open_t"] <= t]
    return sum(a * b for a, b in xs) / sum(b for _, b in xs) if xs else None


def stat(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m, sd = st.mean(xs), st.pstdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}



whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

rows = []
for src, name, trips in whales:
    done = []
    for e in groups(trips):
        t0 = e["open_t"]
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        rec30 = [d for d in done if d[1] < t0 and d[1] >= t0 - 30 * DAY and not d[3]]
        share = (sum(1 for d in rec30 if d[1] - d[0] < 15 * MIN) / len(rec30)) if len(rec30) >= 10 else None
        done.append((t0, e["close_t"], e["pnl"], e["open"]))
        if not bar: continue
        Sx = W.get(e["coin"])
        if Sx is None: continue
        ct = e["close_t"]
        if e["open"]:
            ct = min(ct, int(Sx.t[-1]) + S0.H1 - 3 * MIN)
        hold = ct - t0
        if hold <= 2 * MIN: continue          # the copy cannot exist (as in whale_join.py)
        b = fwd2(Sx, t0 + 2 * MIN, ct + 2 * MIN, e["side"])
        if b is None or not math.isfinite(b[1]): continue
        g0 = min(e["g"], key=lambda x: x["open_t"])
        lev = g0.get("lev")
        r = {"src": src, "w": name, "t": t0, "fast": hold < 15 * MIN, "base": b[1], "share": share,
             "P1": share is not None and share > 0.30, "P2": lev is not None and lev >= 20, "P3": e["coin"] not in MAJ,
             "P4": datetime.fromtimestamp(t0 / 1000, timezone.utc).hour < 8, "lev_known": lev is not None}
        for Hm in (60, 240):
            ex = max(ct + 2 * MIN, t0 + 2 * MIN + Hm * MIN)
            if ex > int(Sx.t[-1]) + S0.H1 - MIN: r[f"H{Hm}"] = None; continue
            v = fwd2(Sx, t0 + 2 * MIN, ex, e["side"])
            r[f"H{Hm}"] = v[1] if v is not None and math.isfinite(v[1]) else None
        rows.append(r)

rows.sort(key=lambda r: r["t"])
mid = rows[len(rows) // 2]["t"]


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = st.pvariance(a) / len(a), st.pvariance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb > 0 else None


O = {"made": now, "n": len(rows), "whales": len({r["w"] for r in rows}), "fast_share": round(sum(r["fast"] for r in rows) / len(rows), 3),
     "base": {p: stat([r["base"] for r in rs]) for p, rs in (("all", rows), ("h1", [r for r in rows if r["t"] < mid]), ("h2", [r for r in rows if r["t"] >= mid]))},
     "fast_vs_rest": {"fast": stat([r["base"] for r in rows if r["fast"]]), "rest": stat([r["base"] for r in rows if not r["fast"]])},
     "pred": {}, "remedy": {}}
for P in ("P1", "P2", "P3", "P4"):
    pool = [r for r in rows if (P != "P1" or r["share"] is not None) and (P != "P2" or r["lev_known"])]
    q = {"pool": len(pool)}
    for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid)):
        rs = [r for r in pool if f(r)]
        a, b = [r["base"] for r in rs if r[P]], [r["base"] for r in rs if not r[P]]
        fa = [r for r in rs if r[P]]; fb = [r for r in rs if not r[P]]
        q[part] = {"flag": stat(a), "rest": stat(b), "t_diff": welch(a, b),
                   "fast_rate_flag": round(sum(r["fast"] for r in fa) / len(fa), 3) if fa else None,
                   "fast_rate_rest": round(sum(r["fast"] for r in fb) / len(fb), 3) if fb else None}
    q["useful"] = bool(q["h1"]["flag"].get("mean", 1) <= 0 and q["h2"]["flag"].get("mean", 1) <= 0 and (q["all"]["t_diff"] or 0) <= -2.5
                       and q["h1"]["flag"].get("mean", 0) < q["h1"]["rest"].get("mean", 0) and q["h2"]["flag"].get("mean", 0) < q["h2"]["rest"].get("mean", 0))
    O["pred"][P] = q
for Hm in (60, 240):
    q = {}
    for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid)):
        rs = [r for r in rows if f(r) and r["P1"] and r.get(f"H{Hm}") is not None]
        q[part] = {"variant": stat([r[f"H{Hm}"] for r in rs]), "base": stat([r["base"] for r in rs]), "gain": stat([r[f"H{Hm}"] - r["base"] for r in rs])}
    g = [q[p]["gain"] for p in ("all", "h1", "h2")]
    q["pass"] = bool((g[0].get("t") or 0) >= 2.5 and all(x.get("mean", 0) > 0 for x in g)
                     and q["h1"]["variant"].get("mean", 0) > 0 and q["h2"]["variant"].get("mean", 0) > 0)
    O["remedy"][f"H{Hm}"] = q
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: O[k] for k in ("n", "whales", "fast_share", "base", "fast_vs_rest")}, ensure_ascii=False))
for P, q in O["pred"].items():
    print(P, "pool", q["pool"], "useful", q["useful"])
    for p in ("all", "h1", "h2"):
        x = q[p]; print("  ", p, "flag", x["flag"], "rest", x["rest"], "t", x["t_diff"], "fastrate", x["fast_rate_flag"], x["fast_rate_rest"])
for k, q in O["remedy"].items():
    print(k, "pass", q["pass"])
    for p in ("all", "h1", "h2"): print("  ", p, q[p])
