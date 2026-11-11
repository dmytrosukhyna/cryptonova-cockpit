"""Backlog 139, OKX/HL half: leave column A when the whale stops passing the bar (11.11.2026, research only).

Design and pass bar: see rot_sim.py (pre-registered); trades built exactly as whale_daystop.py (backlog 90). Data: lab-data-whales → whale_rules_convert.py; whale_rules_study.py
(branch research/whale-rules-0411) next to this file as study.py. Usage: whale_rotbar.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0
import daystop_sim as D
import rot_sim as R

MIN, DAY = 60_000, 86_400_000
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
    return a, t1, s * (b / a - 1) * 100 - S0.COST - S.funding_paid(side, t0, t1)


whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

trades, decs = [], {}
for src, name, trips in whales:
    eps = S0.episodes(sorted(trips, key=lambda x: x["open_t"]))
    for e in eps:
        t0 = e["open_t"]
        if not e.get("open"): decs.setdefault(name, []).append((t0, e["close_t"], e["pnl"]))
        if e.get("open") or e["close_t"] - t0 <= 2 * MIN: continue
        Sx = W.get(e["coin"])
        if Sx is None: continue
        v = fwd2(Sx, t0 + 2 * MIN, e["close_t"] + 2 * MIN, e["side"])
        if v is None or not math.isfinite(v[2]): continue
        trades.append({"src": src, "w": name, "coin": e["coin"], "side": e["side"], "t_in": t0 + 2 * MIN, "t_out": v[1], "a": v[0], "ret": float(v[2])})


def mark(coin, t):
    S = W.get(coin)
    return S.price(t) if S is not None else None


out = {"made": now, "whales": len(decs)}
out["rot"] = R.report(decs, trades, mark, S0.COST, "okx+hl rotbar")
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
