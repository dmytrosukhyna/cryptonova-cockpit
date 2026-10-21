"""Backlog #4 (21.10.2026): new rule families for the Автолаборатория, tested with the SAME data, sizing, costs, walk-forward and
pass rules as scripts/autolab.py — but in a separate study, so the nightly lab is untouched until a family earns its place.

Families (each × 3 directions: only long / long and short / INVERTED):
  tom    — turn of the month: in the market only from the k-th last trading day to the m-th trading day of the next month
           (params (k, m) ∈ (1,3) (1,1) (2,2) (3,4) (0,3) (2,5));
  dow    — one weekday only (Mon…Fri; crypto Mon…Sun); the walk-forward picks the day from the past 5 years;
  dowx   — every day EXCEPT one weekday;
  volbrk — volatility breakout: a daily move bigger than k × the 20-day σ → hold H days in its direction (k ∈ 1.5/2/2.5, H ∈ 1/5/10).
Cross-market (one portfolio, rebalanced every 21 trading days on a common calendar, vol-weighted pieces):
  xsmom  — metals + indices + oil + Treasuries: long the top 3 by L-day return, short the bottom 3 (L ∈ 63/126/252, also with the last
           21 days skipped); long-only top 3; the walk-forward picks L; yardstick: equal weight of all markets long.
  xsmom crypto — the same on BTC/ETH/BNB/SOL/XRP (top 2 long, bottom 2 short).
Honesty: the number of variants is printed; the luck hurdle uses N = this study's rows + the nightly lab's 260 rules; Sharpe of the
two halves of the out-of-sample period is shown. Public data only (Binance spot, Yahoo via yfinance, Stooq). Nothing trades."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autolab as A  # noqa: E402

NIGHTLY_RULES = 260
TOM = [(1, 3), (1, 1), (2, 2), (3, 4), (0, 3), (2, 5)]
VB = [(k, h) for k in (1.5, 2.0, 2.5) for h in (1, 5, 10)]


def month_pos(dates):
    """For every day: its 1-based position in the month and 1-based position from the month's end (last day = 1)."""
    ym = [d[:7] for d in dates]
    n = len(dates)
    pos, fe = np.zeros(n, int), np.zeros(n, int)
    i = 0
    while i < n:
        j = i
        while j < n and ym[j] == ym[i]:
            j += 1
        L = j - i
        pos[i:j] = np.arange(1, L + 1)
        fe[i:j] = np.arange(L, 0, -1)
        if j == n:                       # the month still running: its end is unknown → assume a full month is ahead
            fe[i:j] = 99
        i = j
    return pos, fe


def ahead(x):
    """raw position at close t is decided by a property of day t+1 (known in advance: calendar)."""
    out = np.zeros(len(x))
    out[:-1] = x[1:]
    return out


def sig(fam, p, c, dates, wd, pos, fe):
    n = len(c)
    if fam == "tom":
        k, m = p
        inwin = ((fe <= k) | (pos <= m)).astype(float)
        return ahead(np.where(inwin > 0, 1.0, -1.0))
    if fam == "dow":
        return ahead(np.where(wd == p, 1.0, -1.0))
    if fam == "dowx":
        return ahead(np.where(wd == p, -1.0, 1.0))
    if fam == "volbrk":
        k, h = p
        r = np.r_[0.0, c[1:] / c[:-1] - 1]
        out, left, side = np.zeros(n), 0, 0.0
        for i in range(21, n):
            sd = r[i - 20:i].std()
            if sd > 0 and abs(r[i]) > k * sd:
                left, side = h, math.copysign(1.0, r[i])
            if left > 0:
                out[i] = side
                left -= 1
        return out
    raise ValueError(fam)


def halves(oos, ppy):
    h = len(oos) // 2
    f = lambda x: round(float(x.mean() / x.std() * math.sqrt(ppy)), 2) if x.std() else 0.0  # noqa: E731
    return f(oos[:h]), f(oos[h:])


def vw(c, ppy):
    """A.vol_weight caches by id(); temporary column arrays reuse ids, so drop the cache first."""
    A._W.clear()
    return A.vol_weight(c, ppy)


def strat_fresh(c, raw, mode, cost, ppy):
    A._W.clear()
    return A.strat_returns(c, raw, mode, cost, ppy)


def closes(data, keys):
    maps = [dict(zip(*data[k])) for k in keys]
    common = sorted(set.intersection(*[set(m) for m in maps]))
    return np.array([[m[d] for m in maps] for d in common]), common


