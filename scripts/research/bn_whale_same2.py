"""Backlog 131 (owner 10.11: «монета занята, но второй кит в ту же сторону — добавить к позиции»), Binance lead traders.

Research only — reads ws_trips from the whale study's database (Actions cache), writes nothing. Trades are built exactly as
bn_whale_book.py (backlog 101): decisions = ws_trips merged within 4 h per coin+side, live bar walk-forward (prior 60 d), copy
+2 min, exit +2 min after the whale, stop −16 %, costs 0.11 % a round trip + funding, 5m Binance candles.

Pre-registered before any result was seen (1 alternative, 2 pools — top-10 is primary):
 BASE = the live book (book_sim.simulate): risk 3 % at the 16 % stop, daily stop 3 %, ≤ 15 open copies, one copy per coin,
        first come first served.
 S2   = the same, except: a decision whose coin is taken ONLY by copies of the SAME side from OTHER traders, with < 2 copies on
        that coin, is taken as a second copy with risk 2 % (2/3 of the normal size). Opposite-side and same-trader decisions stay
        skipped. The 15-slot cap counts the second copy as a slot.
 Pass: S2 − BASE paired daily t ≥ 2.5 in BOTH halves on top-10, total > 0, worst day and max drawdown not worse than 1.25 × BASE;
       all-bar must not be negative (sign check). Also reported: n and mean of the added second copies by half.
Usage: bn_whale_same2.py DB OUT.json"""
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


def simulate(trades, mark, second=False, max_pos=15, X=3.0):
    ev = sorted(trades, key=lambda x: x["t_in"])
    openp, closed_today, day_pnl = [], {}, {}
    blocked, taken, busy, added = set(), [], 0, []

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
        w8 = 1.0
        if same:
            ok = second and len(same) < 2 and all(p["side"] == tr["side"] and p["w"] != tr["w"] for p in same)
            if not ok:
                busy += 1; continue
            w8 = W2
        if max_pos is not None and len(openp) >= max_pos:
            busy += 1; continue
        p = dict(tr, w8=w8); openp.append(p); taken.append(tr["ret"])
        if w8 < 1: added.append(tr)
    close_until(float("inf"))
    return {"days": day_pnl, "taken": len(taken), "busy": busy, "added": added}


def report(trades, mark, tag):
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1}
    sims = {"base": simulate(trades, mark), "S2": simulate(trades, mark, second=True)}
    for k, S in sims.items():
        cell = {"taken": S["taken"], "busy": S["busy"]}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        R[k] = cell
        print(f"::notice title={tag} {k}::taken {cell['taken']} busy {cell['busy']} | all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}", flush=True)
    ad = sims["S2"]["added"]
    A = {"n": len(ad), "mean": round(st.mean(x["ret"] for x in ad), 3) if ad else None,
         "win": round(sum(1 for x in ad if x["ret"] > 0) / len(ad), 3) if ad else None}
    for p, (a, b) in parts[1:]:
        xs = [x["ret"] for x in ad if a <= x["t_in"] // DAY <= b]
        A[p] = {"n": len(xs), "mean": round(st.mean(xs), 3) if xs else None}
    R["added"] = A
    P = {p: D.paired(sims["S2"]["days"], sims["base"]["days"], a, b) for p, (a, b) in parts}
    b0, b1 = R["base"]["all"], R["S2"]["all"]
    P["risk_ok"] = bool(b1["worst_day"] >= 1.25 * b0["worst_day"] and b1["max_dd"] >= 1.25 * b0["max_dd"])
    P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["all"]["d"] > 0 and P["risk_ok"])
    R["S2_minus_base"] = P
    print(f"::notice title={tag} added::{A}", flush=True)
    print(f"::notice title={tag} S2−base::{P}", flush=True)
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
