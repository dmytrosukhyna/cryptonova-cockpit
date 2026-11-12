"""Backlog 146, 12.11.2026: the copy test at SELECTION with the live delay (6 min) instead of 2 min. Research only — reads ws_trips
(src='bn') from the whale study's database (Actions cache), writes nothing to it. Same machinery as bn_whale_delay.py (Binance 1-minute
klines + funding, decision = one trader's entries in the same coin + side within 4 h, copy entry/exit at the open of the first 1m bar
≥ whale time + d, stop −16 %, 0.11 % a round trip + funding).

Pre-registered before any result was seen (12.11 09:20 Athens):
- Traders with ≥ 20 decisions. Time split at the median decision. Select in one half, evaluate in the other, BOTH directions
  (h1 → h2 forward, h2 → h1 backward).
- Bar in the selection half (our live bar, as far as closed trips allow): ≥ 20 decisions, whale win ≥ 70 %, whale PF ≥ 2, whale sum > 0,
  AND copy test at delay D: the sum of copy results > 0. SEL2 = bar with D = 2 min (today), SEL6 = with D = 6 min (live lag_s_med 341 s).
  SEL4 reported for information only.
- Evaluation half: copies at d = 6 min (live-like). Metric: mean copy result per decision; «dropped» = SEL2 minus SEL6.
- Holding buckets of the evaluated decisions: < 4 h / ≥ 4 h.
- Pass (= propose to the owner a 6-min copy test in the selection bar): in BOTH directions the dropped traders' evaluation mean < 0 and
  SEL6 mean ≥ SEL2 mean, pooled dropped t ≤ −2, and SEL6 keeps ≥ 50 % of SEL2. One primary comparison (2 vs 6); 4 min is not used for
  the verdict. Known bias: ws_trips holds closed trips only (backlog 104) — open losers missing, makes both bars look better.
Usage: bn_whale_selag.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402

M1, H, COST = D.M1, D.H, D.COST
DELAYS = [2, 4, 6]


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.stdev(xs)
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "pf": round(pos / neg, 2) if neg else None, "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def passes(rs, d):
    if len(rs) < 20:
        return False
    w = [r["wret"] - COST for r in rs]
    pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
    if sum(x > 0 for x in w) / len(w) < 0.70 or sum(w) <= 0 or (neg and pos / neg < 2):
        return False
    return sum(r[f"d{d}"] for r in rs) > 0


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[7] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[4] for x in g)
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[6] / x[5] - 1) * 100 * wi for x, wi in zip(g, w) if x[5] and x[6]) / sum(w)
    print(f"bn rows {len(rows)}, decisions (traders ≥ 20): {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = D.load_sym(sym, [(x["open_t"], x["close_t"]) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "hold_h": (e["close_t"] - e["open_t"]) / H}
        ok = True
        for d in DELAYS:
            v = D.copy(S, e["side"], e["open_t"] + d * M1, e["close_t"] + d * M1)
            if v is None or not math.isfinite(v):
                ok = False; break
            r[f"d{d}"] = v
        if ok:
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    halves = {"h1": [r for r in res if r["t"] < mid], "h2": [r for r in res if r["t"] >= mid]}
    O = {"n": len(res), "traders": len({r["trader"] for r in res}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000)),
         "mid": time.strftime("%Y-%m-%d", time.gmtime(mid / 1000))}
    pooled_drop = []
    ok_all = True
    for name, (a, b) in {"fwd h1→h2": ("h1", "h2"), "back h2→h1": ("h2", "h1")}.items():
        sel_rows = defaultdict(list)
        for r in halves[a]:
            sel_rows[r["trader"]].append(r)
        ev = defaultdict(list)
        for r in halves[b]:
            ev[r["trader"]].append(r)
        sel = {d: {tr for tr, rs in sel_rows.items() if passes(rs, d)} for d in DELAYS}
        drop = sel[2] - sel[6]
        P = {"sel": {f"SEL{d}": len(sel[d]) for d in DELAYS}, "dropped": len(drop), "added": len(sel[6] - sel[2])}
        for lab, group in {"SEL2": sel[2], "SEL4": sel[4], "SEL6": sel[6], "dropped": drop}.items():
            rs = [r for tr in group for r in ev.get(tr, [])]
            P[lab] = {"d6": tstat([r["d6"] for r in rs]), "d2": tstat([r["d2"] for r in rs]),
                      "whale": tstat([r["wret"] - COST for r in rs]),
                      "<4h": tstat([r["d6"] for r in rs if r["hold_h"] < 4]), ">=4h": tstat([r["d6"] for r in rs if r["hold_h"] >= 4]),
                      "traders_eval": len({r["trader"] for r in rs})}
            if lab == "dropped":
                pooled_drop += [r["d6"] for r in rs]
        dm, s2, s6 = P["dropped"]["d6"].get("mean"), P["SEL2"]["d6"].get("mean"), P["SEL6"]["d6"].get("mean")
        ok = (dm is not None and dm < 0 and s2 is not None and s6 is not None and s6 >= s2 and len(sel[6]) >= 0.5 * max(1, len(sel[2])))
        P["ok"] = ok
        ok_all = ok_all and ok
        O[name] = P
    O["pooled_dropped_d6"] = tstat(pooled_drop)
    pt = O["pooled_dropped_d6"].get("t")
    O["verdict"] = "прошло" if ok_all and pt is not None and pt <= -2 else "не прошло"
    O["hold_all"] = {"<4h": {f"d{d}": tstat([r[f"d{d}"] for r in res if r["hold_h"] < 4]) for d in DELAYS},
                     ">=4h": {f"d{d}": tstat([r[f"d{d}"] for r in res if r["hold_h"] >= 4]) for d in DELAYS}}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=selag n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']}, mid {O['mid']}")
    for name in ("fwd h1→h2", "back h2→h1"):
        P = O[name]
        txt = " | ".join(f"{k}: d6 {P[k]['d6'].get('mean')}/t{P[k]['d6'].get('t')}/n{P[k]['d6'].get('n')}/tr{P[k]['traders_eval']} "
                         f"<4h {P[k]['<4h'].get('mean')}/{P[k]['<4h'].get('n')} ≥4h {P[k]['>=4h'].get('mean')}/{P[k]['>=4h'].get('n')}"
                         for k in ("SEL2", "SEL4", "SEL6", "dropped"))
        print(f"::notice title=selag {name}::sel {P['sel']} dropped {P['dropped']} added {P['added']} ok {P['ok']} || {txt}")
    h = O["hold_all"]
    print(f"::notice title=selag hold::<4h " + " ".join(f"{k} {v.get('mean')}/{v.get('n')}" for k, v in h["<4h"].items())
          + " || ≥4h " + " ".join(f"{k} {v.get('mean')}/{v.get('n')}" for k, v in h[">=4h"].items()))
    print(f"::notice title=selag verdict::{O['verdict']} (pooled dropped d6 {O['pooled_dropped_d6']})")


if __name__ == "__main__":
    main()
