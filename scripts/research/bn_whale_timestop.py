"""Backlog 219 (20.11.2026): a time stop for a LOSING copy — our copy is open ≥ 72 h and the move is ≤ −5 % → close it, even if the
trader still holds (20.11: Ivan's ARB long 6 days, stopped −16 %; OP/LDO 6 days at −7…−12 %). Binance lead traders, research
only — reads ws_trips from the whale study's database (Actions cache), writes nothing. Real rules are not touched.

Decisions and copies exactly as bn_whale_drift.py / bn_whale_lastexit.py: ws_trips merged within 4 h per coin+side, the live bar
walk-forward (prior 60 d: ≥ 20 decisions, win ≥ 70 %, PF ≥ 2, P&L > 0), copy +2 min, exit +2 min after the trader, stop −16 %
from OUR entry, costs 0.11 % + funding, 5m Binance candles. Unit = one bar-passing decision's copy return (% of notional).

Rule (pre-registered before any result was seen): at our entry + T the move (5m bar open) is ≤ X → we exit at that bar
(costs and funding to that moment), otherwise the copy runs as is. Only copies still open at entry + T can differ.
 Primary: T = 72 h, X = −5 %. Second variant: T = 72 h, X = −3 %. Information only: T = 48 h, X = −5 %.
Pass (per variant, primary pool top-10; 2 variants → Bonferroni, paired t ≥ 2.24):
 n_cut ≥ 20, sum(rule − as-is) > 0, > 0 in BOTH halves (by entry day), paired t of the differences over copies open at T ≥ 2.24,
 and the profit cut from long-held copies that ended positive ≤ 20 % of their total profit.
BTC «rise»/«fall» at entry (BTC close vs 20 days earlier) is reported separately, information only.
Usage: bn_whale_timestop.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
VARIANTS = (("T72_X5", 72, -5.0, True), ("T72_X3", 72, -3.0, True), ("T48_X5", 48, -5.0, False))


def move_at(S, side, t_in, t):
    i, j = B.bar_at(S, t_in), B.bar_at(S, t)
    if i is None or j is None or j <= i: return None
    s = 1 if side == "LONG" else -1
    return s * (S["k"][S["t"][j]][0] / S["k"][S["t"][i]][0] - 1) * 100


def report(trades, tag):
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_in"] for t in trades) // DAY; mid = (d0 + d1) // 2
    R = {"n": len(trades), "days": d1 - d0 + 1, "mean_all": round(st.mean(t["ret"] for t in trades), 3),
         "sum_all": round(sum(t["ret"] for t in trades), 2)}
    for name, T, X, judged in VARIANTS:
        longh = [t for t in trades if t["hold_h"] >= T and t.get(name) is not None]
        diffs = [t[name] - t["ret"] for t in longh]
        cut = [t for t in longh if t[name] != t["ret"]]
        h1 = sum(t[name] - t["ret"] for t in longh if t["t_in"] // DAY <= mid)
        h2 = sum(t[name] - t["ret"] for t in longh if t["t_in"] // DAY > mid)
        pos = [t for t in longh if t["ret"] > 0]
        lost = sum(t["ret"] - t[name] for t in pos if t[name] < t["ret"])
        pos_sum = sum(t["ret"] for t in pos)
        tt = None
        if len(diffs) >= 2 and st.pstdev(diffs) > 0:
            tt = st.mean(diffs) / (st.stdev(diffs) / math.sqrt(len(diffs)))
        reg = {}
        for rg in ("rise", "fall"):
            sub = [t for t in longh if t.get("reg") == rg]
            reg[rg] = {"n": len(sub), "n_cut": sum(1 for t in sub if t[name] != t["ret"]),
                       "diff": round(sum(t[name] - t["ret"] for t in sub), 2)}
        A = {"n_open_at_T": len(longh), "n_cut": len(cut), "cut_saved": round(sum(t[name] - t["ret"] for t in cut if t[name] > t["ret"]), 2),
             "cut_lost": round(sum(t["ret"] - t[name] for t in cut if t[name] < t["ret"]), 2),
             "cut_better_share": round(sum(1 for t in cut if t[name] > t["ret"]) / len(cut), 3) if cut else None,
             "sum_diff": round(sum(diffs), 2), "h1": round(h1, 2), "h2": round(h2, 2), "t": round(tt, 2) if tt is not None else None,
             "pos_profit": round(pos_sum, 2), "pos_lost_share": round(lost / pos_sum, 3) if pos_sum > 0 else None,
             "mean_asis_cut": round(st.mean(t["ret"] for t in cut), 2) if cut else None,
             "mean_rule_cut": round(st.mean(t[name] for t in cut), 2) if cut else None, "regime": reg}
        A["pass"] = bool(judged and len(cut) >= 20 and sum(diffs) > 0 and h1 > 0 and h2 > 0 and tt is not None and tt >= 2.24
                         and pos_sum > 0 and lost / pos_sum <= 0.20)
        R[name] = A
        print(f"::notice title={tag} {name}::{A}", flush=True)
    return R


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, pnl FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
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
    btc = B.load_sym("BTCUSDT", min(e["open_t"] for e in cand) - 25 * DAY, max(e["close_t"] for e in cand) + DAY)

    def regime(t):
        if not btc: return None
        i = bisect.bisect_right(btc["t"], t) - 1; j = bisect.bisect_right(btc["t"], t - 20 * DAY) - 1
        if i < 0 or j < 0: return None
        return "rise" if btc["k"][btc["t"][i]][3] > btc["k"][btc["t"][j]][3] else "fall"

    trades = []
    for e in cand:
        S = data.get(e["coin"] + "USDT")
        if not S: continue
        t_in = e["open_t"] + 2 * MIN
        v = copy2(S, e["side"], t_in, e["close_t"] + 2 * MIN)
        if v is None or not math.isfinite(v[2]): continue
        tr = {"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": t_in, "t_out": v[1], "ret": v[2],
              "hold_h": (v[1] - t_in) / H, "reg": regime(t_in)}
        for name, T, X, _ in VARIANTS:
            tT = t_in + T * H
            if v[1] <= tT: tr[name] = v[2]; continue
            m = move_at(S, e["side"], t_in, tT)
            if m is None: tr[name] = None; continue
            if m <= X:
                w = copy2(S, e["side"], t_in, tT)
                tr[name] = w[2] if w and math.isfinite(w[2]) else None
            else: tr[name] = v[2]
        trades.append(tr)
    hs = sorted(t["hold_h"] for t in trades)
    O = {"traders": len({t["w"] for t in trades}), "copies": len(trades),
         "hold_ge72": sum(1 for h in hs if h >= 72), "hold_ge48": sum(1 for h in hs if h >= 48),
         "hold_median_h": round(st.median(hs), 1) if hs else None}
    print(f"::notice title=bn timestop sample::{O}", flush=True)
    O["top10"] = report(D.top_n(trades, status, 10), "bn top10")
    O["all_bar"] = report(trades, "bn all-bar")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