def xs_portfolio(data, keys, ppy, cost, top, L, skip, long_only):
    """Common calendar (dates where every market of `keys` has a close, after each has 300 days); every 21 days rank by the
    L-day return (optionally skipping the last 21 days); long top `top` (and short the bottom `top`), each piece vol-weighted."""
    C, common = closes(data, keys)
    R = np.zeros_like(C)
    R[1:] = C[1:] / C[:-1] - 1
    W = np.column_stack([vw(np.ascontiguousarray(C[:, j]), ppy) for j in range(len(keys))])
    T, K = C.shape
    pos = np.zeros((T, K))
    cur = np.zeros(K)
    for t in range(T):
        if t >= L + skip + 1 and (t % 21 == 0):
            mom = C[t - skip] / C[t - skip - L] - 1
            order = np.argsort(mom)
            cur = np.zeros(K)
            cur[order[-top:]] = 1.0
            if not long_only:
                cur[order[:top]] = -1.0
        pos[t] = cur
    expo = pos * W / top
    out = np.zeros(T)
    out[1:] = (expo[:-1] * R[1:]).sum(axis=1) - cost * np.abs(np.diff(expo, axis=0)).sum(axis=1)
    return common, out


def market_rows(data, meta, fams):
    rows, tried = [], 0
    for key, (dates, c) in data.items():
        cls = meta[key][2]
        ppy = 365 if cls == "crypto" else 252
        wd = np.array([dt.date.fromisoformat(d).weekday() for d in dates])
        pos, fe = month_pos(dates)
        days = list(range(7)) if cls == "crypto" else list(range(5))
        fset = dict(fams)
        fset["dow"] = ("Один день недели", days)
        fset["dowx"] = ("Все дни, кроме одного", days)
        for fam, (_ru, params) in fset.items():
            raws = {p: sig(fam, p, c, dates, wd, pos, fe) for p in params}
            for mode in A.MODES:
                tried += len(params)
                A._W.clear()
                ser = {p: A.strat_returns(c, raws[p], mode, A.COST[cls], ppy) for p in params}
                oos, start, chosen = A.walk_forward(ser, ppy)
                st = A.stats(oos, dates[start:], ppy)
                if not st:
                    continue
                h1, h2 = halves(oos, ppy)
                rows.append({"m": key, "cls": cls, "f": fam, "mode": mode, "p": chosen[-1] if chosen else None,
                             "sh": st["sharpe"], "cagr": st["cagr"], "dd": st["dd"], "py": f"{st['pos_years']}/{st['n_years']}",
                             "_py": st["pos_years"] / max(1, st["n_years"]), "_ny": st["n_years"], "s3": st["sharpe3y"],
                             "h1": h1, "h2": h2, "from": st["from"]})
    return rows, tried


def judge(rows, N):
    for r in rows:
        hurdle = math.sqrt(2 * math.log(N)) / math.sqrt(max(1, r["_ny"]))
        r["passes"] = bool(r["sh"] >= 0.4 and r["_ny"] >= 3 and r["_py"] >= 0.6 and r["dd"] >= -35 and r["s3"] > 0)
        r["reliable"] = bool(r["passes"] and r["sh"] >= hurdle)
        r["hurdle"] = round(hurdle, 2)
        r["both_halves"] = bool(r["h1"] > 0 and r["h2"] > 0)


