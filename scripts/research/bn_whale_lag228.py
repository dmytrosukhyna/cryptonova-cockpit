"""Backlog 228, 21.11.2026: the selection copy test with the MEASURED live delay (backlog 214: Binance ≈ 2–5 min, OKX ≈ 5–6 min)
instead of 2 min. Research only — reads ws_trips (src bn + okx) from the whale study's database (Actions cache), writes nothing to it.
Same machinery as bn_whale_selag.py (Binance 1-minute klines + funding, decision = one trader's entries in the same coin + side within
4 h, copy entry/exit at the open of the first 1m bar ≥ whale time + d, stop −16 %, 0.11 % a round trip + funding).

Pre-registered before any result was seen (21.11 03:20 Athens):
- Window: decisions opened in the last 60 days before the newest closed trip in ws_trips (the live bar's window). Closed trips only
  (backlog 104 bias: open losers missing — both delays look better, the comparison between them is what matters).
- Traders with ≥ 20 decisions in the window, sources bn and okx.
- Bar (closed-only stand-in for the live bar): ≥ 20 decisions, whale win ≥ 70 %, whale PF ≥ 2, whale sum > 0 (after 0.11 %),
  AND sum of copy results at delay D > 0.
- Primary comparison: D = 2 min (today's bar) vs D = live (bn 5 min, okx 6 min). 10 and 15 min for information only.
- Report: traders passing at each D, flips 2 → live (pass → fail and fail → pass), mean copy per decision at 2 vs live (all traders,
  passing traders), and the same for the 19 traders of the lock's «passing» list and the 4 followed Binance traders.
- Verdict (fixed in advance): if ≥ 1 trader of the current lock (following or passing list) flips pass → fail at the live delay →
  «вопрос владельцу» (rule of selection); otherwise «менять нечего».
Usage: bn_whale_lag228.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402

M1, H, DAY, COST = D.M1, D.H, D.DAY, D.COST
LIVE = {"bn": 5, "okx": 6}
DELAYS = [2, 5, 6, 10, 15]
WINDOW = 60 * DAY


def load_lock():
    try:
        w = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "whales_top.json")))
    except Exception:  # noqa: BLE001
        return set(), set()
    fol = {f.get("addr") for f in w.get("following") or [] if isinstance(f, dict)}
    pas = set((w.get("follow_lock") or {}).get("passing") or {})
    return fol, pas


def tstat(xs):
    if len(xs) < 2:
        return {"n": len(xs), "mean": round(xs[0], 3) if xs else None}
    m, sd = st.mean(xs), st.stdev(xs)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def bar(rs, d):
    if len(rs) < 20:
        return False
    w = [r["wret"] - COST for r in rs]
    pos, neg = sum(x for x in w if x > 0), -sum(x for x in w if x < 0)
    if sum(x > 0 for x in w) / len(w) < 0.70 or sum(w) <= 0 or (neg and pos / neg < 2):
        return False
    return sum(r[f"d{d}"] for r in rs) > 0


def main():
    db, out = sys.argv[1], sys.argv[2]
    fol, pas = load_lock()
    lock = fol | pas
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips "
                     "WHERE src IN ('bn','okx') AND close_t > 0 AND coin IS NOT NULL AND coin != '' ORDER BY src, trader, open_t").fetchall()
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    t_end = max(r[5] for r in rows)
    since = t_end - WINDOW
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[(r[0], tid)].append(r)
    eps = []
    for (src, tid), lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[2], r[3]); e = cur.get(k)
            if e and r[4] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"src": src, "trader": tid, "coin": r[2], "side": r[3], "open_t": r[4], "g": [r]}; cur[k] = e; mine.append(e)
        mine = [e for e in mine if e["open_t"] >= since]
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[8] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[5] for x in g)
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[7] / x[6] - 1) * 100 * wi for x, wi in zip(g, w) if x[6] and x[7]) / sum(w)
    print(f"rows {len(rows)}, decisions (traders ≥ 20 in 60 d): {len(eps)}, window "
          f"{time.strftime('%Y-%m-%d', time.gmtime(since / 1000))} → {time.strftime('%Y-%m-%d', time.gmtime(t_end / 1000))}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"].upper() + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = D.load_sym(sym, [(x["open_t"], x["close_t"]) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res, nodata = [], 0
    for e in eps:
        S = data.get(e["coin"].upper() + "USDT")
        if not S:
            nodata += 1; continue
        r = {"src": e["src"], "trader": e["trader"], "t": e["open_t"], "wret": e["wret"], "hold_h": (e["close_t"] - e["open_t"]) / H}
        ok = True
        for d in DELAYS:
            v = D.copy(S, e["side"], e["open_t"] + d * M1, e["close_t"] + d * M1)
            if v is None or not math.isfinite(v):
                ok = False; break
            r[f"d{d}"] = v
        if ok:
            res.append(r)
        else:
            nodata += 1
    per = defaultdict(list)
    for r in res:
        per[r["trader"]].append(r)
    O = {"window": [time.strftime("%Y-%m-%d", time.gmtime(since / 1000)), time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))],
         "decisions": len(res), "no_data": nodata, "traders": len(per), "by_src": {}, "lock_traders_in_data": 0}
    flips_lock = []
    for src in ("bn", "okx"):
        L = LIVE[src]
        trs = {tr: rs for tr, rs in per.items() if rs[0]["src"] == src and len(rs) >= 20}
        p2 = {tr for tr, rs in trs.items() if bar(rs, 2)}
        pl = {tr for tr, rs in trs.items() if bar(rs, L)}
        info = {f"pass_d{d}": sum(1 for rs in trs.values() if bar(rs, d)) for d in DELAYS}
        allr = [r for rs in trs.values() for r in rs]
        pr = [r for tr in p2 for r in trs[tr]]
        lk = {tr: rs for tr, rs in trs.items() if tr in lock}
        O["lock_traders_in_data"] += len(lk)
        lock_rows = []
        for tr, rs in sorted(lk.items()):
            row = {"trader": tr, "followed": tr in fol, "n": len(rs), "pass2": tr in p2, f"pass{L}": tr in pl,
                   "copy_sum_d2": round(sum(r["d2"] for r in rs), 2), f"copy_sum_d{L}": round(sum(r[f"d{L}"] for r in rs), 2),
                   "copy_mean_d2": round(st.mean(r["d2"] for r in rs), 3), f"copy_mean_d{L}": round(st.mean(r[f"d{L}"] for r in rs), 3),
                   "copy_mean_d15": round(st.mean(r["d15"] for r in rs), 3)}
            lock_rows.append(row)
            if tr in p2 and tr not in pl:
                flips_lock.append(tr)
        O["by_src"][src] = {"live_delay_min": L, "traders": len(trs), **info, "flip_out": sorted(p2 - pl), "flip_in": sorted(pl - p2),
                            "all": {f"d{d}": tstat([r[f"d{d}"] for r in allr]) for d in DELAYS},
                            "passing_d2": {f"d{d}": tstat([r[f"d{d}"] for r in pr]) for d in DELAYS},
                            "diff_live_minus_2_all": tstat([r[f"d{L}"] - r["d2"] for r in allr]),
                            "diff_live_minus_2_passing": tstat([r[f"d{L}"] - r["d2"] for r in pr]),
                            "lock": lock_rows}
    O["flips_lock"] = flips_lock
    O["verdict"] = "вопрос владельцу" if flips_lock else "менять нечего"
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=lag228 n::{O['decisions']} decisions, {O['traders']} traders, window {O['window']}, no data {nodata}, "
          f"lock traders in data {O['lock_traders_in_data']}")
    for src, P in O["by_src"].items():
        L = P["live_delay_min"]
        print(f"::notice title=lag228 {src}::traders {P['traders']} pass " + " ".join(f"d{d} {P[f'pass_d{d}']}" for d in DELAYS)
              + f" | flip_out {len(P['flip_out'])} flip_in {len(P['flip_in'])} | all d2 {P['all']['d2']} d{L} {P['all'][f'd{L}']}"
              + f" | passing d2 {P['passing_d2']['d2']} d{L} {P['passing_d2'][f'd{L}']} | diff all {P['diff_live_minus_2_all']}"
              + f" diff passing {P['diff_live_minus_2_passing']}")
        for row in P["lock"]:
            print(f"::notice title=lag228 lock {src}::" + json.dumps(row, ensure_ascii=False))
    print(f"::notice title=lag228 verdict::{O['verdict']} flips_lock {flips_lock}")


if __name__ == "__main__":
    main()
