"""Backlog #59 (30.10.2026): «small, illiquid, calm, wide-range» coins — a weekly long/short factor on Binance USDT-M perps.

Public idea: arXiv 2604.26747 (2026) — an LLM agent searched 25 factors; the best combined small cap + low turnover + low
volatility + large daily range; out of sample 2024–25 Sharpe 1.55 at 0.05 % per side, but only 0.46–0.57 (≈1.5 %/yr) at
0.1–0.3 % per side. We expect the costs to kill it and test it honestly on a much wider universe.
Rule, fixed BEFORE the run (one configuration, no tuning):
  every 7 days at the daily close t: universe = every perpetual with 21 clean days up to t (no bar gap) and 20-day average
  turnover ≥ $1M (tradable at all); features over the 20 days ending at t:
    turnover = mean quote volume (LOW is good), vol = std of daily close-to-close returns (LOW is good),
    range = mean (high − low) / close (HIGH is good);
  score = rank(−turnover) + rank(−vol) + rank(range); LONG the top quintile, SHORT the bottom quintile, equal weights,
  0.5× gross per side (1× gross, net 0). Positions drift inside the week. Returns = close-to-close + real funding.
  Costs: 0.05 % taker + 0.15 % slippage (our small-coin estimate) on every unit of turnover at each rebalance.
  A coin whose data ends (delisted) is closed at its last close (no exit cost charged — optimistic for the short leg).
Data: data.binance.vision 1d klines + fundingRate, 2020-01 → 2026-09, every USDT-M perpetual, delisted included.
Honesty: placebo = the same universe each week, random quintiles, 20 draws. Variants: 1 → luck hurdle N = 526.
PASS (fixed now): Sharpe > luck hurdle AND > placebo p95, both halves Sharpe > 0, t of weekly returns ≥ 2, DD ≥ −25 %.
Reported, not a gate: the paper's cost (0.05 % + 0.02 %), legs separately, 2024+ only (the paper's out-of-sample window),
price vs funding vs costs, years.
Public data only, no keys, no orders.
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

N_LUCK = 526
STEP = 7
WIN = 20
LIQ = 1e6
COST = vb.FEE + vb.SLIP_SMALL
COST_PAPER = vb.FEE + vb.SLIP


def feats(x):
    c, h, l, q = x["c"], x["h"], x["l"], x["q"]
    n = len(c)
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    r[~x["cont"]] = 0.0
    rng_ = (h - l) / c
    tv = np.full(n, np.nan); vo = np.full(n, np.nan); ra = np.full(n, np.nan); ok = np.zeros(n, bool)
    for i in range(WIN, n):
        tv[i] = q[i - WIN + 1:i + 1].mean()
        vo[i] = r[i - WIN + 1:i + 1].std()
        ra[i] = rng_[i - WIN + 1:i + 1].mean()
        ok[i] = bool(x["cont"][i - WIN:i + 1].all())
    x["r"], x["tv"], x["vo"], x["ra"], x["okw"] = r, tv, vo, ra, ok
    return x


def grid(data, d0, d1):
    n, m = len(data), d1 - d0 + 1
    R = np.zeros((n, m)); F = np.zeros((n, m)); H = np.zeros((n, m), bool); OK = np.zeros((n, m), bool)
    TV = np.full((n, m), np.nan); VO = np.full((n, m), np.nan); RA = np.full((n, m), np.nan)
    for k, x in enumerate(data):
        idx = x["d"] - d0
        H[k, idx] = True
        R[k, idx] = x["r"]; F[k, idx] = x["f"]
        TV[k, idx] = x["tv"]; VO[k, idx] = x["vo"]; RA[k, idx] = x["ra"]; OK[k, idx] = x["okw"]
    return R, F, H, OK, TV, VO, RA


def rank(a):
    o = np.argsort(a, kind="stable")
    rk = np.empty(len(a)); rk[o] = np.arange(len(a))
    return rk


def run(G, rng=None, legs=(1, 1), cost=COST):
    R, F, H, OK, TV, VO, RA = G
    n, m = R.shape
    w = np.zeros(n)
    pr = np.zeros(m); price = np.zeros(m); fund = np.zeros(m); cst = np.zeros(m)
    weekly, sizes = [], []
    wk = 0.0
    t_first = WIN + 1
    for t in range(t_first, m - 1):
        if t > t_first:
            w[~H[:, t]] = 0.0
            pp = float(w @ R[:, t]); ff = float(-(w @ F[:, t]))
            day = pp + ff
            pr[t] += day; price[t] += pp; fund[t] += ff
            wk = (1 + wk) * (1 + day) - 1
            w = w * (1 + R[:, t]) / (1 + day) if 1 + day > 0 else w * 0
        if (t - t_first) % STEP == 0:
            if t > t_first:
                weekly.append(wk)
            wk = 0.0
            ok = H[:, t] & OK[:, t] & (np.nan_to_num(TV[:, t]) >= LIQ) & np.isfinite(VO[:, t]) & np.isfinite(RA[:, t])
            idx = np.flatnonzero(ok)
            nw = np.zeros(n)
            if len(idx) >= 20:
                k5 = len(idx) // 5
                if rng is None:
                    s = rank(-TV[idx, t]) + rank(-VO[idx, t]) + rank(RA[idx, t])
                    order = idx[np.argsort(s, kind="stable")]
                else:
                    order = rng.permutation(idx)
                lo, hi = order[:k5], order[-k5:]
                nw[hi] = 0.5 / k5 * legs[0]       # long the best-matching quintile
                nw[lo] = -0.5 / k5 * legs[1]      # short the opposite quintile
                sizes.append(len(idx))
            c = cost * float(np.abs(nw - w).sum())
            pr[t] -= c; cst[t] += c
            w = nw
    return pr, price, fund, cst, np.array(weekly), sizes


def sharpe(p):
    return round(p.mean() / p.std() * math.sqrt(365), 2) if p.std() > 0 else 0.0


def tstat(wk):
    return wk.mean() / (wk.std(ddof=1) / math.sqrt(len(wk))) if len(wk) > 2 and wk.std() > 0 else 0.0


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [feats(f36.prep(x)) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p59 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    span = np.arange(d0, live_end + 1)
    G = grid(data, d0, live_end)
    btc = next(k for k, x in enumerate(data) if x["sym"] == "BTCUSDT")
    bb = vb.stats(span, G[0][btc])
    vb.note("p59 · BTC", f"Sharpe {bb['sharpe']} (halves {bb['halves']}) {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%")

    pr, price, fund, cst, weekly, sizes = run(G)
    first = np.flatnonzero(pr != 0)
    a = int(first[0]) if len(first) else 0
    sp, p = span[a:], pr[a:]
    st = vb.stats(sp, p)
    tw = tstat(weekly)
    yrs = len(p) / 365
    plac = []
    rng = np.random.default_rng(59)
    for _ in range(20):
        q, *_ = run(G, rng=rng)
        plac.append(sharpe(q[a:]))
    p95 = float(np.percentile(plac, 95))
    ok = st["sharpe"] > st["hurdle"] and st["sharpe"] > p95 and min(st["halves"]) > 0 and tw >= 2 and st["dd_pct"] >= -25
    m24 = sp >= int(np.datetime64("2024-01-01").astype("datetime64[D]").astype(np.int64))
    vb.note("p59 · factor", f"{'PASS' if ok else 'no'} — Sharpe {st['sharpe']} (halves {st['halves']}, hurdle {st['hurdle']}), "
            f"{st['cagr_pct']}%/yr, DD {st['dd_pct']}%, weeks {len(weekly)} mean {weekly.mean() * 100:+.2f}% t {tw:.2f}; "
            f"placebo Sharpe mean {np.mean(plac):.2f} p95 {p95:.2f}; universe per week median "
            f"{int(np.median(sizes)) if sizes else 0} (min {min(sizes) if sizes else 0}, max {max(sizes) if sizes else 0}); "
            f"2024+ Sharpe {sharpe(p[m24]) if m24.any() else 0.0}")
    lo_leg, *_r = run(G, legs=(1, 0))
    hi_leg, *_r = run(G, legs=(0, 1))
    cheap, *_r = run(G, cost=COST_PAPER)
    sc = vb.stats(sp, cheap[a:])
    vb.note("p59 · parts", f"per year: price {price[a:].sum() / yrs * 100:+.1f}%, funding {fund[a:].sum() / yrs * 100:+.1f}%, "
            f"costs {-cst[a:].sum() / yrs * 100:.1f}%; long leg alone Sharpe {sharpe(lo_leg[a:])} "
            f"({vb.stats(sp, lo_leg[a:])['cagr_pct']}%/yr), short leg alone Sharpe {sharpe(hi_leg[a:])} "
            f"({vb.stats(sp, hi_leg[a:])['cagr_pct']}%/yr); at 0.07%/side Sharpe {sc['sharpe']} (halves {sc['halves']}) "
            f"{sc['cagr_pct']}%/yr DD {sc['dd_pct']}%; years {st['years']}")
    vb.note("p59 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
