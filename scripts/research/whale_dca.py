"""Backlog 99 (08.11.2026, research only): copy the whale's ADDS vs copy once. Pre-registered before looking:
- Decision = whale's entries into one coin+side within 4 h (as whale_join.py); population = whales passing the live 60-day bar at
  the open (>= 20 decisions, win >= 70 %, PF >= 2, P&L > 0). Slices: OKX = sub-positions (own open time / entry); HL = opening fills
  of the trip, merged within 2 min, plus fills of later trips in the same 4 h group.
- A (now): one entry of 1 unit (= full 3 % risk budget) at first slice + 2 min.
- B(f): first entry f units, each later slice i adds f * notional_i / notional_0 units at slice time + 2 min, total capped at 1 unit
  (risk limit 3 %). f in {0.5, 0.33} -> 2 variants, pass bar t >= 2.2 on paired B - A, both halves, both OKX and HL where n >= 30.
- Common exit 2 min after the whale's last close (open ones marked at last price), stop -16 % from OUR average entry (hourly bars),
  costs 0.11 % per unit round trip + 0.02 % extra per add order + funding on each unit from its own entry.
- Metric: P&L per decision in % of the 1-unit budget (what the account earns at equal risk budget). Also P&L per unit deployed.
Usage: whale_dca.py <conv>/whales.json.gz OUT.json   (study.py = whale_rules_study.py from research/whale-rules-0411)
"""
import json, math, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0
import numpy as np

MIN, H, DAY = 60_000, 3_600_000, 86_400_000
FS = (0.5, 0.33)
ADD_COST = 0.02
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)


def groups(trips):
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
    return [{"coin": g[0]["coin"], "side": g[0]["side"], "open_t": min(x["open_t"] for x in g), "close_t": max(x["close_t"] for x in g),
             "pnl": sum(x["pnl"] for x in g), "open": any(x.get("open") for x in g), "g": g} for g in out]


def slices_okx(e):
    return sorted((x["open_t"], max(x["notional"], 1e-9)) for x in e["g"])


def slices_hl(e, fills):
    word = "Long" if e["side"] == "LONG" else "Short"
    out = []
    for tr in e["g"]:
        fs = [f for f in fills.get(tr["coin"], []) if tr["open_t"] - MIN <= f[0] < tr["close_t"] and f[5] == "Open " + word]
        if not fs: out.append((tr["open_t"], max(tr["notional"], 1e-9))); continue
        for f in fs:
            v = float(f[3]) * float(f[4])
            if out and f[0] - out[-1][0] <= 2 * MIN: out[-1] = (out[-1][0], out[-1][1] + v)
            else: out.append((f[0], v))
    out.sort()
    m = []
    for t, v in out:
        if m and t - m[-1][0] <= 2 * MIN: m[-1] = (m[-1][0], m[-1][1] + v)
        else: m.append((t, v))
    return m


def sim(S, side, legs, t_exit):
    """legs = [(t_entry, units, is_add)]; returns (pnl in % of 1 unit budget, units deployed) or None."""
    s = 1 if side == "LONG" else -1
    L = []
    for t, u, add in legs:
        px = S.price(t)
        if not px or not math.isfinite(px) or t >= t_exit: continue
        L.append([t, u, px, add])
    if not L: return None
    i0, i1 = S.idx(L[0][0] + H), S.idx(t_exit + H)
    b, tx = S.price(t_exit), t_exit
    if not b: return None
    for k in range(i0 + 1, i1):
        bt = int(S.t[k])
        act = [x for x in L if x[0] < bt]
        U = sum(x[1] for x in act); avg = sum(x[1] * x[2] for x in act) / U
        lvl = avg * (1 - s * 0.16)
        if (s > 0 and S.l[k] <= lvl) or (s < 0 and S.h[k] >= lvl):
            b = S.o[k] if ((s > 0 and S.o[k] < lvl) or (s < 0 and S.o[k] > lvl)) else lvl
            tx = bt; break
    pnl, dep = 0.0, 0.0
    for t, u, px, add in L:
        if t >= tx: continue
        pnl += u * (s * (b / px - 1) * 100 - S0.COST - (ADD_COST if add else 0) - S.funding_paid(side, t, tx))
        dep += u
    return pnl, dep


def stat(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m, sd = st.mean(xs), st.pstdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None}


whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    fills = defaultdict(list)
    for f in w["fills"]: fills[f[1]].append(f)
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now), fills))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr, None))

rows = []
for src, name, trips, fills in whales:
    done = []
    for e in sorted(groups(trips), key=lambda x: x["open_t"]):
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
        if e["open"]: e["close_t"] = min(e["close_t"], int(Sx.t[-1]) + S0.H1 - 3 * MIN)
        sl = slices_okx(e) if src == "okx" else slices_hl(e, fills)
        tex = e["close_t"] + 2 * MIN
        a = sim(Sx, e["side"], [(sl[0][0] + 2 * MIN, 1.0, False)], tex)
        if a is None or not math.isfinite(a[0]): continue
        r = {"src": src, "w": name, "t": t0, "n_sl": len(sl), "A": a[0]}
        for f in FS:
            legs, tot = [(sl[0][0] + 2 * MIN, f, False)], f
            for ts, v in sl[1:]:
                u = min(f * v / sl[0][1], 1.0 - tot)
                if u <= 1e-6: break
                legs.append((ts + 2 * MIN, u, True)); tot += u
            b = sim(Sx, e["side"], legs, tex)
            r[f"B{f}"], r[f"dep{f}"] = (b if b else (None, None))
        if any(r[f"B{f}"] is None or not math.isfinite(r[f"B{f}"]) for f in FS): continue
        rows.append(r)

rows.sort(key=lambda r: r["t"])
O = {"made": now, "n": len(rows), "multi": sum(r["n_sl"] > 1 for r in rows), "whales": len({r["w"] for r in rows})}
print(O)
for key, flt in (("okx+hl", lambda r: True), ("okx", lambda r: r["src"] == "okx"), ("hl", lambda r: r["src"] == "hl"),
                 ("multi", lambda r: r["n_sl"] > 1)):
    rs0 = [r for r in rows if flt(r)]
    if len(rs0) < 30: continue
    mid = rs0[len(rs0) // 2]["t"]
    blk = {}
    for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
        rs = [r for r in rs0 if f(r)]
        q = {"A": stat([r["A"] for r in rs])}
        for fv in FS:
            q[f"B{fv}"] = stat([r[f"B{fv}"] for r in rs])
            q[f"Δ{fv}"] = stat([r[f"B{fv}"] - r["A"] for r in rs])
            q[f"per_unit{fv}"] = round(sum(r[f"B{fv}"] for r in rs) / sum(r[f"dep{fv}"] for r in rs), 3)
            q[f"dep{fv}"] = round(st.mean(r[f"dep{fv}"] for r in rs), 3)
        blk[p] = q
        print(key, p, "A", q["A"], *[f"B{fv} {q[f'B{fv}']} Δ {q[f'Δ{fv}']} per_unit {q[f'per_unit{fv}']} dep {q[f'dep{fv}']}" for fv in FS], sep="\n   ")
    O[key] = blk
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