def main():
    t0 = time.time()
    with ThreadPoolExecutor(5) as ex:
        got = list(ex.map(A.load, [m for m in A.MARKETS if m[5]]))
    got += [A.load(m) for m in A.MARKETS if not m[5]]
    meta = {m[0]: m for m in A.MARKETS}
    data, cover = {}, {}
    for key, d, src, err in got:
        if d:
            ds = sorted(d)
            data[key] = (ds, np.array([d[x][3] for x in ds]))
            cover[key] = (src, ds[0], len(ds))
        else:
            cover[key] = err
    A.note("families · data", json.dumps(cover, ensure_ascii=False))
    fams = {"tom": ("Начало/конец месяца", TOM), "volbrk": ("Пробой волатильности", VB)}
    rows, tried = market_rows(data, meta, fams)
    # cross-market momentum (walk-forward over the lookback)
    xs = {}
    groups = {"noncrypto": ([k for k in data if meta[k][2] != "crypto"], 252, 0.0005, 3),
              "crypto": ([k for k in data if meta[k][2] == "crypto"], 365, 0.001, 2)}
    for g, (keys, ppy, cost, top) in groups.items():
        if len(keys) < 2 * top + 1:
            continue
        for lo in (False, True):
            ser, cal = {}, None
            for L in (63, 126, 252):
                for skip in (0, 21):
                    cal, ser[(L, skip)] = xs_portfolio(data, keys, ppy, cost, top, L, skip, lo)
                    tried += 1
            oos, start, chosen = A.walk_forward(ser, ppy)
            st = A.stats(oos, cal[start:], ppy)
            h1, h2 = halves(oos, ppy)
            name = f"xsmom·{g}·{'long' if lo else 'ls'}"
            xs[name] = {"markets": len(keys), "p_now": chosen[-1] if chosen else None, "sh": st["sharpe"], "cagr": st["cagr"],
                        "dd": st["dd"], "py": f"{st['pos_years']}/{st['n_years']}", "s3": st["sharpe3y"], "h1": h1, "h2": h2,
                        "from": st["from"], "_py": st["pos_years"] / max(1, st["n_years"]), "_ny": st["n_years"]}
            rows.append({"m": g, "cls": g, "f": "xsmom", "mode": "long" if lo else "ls", "p": chosen[-1] if chosen else None,
                         "sh": st["sharpe"], "cagr": st["cagr"], "dd": st["dd"], "py": xs[name]["py"], "_py": xs[name]["_py"],
                         "_ny": st["n_years"], "s3": st["sharpe3y"], "h1": h1, "h2": h2, "from": st["from"]})
        # yardstick: equal weight long of all markets (vol-weighted), same calendar
        C, common = closes(data, keys)
        rs = np.column_stack([strat_fresh(np.ascontiguousarray(C[:, j]), np.ones(len(common)), "long", cost, ppy) for j in range(len(keys))]).mean(axis=1)
        b = 3 * ppy
        st = A.stats(rs[b:], common[b:], ppy)
        xs[f"yardstick·{g}·equal long"] = {k: st[k] for k in ("sharpe", "cagr", "dd", "from")}
    N = len(rows) + NIGHTLY_RULES
    judge(rows, N)
    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    by_f = {}
    for r in rows:
        f = by_f.setdefault(f"{r['f']}·{r['mode']}", {"rows": 0, "passed": 0, "reliable": 0, "sh_med": []})
        f["rows"] += 1
        f["passed"] += r["passes"]
        f["reliable"] += r["reliable"]
        f["sh_med"].append(r["sh"])
    for f in by_f.values():
        f["sh_med"] = round(float(np.median(f["sh_med"])), 2)
    summary = {"tried_variants": tried, "rows": len(rows), "N_for_luck": N, "passed": sum(r["passes"] for r in rows),
               "reliable": sum(r["reliable"] for r in rows), "minutes": round((time.time() - t0) / 60, 1)}
    A.note("families · summary", json.dumps(summary))
    A.note("families · by family", json.dumps(by_f, ensure_ascii=False))
    fmt = lambda r: f"{r['m']} {r['f']}/{r['mode']} p={r['p']} Sh {r['sh']} ({r['h1']}/{r['h2']}) {r['cagr']}%/г dd {r['dd']} лет+ {r['py']} 3г {r['s3']} порог {r['hurdle']}{' НАДЁЖНО' if r['reliable'] else ''}"  # noqa: E731
    passed = sorted([r for r in rows if r["passes"]], key=lambda r: -r["sh"])
    A.note("families · passed", "\n".join(fmt(r) for r in passed))
    A.note("families · cross-market", json.dumps(xs, ensure_ascii=False))
    # parameter neighbourhood of the volatility breakout on crypto: every (k, H) on the whole history (not a test — a stability look)
    grid = {}
    for key, (dates, c) in data.items():
        if meta[key][2] != "crypto":
            continue
        for mode in ("long", "ls"):
            line = []
            for p in VB:
                A._W.clear()
                x = A.strat_returns(c, sig("volbrk", p, c, None, None, None, None), mode, A.COST["crypto"], 365)[200:]
                line.append(round(float(x.mean() / x.std() * math.sqrt(365)), 2) if x.std() else 0)
            grid[f"{key}·{mode}"] = line
    A.note("families · volbrk grid (k×H: 1.5/1,5,10 · 2/… · 2.5/…)", json.dumps(grid))
    # NULL: the same pipeline on shuffled daily returns (no calendar, no momentum by construction) → how many pass by luck
    rng = np.random.default_rng(7)
    null = []
    for rep in range(3):
        sh_data = {}
        for key, (dates, c) in data.items():
            r = c[1:] / c[:-1]
            sh_data[key] = (dates, np.r_[c[0], c[0] * np.cumprod(rng.permutation(r))])
        nr, _ = market_rows(sh_data, meta, fams)
        judge(nr, N)
        null.append({"passed": sum(x["passes"] for x in nr), "reliable": sum(x["reliable"] for x in nr), "rows": len(nr),
                     "crypto_volbrk_passed": sum(x["passes"] for x in nr if x["cls"] == "crypto" and x["f"] == "volbrk")})
    A.note("families · null (shuffled returns, 3 runs)", json.dumps(null))
    os.makedirs("out", exist_ok=True)
    with open("out/autolab_families.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "by_family": by_f, "rows": [clean(r) for r in rows], "xs": xs}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
