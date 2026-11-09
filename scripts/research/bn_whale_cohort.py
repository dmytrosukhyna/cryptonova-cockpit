"""Backlog 106, Binance half: copy size by the trader's 60-day quality (09.11.2026, research only).

Design and pass bar: cohort_sim.py (pre-registered, same as whale_cohort.py for OKX/HL); trades as bn_whale_daystop.py. Decisions = ws_trips (src 'bn')
merged within 4 h per coin+side, live bar walk-forward (prior 60 d), primary pool top-10 by 60-day P&L of bar-passing traders,
5m Binance candles (bn_whale_stops.py helpers). Read-only. Usage: bn_whale_cohort.py DB OUT.json"""
import bisect, json, math, os, sqlite3, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
import cohort_sim as K

DAY, H, MIN = 86_400_000, 3_600_000, 60_000


def copy2(S, side, t_in, t_out, stop=16.0):
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i: return None
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]; b = S["k"][S["t"][j]][0]; te = S["t"][j]
    lvl = a * (1 - s * stop / 100)
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
            b = o if q > i and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
            te = S["t"][q]; break
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= te) * 100 * s
    return a, te, s * (b / a - 1) * 100 - B.COST - f


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl FROM ws_trips WHERE src='bn' AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows: by[r[0]].append(r)
    cand, status = [], {}
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H: e["g"].append(r)
            else:
                e = {"w": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        for e in mine:
            e["close_t"] = max(x[4] for x in e["g"]); e["pnl"] = sum(x[5] or 0 for x in e["g"])
        mine.sort(key=lambda e: e["open_t"])
        done = []
        for e in mine:
            t0 = e["open_t"]
            pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            done.append((t0, e["close_t"], e["pnl"]))
            status.setdefault(tr, []).append((t0, bar, sum(d[2] for d in pri)))
            if bar and e["close_t"] - t0 > 2 * MIN:
                e["pf"], e["sh"] = K.quality(pri); cand.append(e)
    print(f"bn bar-passing decisions: {len(cand)}", flush=True)
    syms = defaultdict(list)
    for e in cand: syms[e["coin"] + "USDT"].append(e)
    data = {sym: B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY) for sym, es in syms.items()}
    trades = []
    for e in cand:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        v = copy2(S, e["side"], e["open_t"] + 2 * MIN, e["close_t"] + 2 * MIN)
        if v is None or not math.isfinite(v[2]): continue
        trades.append({"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "t_out": v[1], "a": v[0], "ret": v[2], "pf": e["pf"], "sh": e["sh"]})

    def mark(coin, t):
        S = data.get(coin + "USDT")
        if not S: return None
        i = bisect.bisect_right(S["t"], t) - 1
        return S["k"][S["t"][i]][3] if i >= 0 else None

    O = {"traders": len({t["w"] for t in trades})}
    O["top10"] = K.report(D.top_n(trades, status, 10), mark, "bn top10")
    O["all_bar"] = K.report(trades, mark, "bn all-bar")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
