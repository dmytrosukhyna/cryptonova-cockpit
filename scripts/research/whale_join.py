"""Backlog 103, OKX/HL half (07.11.2026, research only): late joins vs entry with the whale. Pre-registered design: see
bn_whale_join.py (same lags 2 / 60 / 240 / 720 / 1440 min, live window −3 % … +1 % of the whale's entry known at the lag, stop −16 %,
exit 2 min after the whale's close, 0.11 % + funding). Population as in whale_daystop.py: decisions of whales that pass the live
60-day bar at the open (≥ 20 decisions, win ≥ 70 %, PF ≥ 2, P&L > 0). Data: lab-data-whales → whale_rules_convert.py;
whale_rules_study.py (branch research/whale-rules-0411) next to this file as study.py. Usage: whale_join.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0

MIN, H, DAY = 60_000, 3_600_000, 86_400_000
LAGS = [2, 60, 240, 720, 1440]
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)


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
        done.append((t0, e["close_t"], e["pnl"]))
        if not bar or e["close_t"] - t0 <= 2 * MIN: continue
        Sx = W.get(e["coin"])
        if Sx is None: continue
        if e["open"]:                    # still open at the data's end: marked at the last price (else long-held losers drop out)
            e["close_t"] = min(e["close_t"], int(Sx.t[-1]) + S0.H1 - 3 * MIN)
        b = fwd2(Sx, t0 + 2 * MIN, e["close_t"] + 2 * MIN, e["side"])
        if b is None or not math.isfinite(b[1]): continue
        r = {"src": src, "w": name, "t": t0, "open": e["open"], "L2": b[1], "hold_h": (e["close_t"] - t0) / H}
        s = 1 if e["side"] == "LONG" else -1
        for L in LAGS[1:]:
            ti = t0 + L * MIN
            r[f"L{L}"] = None
            if e["close_t"] <= ti + MIN: continue
            r[f"open{L}"] = True
            we, px = w_entry_at(e["g"], ti), Sx.price(ti)
            if not we or not px: continue
            gap = s * (px / we - 1) * 100
            if gap < -3 or gap > 1: continue
            v = fwd2(Sx, ti, e["close_t"] + 2 * MIN, e["side"])
            if v is not None and math.isfinite(v[1]): r[f"L{L}"] = v[1]
        rows.append(r)

rows.sort(key=lambda r: r["t"])
O = {"made": now, "n": len(rows), "n_open_marked": sum(r["open"] for r in rows), "whales": len({r["w"] for r in rows})}
print("rows", O)
for srcs in (("okx", "hl"), ("okx",), ("hl",), ("closed",)):
    rs0 = [r for r in rows if (r["src"] in srcs) or (srcs == ("closed",) and not r["open"])]
    if len(rs0) < 30: continue
    mid = rs0[len(rs0) // 2]["t"]
    blk = {}
    for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
        rs = [r for r in rs0 if f(r)]
        q = {"L2": stat([r["L2"] for r in rs])}
        for L in LAGS[1:]:
            js = [r for r in rs if r.get(f"L{L}") is not None]
            q[f"open{L}"] = sum(1 for r in rs if r.get(f"open{L}"))
            q[f"L{L}"] = stat([r[f"L{L}"] for r in js])
            q[f"same_L2_{L}"] = stat([r["L2"] for r in js])
            q[f"Δ{L}"] = stat([r[f"L{L}"] - r["L2"] for r in js])
        blk[p] = q
    O["+".join(srcs)] = blk
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for k in ("okx+hl", "okx", "hl", "closed"):
    if k not in O: continue
    for p in ("all", "h1", "h2"):
        q = O[k][p]
        print(k, p, " ".join(f"L{L}:{q[f'L{L}'].get('mean')}/n{q[f'L{L}'].get('n')}/pf{q[f'L{L}'].get('pf')}" for L in LAGS))
        print("   ", " ".join(f"Δ{L}:{q[f'Δ{L}'].get('mean')}/t{q[f'Δ{L}'].get('t')} same2:{q[f'same_L2_{L}'].get('mean')}" for L in LAGS[1:]))
