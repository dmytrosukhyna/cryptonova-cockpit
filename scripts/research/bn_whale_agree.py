"""Backlog 127 (согласие китов): are decisions that ANOTHER bar-passing trader already backs (same coin, same side, opened within
the previous 24 h) better than solo decisions? Binance lead traders, research only — reads ws_trips from the whale study's database
(Actions cache), writes nothing. Trades are built exactly as bn_whale_same2.py / bn_whale_book.py (backlog 101): decisions =
ws_trips merged within 4 h per coin+side, live bar walk-forward (prior 60 d), copy +2 min, exit +2 min after the whale, stop −16 %,
costs 0.11 % a round trip + funding, 5m Binance candles.

Pre-registered before any result was seen (2 pools — top-10 is primary):
 AGREED = at our copy time, another trader IN THE SAME POOL has a decision on the same coin and side whose open time is in the
          previous 24 h (only past information). SOLO = all others.
 A (decision level): mean return per copy, win share, by half. Passes only if AGREED − SOLO > 0 with Welch t ≥ 2 in BOTH halves
   and each half has ≥ 15 agreed decisions.
 B (book): live book (book_sim-equivalent: risk 3 %, daily stop 3 %, ≤ 15 open, one copy per coin, first come) against W =
   the same book with weight 2.0 on AGREED and 0.5 on SOLO copies; W's daily P&L is then divided by the mean weight of the copies
   it took (same average risk as the base). Pass: W_norm − BASE paired daily t ≥ 2.5 in BOTH halves on top-10, total > 0,
   worst day and max drawdown not worse than 1.25 × BASE.
Usage: bn_whale_agree.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
import book_sim as K
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
NOT = D.NOTIONAL


def simulate(trades, mark, wfn=None, max_pos=15, X=3.0):
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
        w8 = wfn(tr) if wfn else 1.0
        if same:
            busy += 1; continue
        if max_pos is not None and len(openp) >= max_pos:
            busy += 1; continue
        p = dict(tr, w8=w8); openp.append(p); taken.append(tr["ret"]); added.append(w8)
    close_until(float("inf"))
    return {"days": day_pnl, "taken": len(taken), "busy": busy, "added": added}


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return round((st.mean(a) - st.mean(b)) / se, 2) if se > 0 else None


def tag_agree(trades):
    by = defaultdict(list)
    for t in trades: by[(t["coin"], t["side"])].append(t)
    for k, ls in by.items():
        ls.sort(key=lambda x: x["t_in"])
        for i, t in enumerate(ls):
            t["agree"] = any(o["w"] != t["w"] and t["t_in"] - DAY <= o["t_in"] < t["t_in"] for o in ls[:i])
    return trades


def report(trades, mark, tag):
    trades = tag_agree([dict(t) for t in trades])
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_out"] for t in trades) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    R = {"n": len(trades), "days": d1 - d0 + 1}
    A = {}
    for p, (a, b) in parts:
        ag = [t["ret"] for t in trades if t["agree"] and a <= t["t_in"] // DAY <= b]
        so = [t["ret"] for t in trades if not t["agree"] and a <= t["t_in"] // DAY <= b]
        f = lambda xs: {"n": len(xs), "mean": round(st.mean(xs), 3) if xs else None,
                        "win": round(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None}
        A[p] = {"agreed": f(ag), "solo": f(so), "t": welch(ag, so)}
    A["pass"] = bool(all(A[h]["t"] is not None and A[h]["t"] >= 2 and A[h]["agreed"]["n"] >= 15 for h in ("h1", "h2")))
    R["A"] = A
    print(f"::notice title={tag} A::{A}", flush=True)
    base = simulate(trades, mark)
    W = simulate(trades, mark, wfn=lambda t: 2.0 if t["agree"] else 0.5)
    mw = st.mean(W["added"]) if W["added"] else 1.0
    W["days"] = {d: v / mw for d, v in W["days"].items()}
    R["W_mean_weight"] = round(mw, 3)
    for k, S in (("base", base), ("W_norm", W)):
        cell = {"taken": S["taken"], "busy": S["busy"]}
        for p, (a, b) in parts: cell[p] = {k2: (float(v) if v is not None else None) for k2, v in D.curve_stats(S["days"], a, b).items()}
        R[k] = cell
        print(f"::notice title={tag} {k}::taken {cell['taken']} busy {cell['busy']} | all {cell['all']} | h1 {cell['h1']['total']} | h2 {cell['h2']['total']}", flush=True)
    P = {p: D.paired(W["days"], base["days"], a, b) for p, (a, b) in parts}
    b0, b1 = R["base"]["all"], R["W_norm"]["all"]
    P["risk_ok"] = bool(b1["worst_day"] >= 1.25 * b0["worst_day"] and b1["max_dd"] >= 1.25 * b0["max_dd"])
    P["pass_here"] = bool(P["h1"]["t"] and P["h2"]["t"] and P["h1"]["t"] >= 2.5 and P["h2"]["t"] >= 2.5 and P["all"]["d"] > 0 and P["risk_ok"])
    R["W_minus_base"] = P
    print(f"::notice title={tag} W−base::mean_w {mw:.3f} {P}", flush=True)
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
