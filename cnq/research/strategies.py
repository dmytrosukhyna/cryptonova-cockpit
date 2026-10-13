"""Literature-backed strategy tests on 1m data. Each strategy emits Trades with an
explicit exit rule (stop, optional target, time limit). `simulate` walks 1m candles
from the fill forward — the only intrabar assumption left is stop-before-target
inside ONE minute, which is immaterial.

Costs are explicit: taker/maker fees + slippage per side, as fractions of price.
Results are reported both in R (per unit of initial stop risk) and in % of notional,
because time-exit strategies are not naturally R-based.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, resample


@dataclass
class Trade:
    fill_index: int          # index in the 1m series where the fill happens (at open unless limit)
    direction: str
    entry: float
    stop: float
    target: float | None = None
    time_limit: int | None = None     # max minutes in trade
    entry_is_limit: bool = False
    tag: str = ""


@dataclass
class Costs:
    taker: float = 0.0005
    maker: float = 0.0002
    slippage: float = 0.0002


def simulate(m1: list[Candle], trades: list[Trade], costs: Costs) -> list[dict]:
    h = np.array([c.high for c in m1]); l = np.array([c.low for c in m1]); o = np.array([c.open for c in m1])
    out = []
    n = len(m1)
    for t in trades:
        if t.fill_index >= n:
            continue
        sign = 1 if t.direction == "LONG" else -1
        px = t.entry if t.entry_is_limit else o[t.fill_index] * (1 + sign * costs.slippage)
        fee_in = costs.maker if t.entry_is_limit else costs.taker
        r = abs(px - t.stop)
        if r <= 0:
            continue
        bm = bar_minutes(m1)
        end = n if t.time_limit is None else min(n, t.fill_index + max(1, t.time_limit // bm))
        exit_px, kind, j_exit = None, "TIME", end - 1
        for j in range(t.fill_index, end):
            if (l[j] <= t.stop) if sign == 1 else (h[j] >= t.stop):
                exit_px, kind, j_exit = t.stop * (1 - sign * costs.slippage), "STOP", j
                break
            if t.target is not None and ((h[j] >= t.target) if sign == 1 else (l[j] <= t.target)):
                exit_px, kind, j_exit = t.target, "TARGET", j
                break
        if exit_px is None:
            exit_px = m1[end - 1].close * (1 - sign * costs.slippage)
        fee_out = costs.maker if kind == "TARGET" else costs.taker
        gross = sign * (exit_px - px) / px
        net = gross - fee_in - fee_out
        out.append({"tag": t.tag, "dir": t.direction, "fill": t.fill_index, "exit": j_exit, "kind": kind,
                    "gross_pct": gross * 100, "net_pct": net * 100, "net_r": net * px / r,
                    "minutes": (j_exit - t.fill_index + 1) * bm, "time": m1[t.fill_index].open_time})
    return out


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    net = np.array([r["net_pct"] for r in rows]); rr = np.array([r["net_r"] for r in rows])
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    days = max(1.0, (rows[-1]["time"] - rows[0]["time"]) / 86_400_000)
    daily = net.sum() / days
    sharpe = float(net.mean() / net.std() * np.sqrt(len(net) / (days / 365))) if net.std() > 0 and days > 0 else 0.0
    return {"n": len(rows), "net_pct_sum": round(float(net.sum()), 2), "net_pct_mean": round(float(net.mean()), 4),
            "net_r_mean": round(float(rr.mean()), 3), "winrate": round(float((net > 0).mean()), 3),
            "max_dd_pct": round(dd, 2), "sharpe_ann": round(sharpe, 2),
            "pf": round(float(net[net > 0].sum() / -net[net < 0].sum()), 2) if (net < 0).any() else float("inf"),
            "stops": int(sum(r["kind"] == "STOP" for r in rows)), "targets": int(sum(r["kind"] == "TARGET" for r in rows))}


def split_rows(rows: list[dict], cut_time: int) -> tuple[list[dict], list[dict]]:
    return [r for r in rows if r["time"] < cut_time], [r for r in rows if r["time"] >= cut_time]


def random_trades(m1: list[Candle], like: list[Trade], seed: int = 0, reps: int = 5) -> list[Trade]:
    rng = np.random.default_rng(seed)
    o = np.array([c.open for c in m1])
    out = []
    for _ in range(reps):
        for t in like:
            j = int(rng.integers(200, len(m1) - 500))
            sign = 1 if t.direction == "LONG" else -1
            dist = abs(t.entry - t.stop) / t.entry
            tgt = None if t.target is None else o[j] * (1 + sign * abs(t.target - t.entry) / t.entry)
            out.append(Trade(j, t.direction, o[j], o[j] * (1 - sign * dist), tgt, t.time_limit, False, "rnd"))
    return out


# ------------------------------------------------------------------ helpers
def utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def bar_minutes(cs: list[Candle]) -> int:
    return max(1, int(round((cs[1].open_time - cs[0].open_time) / 60_000))) if len(cs) > 1 else 1


def daily_series(m1: list[Candle]) -> tuple[list[Candle], np.ndarray]:
    d = resample(m1, 1440 // bar_minutes(m1))
    return d, np.array([c.open_time for c in d])


def minute_index_at(m1_times: np.ndarray, t_ms: int) -> int:
    return int(np.searchsorted(m1_times, t_ms, side="left"))


# ------------------------------------------------------------------ strategies
def s1_daily_trend(m1: list[Candle], lookback_ret: int = 28, breakout: int = 20, hold_days: int = 5,
                   stop_atr: float = 2.0, side: str = "LONG") -> list[Trade]:
    """Idea 1: at 00:00 UTC, if r(lookback)>0 and close >= highest high of `breakout` days
    (mirror for SHORT) → market at the next 1m open; stop = 2·ATR(14d); exit after hold_days."""
    d, dt = daily_series(m1)
    a = arrays(d); atr_d = atr(a["h"], a["l"], a["c"], 14)
    m1_t = np.array([c.open_time for c in m1])
    out, busy_until = [], -1
    for k in range(max(lookback_ret, breakout) + 1, len(d)):
        if k <= busy_until:
            continue
        c = a["c"][k - 1]  # last fully closed day
        r = c / a["c"][k - 1 - lookback_ret] - 1
        hh = a["h"][k - 1 - breakout: k - 1].max(); ll = a["l"][k - 1 - breakout: k - 1].min()
        j = minute_index_at(m1_t, int(dt[k]))
        if j >= len(m1):
            break
        if side in ("LONG", "BOTH") and r > 0 and c >= hh:
            out.append(Trade(j, "LONG", m1[j].open, m1[j].open - stop_atr * atr_d[k - 1], None, hold_days * 1440, False, "trend_L"))
            busy_until = k + hold_days - 1
        elif side in ("SHORT", "BOTH") and r < 0 and c <= ll:
            out.append(Trade(j, "SHORT", m1[j].open, m1[j].open + stop_atr * atr_d[k - 1], None, hold_days * 1440, False, "trend_S"))
            busy_until = k + hold_days - 1
    return out


def s2_session_window(m1: list[Candle], start_h: int = 21, end_h: int = 23, hv_filter: bool = True,
                      sma_filter: bool = True, stop_atr_1h: float = 0.7) -> list[Trade]:
    """Idea 2A: long at start_h:00 UTC, exit at end_h:00 UTC. Filters: 30d realised vol above
    its 1y median; close above 20d SMA. Stop = 0.7·ATR(1h,24)."""
    d, dt = daily_series(m1)
    a = arrays(d)
    closes = a["c"]
    rets = np.diff(np.log(closes))
    h1 = resample(m1, 60); ah = arrays(h1); atr_h = atr(ah["h"], ah["l"], ah["c"], 24); h1_t = ah["t"]
    m1_t = np.array([c.open_time for c in m1])
    out = []
    for k in range(40, len(d)):
        hv30 = rets[k - 31: k - 1].std()
        hv_hist = [rets[i - 30: i].std() for i in range(31, k, 5)]
        if hv_filter and (not hv_hist or hv30 < np.median(hv_hist)):
            continue
        if sma_filter and closes[k - 1] < closes[k - 21: k - 1].mean():
            continue
        t0 = int(dt[k]) + start_h * 3_600_000
        j = minute_index_at(m1_t, t0)
        if j >= len(m1) or m1[j].open_time != t0:
            continue
        hi = max(0, int(np.searchsorted(h1_t, t0, side="right")) - 1)
        out.append(Trade(j, "LONG", m1[j].open, m1[j].open - stop_atr_1h * atr_h[hi], None, (end_h - start_h) * 60, False, "session"))
    return out


def s4_intraday_tsmom(m1: list[Candle], vol_filter: bool = True) -> list[Trade]:
    """Idea 4 (Shen–Urquhart–Wang): US session 13:30–21:00 UTC. ONFH = return 13:30→14:00.
    At 20:30 take the sign of ONFH (flip if 20:00→20:30 went against it); exit 21:00.
    Stop = opposite edge of the 13:30–14:00 range. Filter: first-30-min volume above 30d median."""
    m1_t = np.array([c.open_time for c in m1])
    out, vol_hist = [], []
    day0 = m1[0].open_time // 86_400_000
    for day in range(day0 + 1, m1[-1].open_time // 86_400_000):
        base = day * 86_400_000
        j0 = minute_index_at(m1_t, base + 13 * 3_600_000 + 30 * 60_000)
        j1 = j0 + 30; j2 = minute_index_at(m1_t, base + 20 * 3_600_000); j3 = j2 + 30
        if j3 >= len(m1) or m1[j0].open_time != base + 13 * 3_600_000 + 30 * 60_000:
            continue
        if utc(base).weekday() >= 5:
            continue
        first = m1[j0:j1]; vol30 = sum(c.volume for c in first)
        onfh = first[-1].close / first[0].open - 1
        ok = (not vol_filter) or (len(vol_hist) >= 10 and vol30 > np.median(vol_hist[-30:]))
        vol_hist.append(vol30)
        if not ok or onfh == 0:
            continue
        slh = m1[j3 - 1].close / m1[j2].open - 1
        sign = 1 if onfh > 0 else -1
        if slh * sign < 0:
            sign = -sign
        rng_hi = max(c.high for c in first); rng_lo = min(c.low for c in first)
        entry = m1[j3].open
        stop = rng_lo if sign == 1 else rng_hi
        if (sign == 1 and stop >= entry) or (sign == -1 and stop <= entry):
            stop = entry * (1 - sign * 0.004)
        out.append(Trade(j3, "LONG" if sign == 1 else "SHORT", entry, stop, None, 30, False, "itsmom"))
    return out


def s5_vwap_reversion(m1: list[Candle], z_min: float = 2.5, vol_mult: float = 2.0, hold_bars: int = 8,
                      near_level_only: bool = False, level_atr: float = 0.3) -> list[Trade]:
    """Idea 5: on 15m bars, z = (close − session VWAP)/σ(15m,96); fade when |z|≥z_min and bar
    volume ≥ vol_mult × mean(96). Target = VWAP, stop = bar extreme ± 0.5·ATR(15m), hold ≤ 8 bars.
    Optionally only when within level_atr·ATR of prior-day high/low or a $1000 round number."""
    b15 = resample(m1, 15); a = arrays(b15); atr15 = atr(a["h"], a["l"], a["c"], 14)
    m1_t = np.array([c.open_time for c in m1])
    day = a["t"] // 86_400_000
    out = []
    vwap_num = vwap_den = 0.0; cur_day = None
    pdh = pdl = None; day_hi = day_lo = None
    rets = np.diff(a["c"]) / a["c"][:-1]
    for i in range(100, len(b15) - 1):
        if day[i] != cur_day:
            if day_hi is not None:
                pdh, pdl = day_hi, day_lo
            cur_day, vwap_num, vwap_den, day_hi, day_lo = day[i], 0.0, 0.0, a["h"][i], a["l"][i]
        tp = (a["h"][i] + a["l"][i] + a["c"][i]) / 3
        vwap_num += tp * a["v"][i]; vwap_den += a["v"][i]
        day_hi, day_lo = max(day_hi, a["h"][i]), min(day_lo, a["l"][i])
        if vwap_den == 0:
            continue
        vwap = vwap_num / vwap_den
        sigma = a["c"][i] * rets[i - 96: i].std() if i >= 97 else None   # one-bar σ, as in the 1h-reversal study
        if not sigma:
            continue
        z = (a["c"][i] - vwap) / sigma
        if abs(z) < z_min or a["v"][i] < vol_mult * a["v"][i - 96: i].mean():
            continue
        if near_level_only:
            px = a["c"][i]; tol = level_atr * atr15[i]
            rn = round(px / 1000) * 1000
            if pdh is None or not (abs(px - pdh) <= tol or abs(px - pdl) <= tol or abs(px - rn) <= tol):
                continue
        j = minute_index_at(m1_t, int(a["t"][i + 1]))
        if j >= len(m1):
            break
        if z > 0:
            out.append(Trade(j, "SHORT", m1[j].open, a["h"][i] + 0.5 * atr15[i], vwap, hold_bars * 15, False, "vwap"))
        else:
            out.append(Trade(j, "LONG", m1[j].open, a["l"][i] - 0.5 * atr15[i], vwap, hold_bars * 15, False, "vwap"))
    return out


def report(name: str, m1: list[Candle], trades: list[Trade], costs: Costs, cut_time: int) -> None:
    rows = simulate(m1, trades, costs)
    rnd = simulate(m1, random_trades(m1, trades), costs)
    ins, oos = split_rows(rows, cut_time)
    rins, roos = split_rows(rnd, cut_time)
    print(f"== {name}  (n={len(rows)})")
    for lab, r, rr in (("IS", ins, rins), ("OOS", oos, roos)):
        s, sr = stats(r), stats(rr)
        if s["n"] == 0:
            print(f"  {lab:3} no trades"); continue
        print(f"  {lab:3} n={s['n']:4d} net={s['net_pct_sum']:+7.2f}%  mean={s['net_pct_mean']:+.3f}%/trade  "
              f"WR={s['winrate']:.2f} PF={s['pf']} Sharpe={s['sharpe_ann']} maxDD={s['max_dd_pct']}%  "
              f"| random mean={sr.get('net_pct_mean', 0):+.3f}%  → edge {s['net_pct_mean'] - sr.get('net_pct_mean', 0):+.3f}%/trade")
