"""Backlog 132 (owner 10.11: «встречные киты по одной монете — чью сторону брать»), Binance lead traders, part (a) only.

Research only — reads ws_trips from the whale study's database (Actions cache), writes nothing. Trades are built exactly as
bn_whale_same2.py / bn_whale_book.py: decisions = ws_trips merged within 4 h per coin+side, live bar walk-forward (prior 60 d),
copy +2 min, exit +2 min after the whale, stop −16 %, costs 0.11 % a round trip + funding, 5m Binance candles.

Pre-registered before any result was seen (2 alternatives, 2 pools — top-10 is primary):
 BASE = the live book (book_sim.simulate logic): risk 3 % at the 16 % stop, daily stop 3 %, ≤ 15 open, one copy per coin,
        first come first served — a decision opposite to our open copy of ANOTHER trader is skipped.
 MAJ  = when such an opposite decision arrives, count distinct pool traders (the same top-n pool, copied or not) holding the
        coin at that moment on each side, the newcomer included; if the newcomer's side has a STRICT majority, close our copy
        at the 5m mark (0.11 % cost) and open the newcomer's copy; else skip as BASE.
 LAST = always switch to the newcomer (the latest opinion wins).
 Same-trader opposite decisions (hedge mode, backlog 129) are skipped in all variants.
 Pass: variant − BASE paired daily t ≥ 2.8 (2 variants, Bonferroni) in BOTH halves on top-10, total > 0, worst day and max
       drawdown not worse than 1.25 × BASE; all-bar must not be negative. Also reported: n of switches and the per-switch
       delta (new copy + early close − what the old copy finally made), in % of notional.
 Part (b) (OKX order-book imbalance) has no history — forward record only (web/books.json).
Usage: bn_whale_side.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
import book_sim as K
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
NOT = D.NOTIONAL
W2 = 2.0 / 3.0


def simulate(trades, mark, mode="base", max_pos=15, X=3.0):
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, day_pnl = [], {}, {}
    blocked, taken, busy, sw = set(), [], 0, []
    conflicts = 0
    by_coin = defaultdict(list)
    for x in ev: by_coin[x["coin"]].append(x)

    def book(d, v):
        day_pnl[d] = day_pnl.get(d, 0.0) + v; closed_today[d] = closed_today.get(d, 0.0) + v

    def close_until(t):
        nonlocal openp
        keep = []
        for p in sorted(openp, key=lambda p: p["t_out"]):
            if p["t_out"] <= t: book(p["t_out"] // DAY, p["ret"] * NOT * p["w8"])
            else: keep.append(p)
        openp = keep

    for tr in ev:
        t = tr["t_in"]; d = t // DAY
        close_until(t)
        if X is not None and d not in blocked:
            unl = 0.0
            for p in openp:
                px = mark(p["coin"], t)
                if px:
                    s = 1 if p["side"] == "LONG" else -1
                    u = s * (px / p["a"] - 1) * 100 * NOT * p["w8"]
                    if u < 0: unl += u
            if closed_today.get(d, 0.0) + unl <= -X: blocked.add(d)
        if X is not None and d in blocked: continue
        same = [p for p in openp if p["coin"] == tr["coin"]]
        if same:
            p = same[0]
            if p["side"] == tr["side"] or p["w"] == tr["w"]:
                busy += 1; continue
            conflicts += 1
            flip = False
            if mode == "last": flip = True
            elif mode == "maj":
                L, S = set(), set()
                for x in by_coin[tr["coin"]]:
                    if x["t_in"] <= t < x["t_out"] or x is tr:
                        (L if x["side"] == "LONG" else S).add(x["w"])
                mine, other = (L, S) if tr["side"] == "LONG" else (S, L)
                flip = len(mine) > len(other)
            px = mark(p["coin"], t) if flip else None
            if not px:
                busy += 1; continue
            s = 1 if p["side"] == "LONG" else -1
            r = s * (px / p["a"] - 1) * 100 - B.COST
            book(d, r * NOT * p["w8"])
            openp = [q for q in openp if q is not p]
            sw.append({"t": t, "delta": tr["ret"] + r - p["ret"], "new": tr["ret"], "early": r, "old": p["ret"]})
        if max_pos is not None and len(openp) >= max_pos:
            busy += 1; continue
        openp.append(dict(tr, w8=1.0)); taken.append(tr["ret"])
    close_until(float("inf"))
    return {"days": day_pnl, "taken": len(taken), "busy": busy, "switches": sw, "conflicts": conflicts}


def report(trades, mark, tag):
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1}
    sims = {k: simulate(trades, mark, mode=k) for k in ("base", "maj", "last")}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "busy": S["busy"], "conflicts": S["conflicts"]}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        sw = S["switches"]
        cell["switches"] = {"n": len(sw), "delta_mean": round(st.mean(x["delta"] for x in sw), 3) if sw else None,
                            "delta_pos": round(sum(1 for x in sw if x["delta"] > 0) / len(sw), 3) if sw else None,
                            "new_mean": round(st.mean(x["new"] for x in sw), 3) if sw else None,
                            "early_mean": round(st.mean(x["early"] for x in sw), 3) if sw else None,
                            "old_mean": round(st.mean(x["old"] for x in sw), 3) if sw else None}
        for p, (a, b) in parts[1:]:
            xs = [x["delta"] for x in sw if a <= x["t"] // DAY <= b]
            cell["switches"][p] = {"n": len(xs), "delta_mean": round(st.mean(xs), 3) if xs else None}
        R[k] = cell
        print(f"::notice title={tag} {k}::taken {cell['taken']} busy {cell['busy']} conflicts {cell['conflicts']} | all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']} | sw {cell['switches']}", flush=True)
    b0 = R["base"]["all"]
    for k in ("maj", "last"):
        P = {p: D.paired(sims[k]["days"], sims["base"]["days"], a, b) for p, (a, b) in parts}
        b1 = R[k]["all"]
        P["risk_ok"] = bool(b1["worst_day"] >= 1.25 * b0["worst_day"] and b1["max_dd"] >= 1.25 * b0["max_dd"])
        P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.8 and P["h2"]["t"] >= 2.8 and P["all"]["d"] > 0 and P["risk_ok"])
        R[k + "_minus_base"] = P
        print(f"::notice title={tag} {k}−base::{P}", flush=True)
    return R


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
            if bar and e["close_t"] - t0 > 2 * MIN: cand.append(e)
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
        trades.append({"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "t_out": v[1], "a": v[0], "ret": v[2]})

    def mark(coin, t):
        S = data.get(coin + "USDT")
        if not S: return None
        i = bisect.bisect_right(S["t"], t) - 1
        return S["k"][S["t"][i]][3] if i >= 0 else None

    O = {"traders": len({t["w"] for t in trades})}
    O["top10"] = report(D.top_n(trades, status, 10), mark, "bn top10")
    O["all_bar"] = report(trades, mark, "bn all-bar")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
