"""Daily trend-following portfolio across the research universe.

Signal (per coin, at 00:00 UTC on fully closed daily bars):
  LONG  if ret(lookback) > 0 and close ≥ max high of `breakout` days
  SHORT if ret(lookback) < 0 and close ≤ min low of `breakout` days  (optional)
Position sizing: vol-target — weight = target_vol / realised_vol(30d) capped at `max_w`,
split equally across active coins (no leverage above `max_gross`).
Exit: stop 2·ATR(14d) checked daily on low/high (conservative), else after hold_days.
Costs: taker fee + slippage each side. Returns daily portfolio PnL and yearly stats.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, resample


def daily(cs: list[Candle]) -> list[Candle]:
    bm = int(round((cs[1].open_time - cs[0].open_time) / 60_000))
    return resample(cs, 1440 // bm)


def run(universe: dict[str, list[Candle]], lookback: int = 28, breakout: int = 20, hold_days: int = 5,
        stop_atr: float = 2.0, side: str = "BOTH", target_vol: float = 0.30, max_w: float = 1.0,
        max_gross: float = 2.0, cost_side: float = 0.0007, btc_filter: bool = False,
        btc_symbol: str = "BTCUSDT") -> dict:
    D = {s: daily(c) for s, c in universe.items()}
    A = {s: arrays(d) for s, d in D.items()}
    ATR = {s: atr(a["h"], a["l"], a["c"], 14) for s, a in A.items()}
    days = sorted(set(int(t) for a in A.values() for t in a["t"]))
    idx = {s: {int(t): i for i, t in enumerate(a["t"])} for s, a in A.items()}
    open_pos: dict[str, dict] = {}
    pnl_by_day: dict[int, float] = defaultdict(float)
    trades = []
    for t in days:
        # 1. manage open positions on this day's bar
        for s, p in list(open_pos.items()):
            i = idx[s].get(t)
            if i is None:
                continue
            a = A[s]; sign = p["sign"]
            hit = (a["l"][i] <= p["stop"]) if sign == 1 else (a["h"][i] >= p["stop"])
            exit_px = None
            if hit:
                exit_px, kind = p["stop"], "STOP"
            elif i >= p["exit_i"]:
                exit_px, kind = a["c"][i], "TIME"
            if exit_px is not None:
                pnl_by_day[t] += (sign * (exit_px / p["mark"] - 1) - 2 * cost_side) * p["w"]
                r = sign * (exit_px / p["entry"] - 1) - 2 * cost_side
                trades.append({"sym": s, "time": p["t0"], "ret": r, "w": p["w"], "kind": kind, "sign": sign})
                del open_pos[s]
            else:
                pnl_by_day[t] += sign * (a["c"][i] / p["mark"] - 1) * p["w"]
                p["mark"] = a["c"][i]
        # 2. signals at the close of this day → entry at next day's open
        cands = []
        btc_ok_long = btc_ok_short = True
        if btc_filter and btc_symbol in A:
            ib = idx[btc_symbol].get(t); ab = A[btc_symbol]
            if ib is not None and ib >= 200:
                sma = ab["c"][ib - 200: ib].mean(); btc_ok_long = ab["c"][ib] > sma; btc_ok_short = ab["c"][ib] < sma
        for s, a in A.items():
            i = idx[s].get(t)
            if i is None or i < max(lookback, breakout, 31) or s in open_pos or i + 1 >= len(a["c"]):
                continue
            c = a["c"][i]
            ret = c / a["c"][i - lookback] - 1
            hh = a["h"][i - breakout: i].max(); ll = a["l"][i - breakout: i].min()
            rv = np.std(np.diff(np.log(a["c"][i - 30: i + 1]))) * math.sqrt(365)
            w = min(max_w, target_vol / rv) if rv > 0 else 0.0
            if side in ("LONG", "BOTH") and ret > 0 and c >= hh and btc_ok_long:
                cands.append((s, 1, w, i))
            elif side in ("SHORT", "BOTH") and ret < 0 and c <= ll and btc_ok_short:
                cands.append((s, -1, w, i))
        gross = sum(p["w"] for p in open_pos.values())
        for s, sign, w, i in cands:
            if gross + w > max_gross:
                w = max(0.0, max_gross - gross)
            if w <= 0:
                continue
            a = A[s]
            entry = a["o"][i + 1]
            open_pos[s] = {"sign": sign, "entry": entry, "mark": entry, "stop": entry - sign * stop_atr * ATR[s][i],
                           "exit_i": i + 1 + hold_days - 1, "w": w, "t0": int(a["t"][i + 1])}
            gross += w
            # first-day pnl from entry to that day's close is booked when that day is processed:
            # approximate by starting mark at entry (handled since pnl uses close-to-close from next day)
    # yearly stats
    eq = []; cum = 1.0
    by_year = defaultdict(list)
    for t in days:
        r = pnl_by_day.get(t, 0.0); cum *= (1 + r); eq.append(cum)
        by_year[datetime.fromtimestamp(t / 1000, tz=timezone.utc).year].append(r)
    eq = np.array(eq); dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    cum = cum - 1.0
    rets = np.array([pnl_by_day.get(t, 0.0) for t in days])
    sharpe = float(rets.mean() / rets.std() * math.sqrt(365)) if rets.std() > 0 else 0.0
    years = {y: {"ret_pct": round(100 * (float(np.prod([1 + x for x in v])) - 1), 1), "sharpe": round(float(np.mean(v) / np.std(v) * math.sqrt(365)), 2) if np.std(v) > 0 else 0}
             for y, v in sorted(by_year.items())}
    return {"total_ret_pct": round(100 * cum, 1), "sharpe": round(sharpe, 2), "max_dd_pct": round(100 * dd, 1),
            "trades": len(trades), "winrate": round(float(np.mean([x["ret"] > 0 for x in trades])), 3) if trades else None,
            "avg_trade_pct": round(100 * float(np.mean([x["ret"] for x in trades])), 3) if trades else None,
            "years": years}
