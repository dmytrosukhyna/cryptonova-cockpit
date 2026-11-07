"""Backlog 103, 07.11.2026: is a late «join» (entering a lead trader's position that is already open) worse than entering with him?
Binance half. Research only — reads ws_trips (src='bn') from the whale study's database (Actions cache), writes nothing to it.

Pre-registered (written before any result was seen):
- decision = one trader's entries in the same coin + side within 4 h (as in bn_whale_delay.py), trader ≥ 20 decisions;
- lags L = 2 min (baseline, «with the whale», no window), 60, 240, 720, 1440 min after the whale's open;
- a join at lag L exists only if the decision is still open at L + 1 min and the price at L is inside the live window
  −3 % … +1 % of the whale's entry known at L (notional-weighted entry of his slices opened by then; side-adjusted, + = worse);
- copy: entry at L, exit 2 min after the whale's last close, stop −16 %, 0.11 % a round trip + funding (1-minute candles);
- outputs per lag: mean / t / PF / win, both halves of time; paired Δ(join L − entry 2 min) on the same decisions.
- verdict «late joins are worse»: for some L the join's own mean ≤ 0 in BOTH halves, or the paired Δ < 0 with t ≤ −2.5
  (4 lags × 2 sources) and < 0 in both halves. A money-rule change («no joins later than N h») only with Dmytro's yes.
Usage: bn_whale_join.py DB OUT.json"""
import json, os, sqlite3, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as B

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
        base = B.copy(S, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1)
        if base is None:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "hold_h": (e["close_t"] - e["open_t"]) / H, "L2": base}
        for L in LAGS[1:]:
            t_in = e["open_t"] + L * M1
            if e["close_t"] <= t_in + M1:
                r[f"L{L}"] = None; continue
            v = B.copy(S, e["side"], t_in, e["close_t"] + 2 * M1, w_entry_at(e["g"], t_in), window=True)
            r[f"L{L}"] = None if v in (None, "skip") else v
            r[f"open{L}"] = True
        res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    O = {"n": len(res), "traders": len({r["trader"] for r in res}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))}
    for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
        rs = [r for r in res if f(r)]
        q = {"L2": B.stat([r["L2"] for r in rs])}
        for L in LAGS[1:]:
            js = [r for r in rs if r.get(f"L{L}") is not None]
            q[f"L{L}"] = B.stat([r[f"L{L}"] for r in js])
            q[f"open{L}"] = sum(1 for r in rs if r.get(f"open{L}"))
            q[f"same_L2_{L}"] = B.stat([r["L2"] for r in js])
            q[f"Δ{L}"] = B.stat([r[f"L{L}"] - r["L2"] for r in js])
        O[p] = q
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=join n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']}")
    for p in ("all", "h1", "h2"):
        q = O[p]
        line = " ".join(f"L{L}:{q[f'L{L}'].get('mean')}/n{q[f'L{L}'].get('n')}/pf{q[f'L{L}'].get('pf')}" for L in LAGS)
        dl = " ".join(f"Δ{L}:{q[f'Δ{L}'].get('mean')}/t{q[f'Δ{L}'].get('t')}" for L in LAGS[1:])
        print(f"::notice title=join {p}::{line} || {dl}")


if __name__ == "__main__":
    main()
