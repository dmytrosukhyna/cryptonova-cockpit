"""Binance lead traders: are SMALL traders worse to copy? (backlog 92, Binance half, 06.11.2026, research only)

Same pre-registered design as scripts/research/whale_size.py (OKX/HL): feature = median notional of the same trader's decisions
opened in the prior 30 days (>= 5), buckets < $5k / $5–50k / > $50k, copy +2 and +6 min (live median 341 s), exit +delay after
the trader, stop −16 %, costs 0.11 % + funding, 5m Binance candles (bn_whale_stops.py helpers). Pass bar for a «skip small» filter:
small − rest < 0 with t <= −2.5 and negative in both halves, both pools (A all traders with >= 20 decisions, B walk-forward bar:
prior 60 d closed decisions win >= 70 %, PF >= 2, >= 20, P&L > 0). Read-only. Usage: bn_whale_size.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B

DAY, H, MIN = 86_400_000, 3_600_000, 60_000


def s(xs):
    if len(xs) < 2: return {"n": len(xs)}
    m = st.mean(xs); sd = st.pstdev(xs); gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    return {"n": len(xs), "mean": round(m, 3), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(gp / gl, 2) if gl else None}


def diff(a, b):
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return {"d": round(st.mean(a) - st.mean(b), 3), "t": round((st.mean(a) - st.mean(b)) / se, 2) if se else None}


def bucket(v):
    return None if v is None else ("small" if v < 5_000 else "mid" if v < 50_000 else "large")


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) < 20: continue
        for e in mine:
            g = e["g"]; e["close_t"] = max(x[4] for x in g); e["notional"] = sum(abs(x[8] or 0) for x in g); e["pnl"] = sum(x[7] or 0 for x in g)
        mine.sort(key=lambda e: e["open_t"])
        done = []
        for e in mine:
            t0 = e["open_t"]
            prev = [d for d in done if t0 - 30 * DAY <= d[0] < t0]
            e["med"] = st.median([d[3] for d in prev]) if len(prev) >= 5 else None
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            e["bar"] = bar
            done.append((t0, e["close_t"], e["pnl"], e["notional"]))
            if e["med"] is not None: eps.append(e)
    print(f"bn decisions with a 30-day size history: {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps: syms[e["coin"] + "USDT"].append(e)
    data = {}
    for sym, es in syms.items():
        data[sym] = B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        r = {"w": e["trader"], "t": e["open_t"], "b": bucket(e["med"]), "own": bucket(e["notional"]), "bar": e["bar"],
             "short": e["close_t"] - e["open_t"] < 15 * MIN}
        ok = True
        for dl in (2, 6):
            v = B.copy_ret(S, e["side"], e["open_t"] + dl * MIN, e["close_t"] + dl * MIN, 16)
            if v is None or not math.isfinite(v): ok = False; break
            r[f"r{dl}"] = v
        if ok: res.append(r)
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = sorted(r["t"] for r in res)[len(res) // 2]
    O = {"n": len(res), "traders": len({r["w"] for r in res}),
         "traders_by_bucket": {b: len({r["w"] for r in res if r["b"] == b}) for b in ("small", "mid", "large")},
         "from": time.strftime("%Y-%m-%d", time.gmtime(min(r["t"] for r in res) / 1000)),
         "to": time.strftime("%Y-%m-%d", time.gmtime(max(r["t"] for r in res) / 1000))}
    print(f"::notice title=bn size n::{O['n']} decisions, {O['traders']} traders {O['traders_by_bucket']}, {O['from']} → {O['to']}")
    for pool, rs0 in (("A", res), ("B", [r for r in res if r["bar"]])):
        for dl in (2, 6):
            k = f"r{dl}"
            for part, f in (("all", lambda r: True), ("h1", lambda r: r["t"] < mid), ("h2", lambda r: r["t"] >= mid)):
                rs = [r for r in rs0 if f(r)]
                cell = {b: s([r[k] for r in rs if r["b"] == b]) for b in ("small", "mid", "large")}
                cell["d"] = diff([r[k] for r in rs if r["b"] == "small"], [r[k] for r in rs if r["b"] != "small"])
                O[f"{pool}_{k}_{part}"] = cell
                print(f"::notice title=bn size {pool} {k} {part}::" + " | ".join(
                    f"{b} {cell[b].get('n')} {cell[b].get('mean')} t{cell[b].get('t')} pf{cell[b].get('pf')}" for b in ("small", "mid", "large"))
                    + f" | small−rest {cell['d']}")
        sh = {b: round(sum(r["short"] for r in rs0 if r["b"] == b) / max(1, sum(1 for r in rs0 if r["b"] == b)), 3) for b in ("small", "mid", "large")}
        own = {b: s([r["r2"] for r in rs0 if r["own"] == b]) for b in ("small", "mid", "large")}
        O[f"{pool}_short"] = sh; O[f"{pool}_own"] = own
        print(f"::notice title=bn size {pool} extra::short<15m {sh} | own-size r2 " + " ".join(f"{b} {v.get('n')} {v.get('mean')} t{v.get('t')}" for b, v in own.items()))
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
