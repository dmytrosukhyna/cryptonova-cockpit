"""Backlog item 207 (owner, revision 18.11): the spot core BTC / ETH / SOL in the BEAR regime (BTC below its 200-day average).

Question: is holding the core as now (plan 30 / 10 / 5, always invested) better or worse than parking part of the core in USDT
(at 0 % or a fixed 4 % a year) while BTC's previous daily close is below its 200-day average?

Pre-registered variants (no tuning — every variant tried is printed):
  hold      — core always invested, plan proportions, rebalanced to the plan every Monday
  half      — BTC < SMA200 (yesterday's close vs its 200-day average) → 50 % of the core in USDT
  all       — BTC < SMA200 → 100 % in USDT
  half3     — like half, but the regime switches only after 3 closes in a row on the new side (fewer whipsaws)
each at USDT 0 % and 4 % a year → 7 variants against hold. Costs: 0.15 % of every traded notional (fee 0.1 % + slippage 0.05 %).
Halves: 2021-07 → 2023-12 and 2024-01 → the end of the data. Statistic: t of the mean daily LOG return difference (variant − hold),
and the fraction of the hold return lost in the 90 days after each BEAR→BULL flip (missed rally).
Not financial advice; history only.

Usage: python scripts/spot_core_bear_test.py spot1d.npz [--md out.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math

import numpy as np

PLAN = {"BTC": 0.30, "ETH": 0.10, "SOL": 0.05}
COST = 0.0015
SPLIT = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000


def series(path: str):
    z = np.load(path, allow_pickle=True)
    syms = [str(s) for s in z["syms"]]
    cl = {s: z["close"][syms.index(s)].astype(float) for s in PLAN}
    return z["days"].astype(np.int64), cl


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    c = np.cumsum(np.nan_to_num(x))
    out[n - 1:] = (c[n - 1:] - np.concatenate([[0], c[:-n]])) / n
    return out


def run(days, cl, mode: str, usdt_apy: float):
    btc = cl["BTC"]
    s200 = sma(btc, 200)
    bear_raw = btc < s200                         # known at the close of day i → acts on day i+1
    if mode.endswith("3"):
        bear = np.zeros_like(bear_raw)
        st = bool(bear_raw[199])
        for i in range(199, len(btc)):
            if i >= 201 and all(bear_raw[i - k] != st for k in range(3)):
                st = not st
            bear[i] = st
    else:
        bear = bear_raw
    cash_share = {"hold": 0.0, "half": 0.5, "all": 1.0, "half3": 0.5}[mode]
    w_plan = np.array([PLAN[s] for s in PLAN]) / sum(PLAN.values())
    px = np.vstack([cl[s] for s in PLAN])
    start = 200
    prev_tc = 0.0
    v, w, cash = 1.0, None, 0.0                  # w = coin values (fraction of portfolio units)
    rets, flags = [], []
    for i in range(start, len(days)):
        if np.isnan(px[:, i]).any() or np.isnan(px[:, i - 1]).any():
            rets.append(0.0)
            flags.append(False)
            continue
        target_cash = cash_share if bear[i - 1] else 0.0
        monday = dt.datetime.fromtimestamp(days[i] / 1000, dt.timezone.utc).weekday() == 0
        if w is None:
            w = w_plan * (1 - target_cash) * v
            cash = target_cash * v
            v -= COST * w.sum()
            w *= v / (w.sum() + cash) if (w.sum() + cash) else 1
            cash = v - w.sum()
        elif monday or target_cash != prev_tc:
            new_w = w_plan * (1 - target_cash) * v
            traded = np.abs(new_w - w).sum()
            v -= COST * traded
            w = w_plan * (1 - target_cash) * v
            cash = target_cash * v
        prev_tc = target_cash
        v0 = v
        w = w * px[:, i] / px[:, i - 1]
        cash *= (1 + usdt_apy) ** (1 / 365)
        v = w.sum() + cash
        rets.append(v / v0 - 1)
        flags.append(bool(bear[i - 1]))
    return np.array(rets), np.array(flags), bear


def stats(r: np.ndarray):
    eq = np.cumprod(1 + r)
    yrs = len(r) / 365
    cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    sh = r.mean() / r.std() * math.sqrt(365) if r.std() > 0 else 0
    return eq[-1] - 1, cagr, dd, sh


def tstat(d: np.ndarray) -> float:
    return d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 2 and d.std() > 0 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--md")
    a = ap.parse_args()
    days, cl = series(a.npz)
    d = days[200:]
    h1 = d < SPLIT
    base, _, bear = run(days, cl, "hold", 0.0)
    lines = [f"Данные: Binance spot 1d {dt.datetime.fromtimestamp(d[0]/1000, dt.timezone.utc):%Y-%m-%d} → "
             f"{dt.datetime.fromtimestamp(d[-1]/1000, dt.timezone.utc):%Y-%m-%d}, {len(d)} дней; BTC ниже SMA200 "
             f"{np.mean(bear[200:]) * 100:.0f}% дней; издержки 0.15% оборота; 7 вариантов против «держать».", "",
             "| вариант | весь период | CAGR | макс. просадка | Шарп | 1-я половина (21.07–23.12) | 2-я (24.01–) | t разницы 1/2 | упущено после разворотов |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    # BEAR→BULL flips (raw regime) for the missed-rally measure
    br = bear[199:]
    flips = [i for i in range(1, len(br)) if br[i - 1] and not br[i]]
    for mode in ("hold", "half", "all", "half3"):
        for apy in ((0.0,) if mode == "hold" else (0.0, 0.04)):
            r, fl, _ = run(days, cl, mode, apy)
            tot, cagr, dd, sh = stats(r)
            p1, p2 = stats(r[h1])[0], stats(r[~h1])[0]
            diff = np.log1p(r) - np.log1p(base)          # log: compounding, not the arithmetic mean
            t1, t2 = tstat(diff[h1]), tstat(diff[~h1])
            miss = []
            for f in flips:
                seg = slice(f, min(f + 90, len(r)))
                hb, hv = np.prod(1 + base[seg]) - 1, np.prod(1 + r[seg]) - 1
                miss.append(hb - hv)
            ms = f"{np.mean(miss) * 100:+.1f} п.п. ({len(flips)} разв.)" if mode != "hold" else "—"
            name = mode + ("" if mode == "hold" else f" · USDT {apy * 100:.0f}%")
            ts = "—" if mode == "hold" else f"{t1:+.2f} / {t2:+.2f}"
            lines.append(f"| {name} | {tot * 100:+.0f}% | {cagr * 100:+.1f}% | {dd * 100:.0f}% | {sh:.2f} | {p1 * 100:+.0f}% | "
                         f"{p2 * 100:+.0f}% | {ts} | {ms} |")
    out = "\n".join(lines)
    print(out)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(out + "\n")


if __name__ == "__main__":
    main()
