"""Trend-following LONG study on BTC/ETH perps (owner 04.11.2026: «биткоин летит, а мы стоим»).

Pre-declared, 4 rules × 2 coins = 8 variants, nothing tuned after the run:
  A) 4h close > EMA50 and EMA50 rising → long; exit on a 4h close < EMA50; stop −4 % from entry.
  B) 4h close above the 20-bar Donchian high (previous 20 bars) → long; exit when price touches the previous 10-bar low;
     stop 2 × ATR(20, 4h) from entry.
  C) 1h close > EMA100 (1h) and BTC 4h EMA50 rising (last closed 4h bar) → long; exit on a 1h close < EMA100; stop −3 %.
  D) A, entry only while the last closed daily close > SMA200 (daily).
Mechanics: signal at a bar close, entry at the next 1h open; stops are resting orders checked on 1h bars (gap → fill at open);
after a stop the rule needs a fresh signal (condition false, then true again). Costs 0.07 % per side; real funding: a long pays
the funding rate at each funding time it holds through. Account 1x, all-in per trade, compounding; drawdown mark-to-market on 1h closes.
Qualifies: PF > 1.3 in ≥ 3 of the 4 years (2023/24/25/26, by exit time) and max drawdown ≤ 25 % at 1x.
Usage: python scripts/trend_study.py trend_data.json [out.json]
"""
import datetime as dt
import json
import sys

import numpy as np

COST = 0.0007
YEARS = [2023, 2024, 2025, 2026]
T0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
H = 3_600_000


def ema(x, n):
    a, out = 2 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def load(path):
    d = json.load(open(path))
    data = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        k = np.array(d["kl"][s], dtype=float)
        t = k[:, 0].astype(np.int64)
        # regular hourly grid; missing hours forward-filled (flat bar) and counted
        g = np.arange(t[0], t[-1] + H, H)
        idx = np.searchsorted(t, g)
        have = (idx < len(t)) & (t[np.minimum(idx, len(t) - 1)] == g)
        O = np.full(len(g), np.nan); Hh = O.copy(); L = O.copy(); C = O.copy()
        O[have], Hh[have], L[have], C[have] = k[idx[have], 1], k[idx[have], 2], k[idx[have], 3], k[idx[have], 4]
        for i in range(len(g)):
            if np.isnan(C[i]):
                O[i] = Hh[i] = L[i] = C[i] = C[i - 1]
        f = np.array(d["fund"].get(s, []), dtype=float).reshape(-1, 2)
        data[s] = dict(t=g, o=O, h=Hh, l=L, c=C, missing=int((~have).sum()), fund_t=f[:, 0].astype(np.int64), fund_r=f[:, 1],
                       src=d.get("src", {}).get(s, {}))
    return data


def bars(D, hours):
    """Aggregate hourly bars into UTC-aligned `hours` bars. Returns (open_time, o, h, l, c, close_idx) where close_idx is the
    hourly index of the last hour in the bar (the bar is known at the close of that hour). Only complete bars."""
    t = D["t"]
    key = t // (hours * H)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(t)] - 1
    full = (ends - starts + 1) == hours
    starts, ends = starts[full], ends[full]
    o = D["o"][starts]
    h = np.array([D["h"][a:b + 1].max() for a, b in zip(starts, ends)])
    l = np.array([D["l"][a:b + 1].min() for a, b in zip(starts, ends)])
    c = D["c"][ends]
    return t[starts], o, h, l, c, ends


def to_hourly(vals, ends, n, fill=np.nan):
    """Value of a higher-timeframe series as known at the close of each hour (step function, no look-ahead)."""
    out = np.full(n, fill, dtype=float)
    for v, e in zip(vals, ends):
        out[e] = v
    # forward fill
    last = fill
    for i in range(n):
        if not np.isnan(out[i]):
            last = out[i]
        else:
            out[i] = last
    return out


