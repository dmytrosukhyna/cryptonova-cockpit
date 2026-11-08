"""Backlog 112, OKX/HL half (08.11.2026, research only): are copies worse when ANOTHER whale holds the same coin the other way?
Pre-registered before looking at any result:
- Population (as whale_join.py / whale_dca.py): decisions (entries into one coin+side within 4 h) of whales that pass the live 60-day
  bar at the open (>= 20 decisions, win >= 70 %, PF >= 2, P&L > 0). Copy: entry 2 min after the whale's open, exit 2 min after his
  last close (still-open ones marked at the last price), stop -16 %, costs 0.11 % a round trip + funding.
- At the decision's open t0, look at the OTHER whales' decisions open at t0 (open_t < t0 < close_t), same coin:
  D_bar «спор со списком»: another whale whose decision itself passed the live bar at its open (= would be on our list / desk)
          holds the OPPOSITE side;
  D_any «спор с любым»: any other whale in the data holds the opposite side.
  Also reported (not tested): «согласие» = another bar-passing whale holds the SAME side.
- 2 looks (D_bar, D_any). Pass = disputed copies have mean < 0 in BOTH halves of time, disputed − rest < 0 in both halves, and
  Welch t <= −2.5 on all decisions. If passed — only a candidate («не открывать копию, пока киты списка спорят»), demo first;
  money rules only with Dmytro's yes.
Data: lab-data-whales → whale_rules_convert.py; whale_rules_study.py (branch research/whale-rules-0411) next to this file as study.py.
Usage: whale_dispute.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0

MIN, H, DAY = 60_000, 3_600_000, 86_400_000
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
    return sorted([{"coin": g[0]["coin"], "side": g[0]["side"], "open_t": min(x["open_t"] for x in g),
                    "close_t": max(x["close_t"] for x in g), "pnl": sum(x["pnl"] for x in g), "open": any(x.get("open") for x in g)}
                   for g in out], key=lambda x: x["open_t"])


def stat(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m, sd = st.mean(xs), st.pstdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return round((st.mean(a) - st.mean(b)) / se, 2) if se else None


whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

# every decision of every whale, with its own live-bar flag at its open
decs = []
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
        decs.append({**e, "src": src, "w": f"{src}:{name}", "bar": bar})
by_coin = defaultdict(list)
for d in decs:
    by_coin[str(d["coin"]).upper()].append(d)

rows = []
for e in decs:
    if not e["bar"] or e["close_t"] - e["open_t"] <= 2 * MIN: continue
    Sx = W.get(e["coin"])
    if Sx is None: continue
    t0 = e["open_t"]
    peers = [d for d in by_coin[str(e["coin"]).upper()] if d["w"] != e["w"] and d["open_t"] < t0 < d["close_t"]]
    cl = e["close_t"]
    if e["open"]: cl = min(cl, int(Sx.t[-1]) + S0.H1 - 3 * MIN)
    b = fwd2(Sx, t0 + 2 * MIN, cl + 2 * MIN, e["side"])
    if b is None or not math.isfinite(b[1]): continue
    rows.append({"src": e["src"], "w": e["w"], "t": t0, "side": e["side"], "v": b[1],
                 "D_bar": any(p["bar"] and p["side"] != e["side"] for p in peers),
                 "D_any": any(p["side"] != e["side"] for p in peers),
                 "agree": any(p["bar"] and p["side"] == e["side"] for p in peers)})

rows.sort(key=lambda r: r["t"])
O = {"made": now, "n": len(rows), "whales": len({r["w"] for r in rows}),
     "share": {k: round(sum(r[k] for r in rows) / max(len(rows), 1), 3) for k in ("D_bar", "D_any", "agree")}}
print(O, flush=True)
verdict = {}
for key, flt in (("okx+hl", lambda r: True), ("okx", lambda r: r["src"] == "okx"), ("hl", lambda r: r["src"] == "hl")):
    rs0 = [r for r in rows if flt(r)]
    if len(rs0) < 30: continue
    mid = rs0[len(rs0) // 2]["t"]
    blk = {}
    for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
        rs = [r for r in rs0 if f(r)]
        q = {"base": stat([r["v"] for r in rs])}
        for k in ("D_bar", "D_any", "agree"):
            a, b = [r["v"] for r in rs if r[k]], [r["v"] for r in rs if not r[k]]
            q[k] = stat(a); q[f"no_{k}"] = stat(b)
            q[f"diff_{k}"] = {"mean": round(st.mean(a) - st.mean(b), 3) if len(a) > 1 and b else None, "t": welch(a, b)}
        blk[p] = q
    O[key] = blk
    if key == "okx+hl":
        for k in ("D_bar", "D_any"):
            h1, h2, a = blk["h1"], blk["h2"], blk["all"]
            ok = (all(x[k].get("mean") is not None and x[k]["mean"] < 0 for x in (h1, h2))
                  and all(x[f"diff_{k}"]["mean"] is not None and x[f"diff_{k}"]["mean"] < 0 for x in (h1, h2))
                  and a[f"diff_{k}"]["t"] is not None and a[f"diff_{k}"]["t"] <= -2.5)
            verdict[k] = ok
O["verdict"] = verdict
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
for key in ("okx+hl", "okx", "hl"):
    if key not in O: continue
    for p in ("all", "h1", "h2"):
        q = O[key][p]
        print(key, p, "base", q["base"].get("mean"), q["base"].get("n"), " | ".join(
            f"{k}: {q[k].get('mean')}/n{q[k].get('n')}/pf{q[k].get('pf')} vs {q['no_' + k].get('mean')} Δ{q['diff_' + k]['mean']} t{q['diff_' + k]['t']}"
            for k in ("D_bar", "D_any", "agree")))
print("verdict", verdict)
