"""Backlog 200 (18.11.2026): a «stop copying» signal — does a change in a lead trader's BEHAVIOUR (last 7 days against his own
prior 60 days) warn of worse copies in the next 14 days? Binance lead traders, research only — reads ws_trips from the whale
study's database (Actions cache), writes nothing. Information for the lock review only; real rules are not touched.

Decisions and copies exactly as bn_whale_lastexit.py (backlog 198): ws_trips merged within 4 h per coin+side, the live bar
walk-forward (prior 60 d: ≥ 20 decisions, win ≥ 70 %, PF ≥ 2, P&L > 0), copy +2 min, exit +2 min after the trader, stop −16 %
from OUR entry, costs 0.11 % + funding, 5m Binance candles. Unit = one bar-passing decision's copy return (% of notional).

Behaviour flags, checked at every UTC midnight D for every trader, recent window R = [D−7 d, D), baseline W = [D−67 d, D−7 d)
(position rows of ws_trips; R needs ≥ 3 rows, W ≥ 10). Pre-registered before any result was seen — 3 signs, fixed thresholds:
 LEV  — median leverage in R ≥ 1.5 × median leverage in W;
 SIZE — median notional in R ≥ 2 × median notional in W;
 HOLD — median hold of LOSING rows closed in R ≥ 2 × median hold of losing rows closed in W (R needs ≥ 2 losers, W ≥ 3).
A decision is «flagged by K» if its trader had a K-flag on some midnight D with D ≤ open < D + 14 d.
Pass (per sign, primary pool top-10, Bonferroni for 3 signs → |t| ≥ 2.4): n_flagged ≥ 30, mean(flagged) < mean(rest) with
Welch t ≤ −2.4, and the sum of flagged copies < 0 in BOTH halves (by entry day). ANY = at least one sign, shown for information.
All bar-passing traders = sign check. Frequency (decisions in R ≥ 2 × W's weekly rate) is printed as information only.
Usage: bn_whale_drift.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import daystop_sim as D
from bn_whale_book import copy2

DAY, H, MIN = 86_400_000, 3_600_000, 60_000
SIGNS = ("LEV", "SIZE", "HOLD", "FREQ")


def med(xs):
    return st.median(xs) if xs else None


def flags_for(rows):
    """rows: one trader's ws_trips (coin, side, open_t, close_t, pnl, notional, lever) sorted by open_t → {sign: [midnights]}"""
    out = {k: [] for k in SIGNS}
    if not rows: return out
    d0 = rows[0][2] // DAY + 68; d1 = max(r[3] for r in rows) // DAY + 1
    opens = [r[2] for r in rows]
    closed = sorted((r for r in rows if r[3] and r[3] > 0), key=lambda r: r[3]); ct = [r[3] for r in closed]
    for d in range(d0, d1 + 1):
        D_ = d * DAY
        a, b, c = bisect.bisect_left(opens, D_ - 67 * DAY), bisect.bisect_left(opens, D_ - 7 * DAY), bisect.bisect_left(opens, D_)
        W, R = rows[a:b], rows[b:c]
        if len(R) >= 3 and len(W) >= 10:
            lw = med([r[6] for r in W if r[6]]); lr = med([r[6] for r in R if r[6]])
            if lw and lr and lr >= 1.5 * lw: out["LEV"].append(D_)
            nw = med([r[5] for r in W if r[5]]); nr = med([r[5] for r in R if r[5]])
            if nw and nr and nr >= 2 * nw: out["SIZE"].append(D_)
            if len(R) >= 2 * len(W) / 60 * 7 and len(R) >= 6: out["FREQ"].append(D_)
        a, b, c = bisect.bisect_left(ct, D_ - 67 * DAY), bisect.bisect_left(ct, D_ - 7 * DAY), bisect.bisect_left(ct, D_)
        LW = [r[3] - r[2] for r in closed[a:b] if (r[4] or 0) < 0]; LR = [r[3] - r[2] for r in closed[b:c] if (r[4] or 0) < 0]
        if len(LR) >= 2 and len(LW) >= 3 and med(LR) >= 2 * med(LW): out["HOLD"].append(D_)
    return out


def flagged(fl, t):
    i = bisect.bisect_right(fl, t) - 1
    return i >= 0 and t - fl[i] < 14 * DAY


def welch(a, b):
    if len(a) < 2 or len(b) < 2: return None
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (st.mean(a) - st.mean(b)) / se if se > 0 else None


def report(trades, flags, tag):
    d0 = min(t["t_in"] for t in trades) // DAY; d1 = max(t["t_in"] for t in trades) // DAY; mid = (d0 + d1) // 2
    R = {"n": len(trades), "days": d1 - d0 + 1, "mean_all": round(st.mean(t["ret"] for t in trades), 3)}
    for k in SIGNS + ("ANY",):
        def is_f(tr):
            fl = flags.get(tr["w"], {})
            return any(flagged(fl.get(s, []), tr["t_in"]) for s in ("LEV", "SIZE", "HOLD")) if k == "ANY" else flagged(fl.get(k, []), tr["t_in"])
        F = [t for t in trades if is_f(t)]; O = [t for t in trades if not is_f(t)]
        fa, oa = [t["ret"] for t in F], [t["ret"] for t in O]
        h1 = sum(t["ret"] for t in F if t["t_in"] // DAY <= mid); h2 = sum(t["ret"] for t in F if t["t_in"] // DAY > mid)
        t = welch(fa, oa)
        A = {"n_flag": len(F), "traders_flag": len({x["w"] for x in F}), "mean_flag": round(st.mean(fa), 3) if fa else None,
             "mean_rest": round(st.mean(oa), 3) if oa else None, "win_flag": round(sum(1 for x in fa if x > 0) / len(fa), 3) if fa else None,
             "win_rest": round(sum(1 for x in oa if x > 0) / len(oa), 3) if oa else None,
             "sum_flag": round(sum(fa), 2), "h1": round(h1, 2), "h2": round(h2, 2), "t": round(t, 2) if t is not None else None}
        A["pass"] = bool(k in ("LEV", "SIZE", "HOLD") and len(F) >= 30 and t is not None and t <= -2.4 and h1 < 0 and h2 < 0)
        R[k] = A
        print(f"::notice title={tag} {k}::{A}", flush=True)
    return R


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    raw = c.execute("SELECT trader, coin, side, open_t, close_t, pnl, notional, lever FROM ws_trips WHERE src='bn' AND open_t > 0 "
                    "ORDER BY trader, open_t").fetchall()
    allrows = defaultdict(list)
    for r in raw: allrows[r[0]].append(r[1:])
    flags = {w: flags_for(v) for w, v in allrows.items()}
    print(f"::notice title=bn drift flags::traders {len(flags)}, trader-days flagged " +
          str({k: sum(len(f[k]) for f in flags.values()) for k in SIGNS}), flush=True)
    rows = [r[:6] for r in raw if r[4] and r[4] > 0]
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
        trades.append({"w": e["w"], "coin": e["coin"], "side": e["side"], "t_in": e["open_t"] + 2 * MIN, "ret": v[2]})
    O = {"traders": len({t["w"] for t in trades})}
    O["top10"] = report(D.top_n(trades, status, 10), flags, "bn top10")
    O["all_bar"] = report(trades, flags, "bn all-bar")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