def signals(rule, D, B):
    """Per-hour arrays (decided at the close of hour i): enter[i], exit_close[i] (bool), plus stop config."""
    n = len(D["t"])
    t4, o4, h4, l4, c4, e4 = bars(D, 4)
    E50 = ema(c4, 50)
    up4 = np.r_[False, E50[1:] > E50[:-1]]
    cond4 = (c4 > E50) & up4
    enter = np.zeros(n, bool); xclose = np.zeros(n, bool); xlevel = np.full(n, np.nan); stop_dist = np.full(n, np.nan)
    if rule in ("A", "D"):
        ok = cond4.copy()
        if rule == "D":
            td, od, hd, ld, cd, ed = bars(D, 24)
            sma = np.convolve(cd, np.ones(200) / 200, mode="full")[: len(cd)]
            sma[:199] = np.nan
            day_ok = to_hourly(np.where(cd > sma, 1.0, 0.0) * np.where(np.isnan(sma), np.nan, 1.0), ed, n)
            ok = ok & (day_ok[e4] == 1.0)
        for v, e in zip(ok, e4): enter[e] = v
        for v, e in zip(c4 < E50, e4): xclose[e] = v
        stop_dist[:] = 0.04
        mode = "pct"
    elif rule == "B":
        prevhi = np.r_[[np.nan] * 20, [h4[i - 20:i].max() for i in range(20, len(h4))]]
        tr = np.maximum(h4 - l4, np.maximum(abs(h4 - np.r_[c4[0], c4[:-1]]), abs(l4 - np.r_[c4[0], c4[:-1]])))
        atr = np.r_[[np.nan] * 19, np.convolve(tr, np.ones(20) / 20, mode="valid")]
        for i, e in enumerate(e4):
            enter[e] = c4[i] > prevhi[i] if not np.isnan(prevhi[i]) else False
            stop_dist[e] = 2 * atr[i] / c4[i] if not np.isnan(atr[i]) else np.nan
        # exit level: the low of the last 10 completed 4h bars, a resting order from the hour after that bar closes
        lo10 = np.r_[[np.nan] * 9, [l4[i - 9:i + 1].min() for i in range(9, len(l4))]]
        xlevel = to_hourly(lo10, e4, n)
        stop_dist = to_hourly(stop_dist[e4], e4, n)
        mode = "atr"
    elif rule == "C":
        c = D["c"]
        E100 = ema(c, 100)
        bt4, bo4, bh4, bl4, bc4, be4 = bars(B, 4)
        bE = ema(bc4, 50)
        bup = to_hourly(np.r_[0.0, (bE[1:] > bE[:-1]).astype(float)], be4, len(B["t"]))
        # align BTC regime to this coin's hourly grid by time
        pos = np.searchsorted(B["t"], D["t"], side="right") - 1
        reg = np.where(pos >= 0, bup[np.maximum(pos, 0)], 0.0) == 1.0
        enter = (c > E100) & reg
        xclose = c < E100
        stop_dist[:] = 0.03
        mode = "pct"
    return enter, xclose, xlevel, stop_dist


def simulate(rule, D, B):
    t, o, h, l, c = D["t"], D["o"], D["h"], D["l"], D["c"]
    n = len(t)
    enter, xclose, xlevel, sdist = signals(rule, D, B)
    ft, fr = D["fund_t"], D["fund_r"]
    trades, eq_curve = [], np.ones(n)
    eq, pos, armed = 1.0, None, True
    start = int(np.searchsorted(t, T0))
    for i in range(start, n):
        # 1) a position open at hour i: stops/levels during the hour
        if pos is not None:
            stop = pos["stop"]
            lvl = xlevel[i - 1] if rule == "B" and i > 0 and not np.isnan(xlevel[i - 1]) else -np.inf
            trig = max(stop, lvl)
            if l[i] <= trig:
                px = min(o[i], trig)
                pos.update(exit_i=i, exit_px=px, why="стоп" if trig == stop else "минимум 10 баров")
            elif rule != "B" and xclose[i]:
                if i + 1 < n:
                    pos.update(exit_i=i + 1, exit_px=o[i + 1], why="закрытие ниже EMA")
            if "exit_i" in pos:
                fsum = fr[(ft > t[pos["i"]]) & (ft <= t[pos["exit_i"]])].sum() if len(ft) else 0.0
                r = pos["exit_px"] / pos["px"] * (1 - COST) * (1 - COST) - 1 - fsum
                eq = pos["eq0"] * (1 + r)
                trades.append(dict(entry=int(t[pos["i"]]), exit=int(t[pos["exit_i"]]), ret=r, fund=fsum, why=pos["why"],
                                   bars=int(pos["exit_i"] - pos["i"])))
                if pos["why"] == "стоп":
                    armed = False
                done_i, mark = pos["exit_i"], pos["eq0"] * (c[i] / pos["px"]) * (1 - COST)
                pos = None
                if done_i > i:      # exit fills at the next open: this hour is still held, mark it at the close
                    eq_curve[i] = mark
                    continue
        if pos is not None:
            eq_curve[i] = pos["eq0"] * (c[i] / pos["px"]) * (1 - COST)
        else:
            eq_curve[i] = eq
        # 2) decide at the close of hour i
        if pos is None:
            if not enter[i]:
                armed = True
            elif armed and i + 1 < n and not np.isnan(sdist[i]):
                px = o[i + 1]
                pos = dict(i=i + 1, px=px, stop=px * (1 - sdist[i]), eq0=eq)
    if pos is not None:   # still open: mark to the last close
        fsum = fr[(ft > t[pos["i"]])].sum() if len(ft) else 0.0
        r = c[-1] / pos["px"] * (1 - COST) * (1 - COST) - 1 - fsum
        trades.append(dict(entry=int(t[pos["i"]]), exit=None, ret=r, fund=fsum, why="открыта", bars=int(n - 1 - pos["i"])))
    eq_curve[:start] = 1.0
    return trades, eq_curve, start


