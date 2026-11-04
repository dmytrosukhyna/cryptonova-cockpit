"""Backlog #63 (04.11.2026): does the funding carry of #52 duplicate «Нейтральная корзина» (lsn)?

One check, fixed BEFORE the run, no weight tuning:
  A = lsn book as backtested (scripts/own_signals_study.py ls_neutral, LS10, inverse-vol, gross 0.75), DAILY marks 2022 → .
  B = #52 carry (scripts/p52_study.py: weekly terciles by 7-day funding, gross 1, costs 0.07 %), daily returns.
  On the common window: correlation of daily returns (lags −1/0/+1 shown to catch a date offset), of weekly and of monthly
  sums; Sharpe of A, B and of a 50/50 mix with B scaled to A's daily volatility; the mix then scaled so its worst drawdown
  equals A's, and its yearly growth compared with A's — in both halves of the window.
  «Adds something» (all must hold): daily corr (lag 0) < 0.30; mix Sharpe > A Sharpe on the full window AND in each half;
  mix CAGR at A's drawdown > A CAGR. Variants: 1 (luck hurdle not needed for a correlation; reported family N 532).
Public data only (data.binance.vision, DeFiLlama for the quality card), no keys, no orders.
"""
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402
import p52_study as p52  # noqa: E402
import own_signals_study as OS  # noqa: E402

DAYMS = 86_400_000


def sh(p):
    return round(float(p.mean() / p.std() * math.sqrt(365)), 2) if len(p) > 2 and p.std() > 0 else 0.0


def dd(p):
    eq = np.cumprod(1 + p)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def cagr(p):
    eq = np.prod(1 + p)
    return float(eq ** (365 / len(p)) - 1) if eq > 0 else -1.0


def scale_to_dd(p, target):
    lo, hi = 0.05, 5.0
    for _ in range(50):
        k = (lo + hi) / 2
        if dd(k * p) < target:
            hi = k
        else:
            lo = k
    return lo


def corr(a, b):
    return round(float(np.corrcoef(a, b)[0, 1]), 3) if len(a) > 3 else None


def sums(days, p, width):
    keys = (days - days[0]) // width
    out = np.zeros(int(keys[-1]) + 1)
    np.add.at(out, keys, p)
    return out


def main():
    t0 = time.time()
    # A: lsn daily equity
    box = {"daily": []}
    OS.ls_neutral(iv=True, ret_out=box)
    pts = sorted(box["daily"])
    ad = np.array([t // DAYMS for t, _v in pts], dtype=np.int64)
    av = np.array([v for _t, v in pts])
    ar = np.r_[np.nan, av[1:] / av[:-1] - 1]
    A = {int(d): r for d, r in zip(ad[1:], ar[1:])}
    vb.note("p63 · lsn", f"{len(A)} daily points {ad[0]}…{ad[-1]}; {time.time() - t0:.0f}s")
    # B: carry daily returns
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    d0 = min(int(x["d"][0]) for x in data)
    span = np.arange(d0, live_end + 1)
    R, F, Q, H, FR = p52.grid(data, d0, live_end)
    pr, *_ = p52.run(R, F, Q, H, FR)
    B = {int(d): float(r) for d, r in zip(span, pr)}
    first_b = int(span[np.flatnonzero(pr != 0)[0]])
    common = np.array(sorted(d for d in A if d in B and d >= first_b), dtype=np.int64)
    a = np.array([A[d] for d in common]); b = np.array([B[d] for d in common])
    lag = {L: corr(a[max(0, L):len(a) + min(0, L)], b[max(0, -L):len(b) - max(0, L)]) for L in (-1, 0, 1)}
    cw = corr(sums(common, a, 7), sums(common, b, 7))
    cm = corr(sums(common, a, 30), sums(common, b, 30))
    k = a.std() / b.std() if b.std() > 0 else 0.0
    mix = 0.5 * a + 0.5 * k * b
    s_mix = scale_to_dd(mix, dd(a))
    h = len(a) // 2
    halves = [(sh(a[:h]), sh(b[:h]), sh(mix[:h])), (sh(a[h:]), sh(b[h:]), sh(mix[h:]))]
    ok = (lag[0] is not None and lag[0] < 0.30 and sh(mix) > sh(a) and all(m > x for x, _y, m in halves)
          and cagr(s_mix * mix) > cagr(a))
    yrs = {}
    for y in range(2022, 2027):
        lo = (np.datetime64(f"{y}-01-01").astype("datetime64[D]").astype(np.int64))
        hi = (np.datetime64(f"{y + 1}-01-01").astype("datetime64[D]").astype(np.int64))
        m = (common >= lo) & (common < hi)
        if m.sum() > 20:
            yrs[y] = [round((np.prod(1 + a[m]) - 1) * 100), round((np.prod(1 + b[m]) - 1) * 100),
                      corr(a[m], b[m])]
    vb.note("p63 · result", f"{'ADDS' if ok else 'no'} — {len(common)} common days "
            f"{np.datetime64(int(common[0]), 'D')}…{np.datetime64(int(common[-1]), 'D')}; daily corr lag−1/0/+1 "
            f"{lag[-1]}/{lag[0]}/{lag[1]}, weekly {cw}, monthly {cm}; Sharpe lsn {sh(a)} (CAGR {cagr(a) * 100:.1f}%, DD "
            f"{dd(a) * 100:.1f}%), carry {sh(b)} (CAGR {cagr(b) * 100:.1f}%, DD {dd(b) * 100:.1f}%), 50/50 risk-equal mix "
            f"{sh(mix)}; mix at lsn DD ×{s_mix:.2f} → CAGR {cagr(s_mix * mix) * 100:.1f}% vs lsn {cagr(a) * 100:.1f}%; "
            f"halves [lsn, carry, mix] Sharpe {halves}; years [lsn %, carry %, corr] {yrs}")
    vb.note("p63 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
