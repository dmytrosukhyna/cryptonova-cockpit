"""Backlog 164, Binance half (14.11.2026, research only): join depth (the whale's loss at our join). Design pre-registered in
joindepth_common.py; population, candles and copy model as bn_whale_join.py (backlog 103). Reads ws_trips, writes nothing to it.
Usage: bn_whale_joindepth.py DB OUT.json"""
import bisect, json, os, sqlite3, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as B
import joindepth_common as JC

M1, H = 60_000, 3_600_000
LAGS = [2, 60, 240, 720, 1440]


def w_entry_at(g, t):
    xs = [(x[5], max(x[7] or 0, 1e-9)) for x in g if x[3] <= t and x[5]]
    if not xs:
        return None
    return sum(a * w for a, w in xs) / sum(w for _, w in xs)


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
        e["close_t"] = max(x[4] for x in e["g"])
    print(f"bn rows {len(rows)}, decisions: {len(eps)}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = B.load_sym(sym, [(x["open_t"], x["close_t"]) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        if B.copy(S, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1) is None:
            continue
        s = 1 if e["side"] == "LONG" else -1
        for L in JC.LAGS:
            t_in = e["open_t"] + L * M1
            if e["close_t"] <= t_in + M1:
                continue
            we = w_entry_at(e["g"], t_in)
            v = B.copy(S, e["side"], t_in, e["close_t"] + 2 * M1, we, window=True)
            if v in (None, "skip"):
                continue
            T = S["t"]; i = bisect.bisect_left(T, t_in)
            gap = s * (S["k"][T[i]][0] / we - 1) * 100
            res.append({"dec": f"{e['trader']}|{e['open_t']}|{e['coin']}|{e['side']}", "t": e["open_t"], "L": L, "gap": gap, "ret": v})
    O = JC.report(res, "binance")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    for ln in JC.lines(O):
        if ln.startswith("  pairs"):
            continue
        print(f"::notice title=joindepth::{ln.strip()}")


if __name__ == "__main__":
    main()