def yr(ms): return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).year


def pf(rs):
    g, b = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    return round(g / b, 2) if b > 0 else (99.0 if g > 0 else 0.0)


def maxdd(curve):
    peak = np.maximum.accumulate(curve)
    return float((curve / peak - 1).min())


def summarize(rule, s, D, B):
    T, curve, st = simulate(rule, D, B)
    t, c = D["t"], D["c"]
    closed = [x for x in T if x["exit"] is not None]
    rs = [x["ret"] for x in closed]
    out = dict(rule=rule, coin=s, n=len(closed), win=round(100 * np.mean([r > 0 for r in rs]), 0) if rs else 0, pf=pf(rs),
               mean=round(100 * np.mean(rs), 2) if rs else 0, dd=round(100 * maxdd(curve[st:]), 1),
               total=round(100 * (curve[-1] - 1), 1), fund_paid=round(100 * sum(x["fund"] for x in T), 1),
               exposure=round(100 * sum(x["bars"] for x in T) / (len(t) - st), 0))
    by = {}
    for y in YEARS:
        a = int(np.searchsorted(t, int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)))
        b = int(np.searchsorted(t, int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000))) - 1
        b = min(b, len(t) - 1)
        e0 = curve[a - 1] if a > st else 1.0
        ry = [x["ret"] for x in closed if yr(x["exit"]) == y]
        bh = c[b] / (c[a - 1] if a > 0 else D["o"][a]) - 1
        by[y] = dict(ret=round(100 * (curve[b] / e0 - 1), 1), pf=pf(ry), n=len(ry), bh=round(100 * bh, 1))
    out["years"] = by
    out["pf_years_ok"] = sum(1 for y in YEARS if by[y]["pf"] > 1.3)
    out["qualifies"] = out["pf_years_ok"] >= 3 and out["dd"] >= -25
    bh_curve = c[st:] / c[st - 1]
    out["bh_total"] = round(100 * (bh_curve[-1] - 1), 1)
    out["bh_dd"] = round(100 * maxdd(bh_curve), 1)
    # last 14 days and today
    now = t[-1]
    w0 = now - 14 * 24 * H
    a = int(np.searchsorted(t, w0))
    out["d14"] = dict(acct=round(100 * (curve[-1] / curve[a - 1] - 1), 1), bh=round(100 * (c[-1] / c[a - 1] - 1), 1),
                      trades=[dict(entry=dt.datetime.fromtimestamp(x["entry"] / 1000, dt.timezone.utc).strftime("%d.%m %H:%M"),
                                   exit=dt.datetime.fromtimestamp(x["exit"] / 1000, dt.timezone.utc).strftime("%d.%m %H:%M") if x["exit"] else "открыта",
                                   ret=round(100 * x["ret"], 2), why=x["why"]) for x in T if (x["exit"] or now) >= w0])
    d0 = int(np.searchsorted(t, (now // (24 * H)) * 24 * H))
    out["today"] = dict(acct=round(100 * (curve[-1] / curve[d0 - 1] - 1), 2), bh=round(100 * (c[-1] / c[d0 - 1] - 1), 2),
                        in_long_now=bool(T and T[-1]["exit"] is None),
                        last_bar=dt.datetime.fromtimestamp((now + H) / 1000, dt.timezone.utc).strftime("%d.%m %H:%M UTC"))
    return out


def main():
    D = load(sys.argv[1])
    B = D["BTCUSDT"]
    res = [summarize(r, s, D[s], B) for r in "ABCD" for s in ("BTCUSDT", "ETHUSDT")]
    meta = {s: dict(missing_hours=D[s]["missing"], funding_points=len(D[s]["fund_t"]), src=D[s]["src"],
                    last=dt.datetime.fromtimestamp((D[s]["t"][-1] + H) / 1000, dt.timezone.utc).isoformat()) for s in D}
    json.dump(dict(meta=meta, res=res), open(sys.argv[2] if len(sys.argv) > 2 else "trend_results.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))
    for x in res:
        y = x["years"]
        print(f"{x['rule']} {x['coin'][:3]} n={x['n']} win={x['win']} PF={x['pf']} mean={x['mean']} DD={x['dd']} tot={x['total']} "
              f"BH={x['bh_total']}/{x['bh_dd']} fund={x['fund_paid']} exp={x['exposure']}% | "
              + " ".join(f"{k}:{v['ret']}/{v['pf']}/{v['bh']}" for k, v in y.items())
              + f" | ok={x['pf_years_ok']} Q={x['qualifies']} | 14d {x['d14']['acct']} vs {x['d14']['bh']} | today {x['today']}")


if __name__ == "__main__":
    main()
