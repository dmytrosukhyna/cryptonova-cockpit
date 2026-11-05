"""Backlog #68 (05.11.2026): the «trend factor» on the crypto cross-section vs our 28-day momentum and vs lsn.

Public idea: «Trend factor in the cross-section of cryptocurrency returns» (JFQA, 2024,
jfqa.org/wp-content/uploads/2024/09/24955-trend-factor-cross-section-crypto-returns.pdf) — moving averages of 3…200 days,
combined by cross-sectional regressions, predict next-week returns better than plain momentum.
Rule, fixed BEFORE the run (one configuration, no tuning):
  every 7 days at the daily close t, universe = every Binance USDT-M perpetual with ≥ 200 bars, the last 21 days without a
  bar gap and 20-day average turnover ≥ $20M (the liquid set lsn trades);
  predictors A_L = MA_L(close) / close − 1 for L in 3, 5, 10, 20, 50, 100, 200 (clipped to [−0.9, 3]);
  each week a cross-sectional OLS of the past week's return (t−7 → t) on last week's A (with intercept) gives betas;
  forecast = A_t · mean of ALL PAST betas (expanding window, the current week's regression uses only data up to t);
  trading starts after 26 weekly regressions. LONG the top forecast quintile, SHORT the bottom, equal weights,
  0.5× gross per side. Returns = close-to-close + real funding; costs 0.05 % taker + 0.02 % slippage per unit turnover.
  Delisted coins are closed at their last close.
Comparator in the SAME engine: 28-day momentum quintiles (rank of close_t / close_{t−28} − 1), same universe and costs.
Comparator outside: lsn as backtested (scripts/own_signals_study.py ls_neutral, inverse-vol, gross 0.75), daily marks.
Honesty: placebo = random quintiles of the same universe, 20 draws. Variants: 1 → luck hurdle N = 523.
PASS (fixed now): Sharpe > luck hurdle AND > placebo p95, both halves > 0, t of weekly returns ≥ 2, AND on the common window
with lsn: Sharpe above lsn in BOTH halves and, scaled to lsn's worst drawdown, yearly growth above lsn's.
Public data only (data.binance.vision, DeFiLlama for lsn's quality card), no keys, no orders.
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

N_LUCK = 523
STEP = 7
LAGS = (3, 5, 10, 20, 50, 100, 200)
LIQ = 20e6
WARM = 26
COST = vb.FEE + vb.SLIP
DAYMS = 86_400_000


def feats(x):
    c, q = x["c"], x["q"]
    n = len(c)
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    r[~x["cont"]] = 0.0
    A = np.full((len(LAGS), n), np.nan)
    for j, L in enumerate(LAGS):
        if n >= L:
            ma = np.convolve(c, np.ones(L) / L, "valid")
            A[j, L - 1:] = np.clip(ma / c[L - 1:] - 1, -0.9, 3.0)
    tv = np.full(n, np.nan); ok = np.zeros(n, bool)
    for i in range(20, n):
        tv[i] = q[i - 19:i + 1].mean()
        ok[i] = bool(x["cont"][i - 20:i + 1].all()) and i >= 199
    x["r"], x["A"], x["tv"], x["okw"] = r, A, tv, ok
    return x


def grid(data, d0, d1):
    n, m = len(data), d1 - d0 + 1
    R = np.zeros((n, m)); F = np.zeros((n, m)); H = np.zeros((n, m), bool); OK = np.zeros((n, m), bool)
    TV = np.full((n, m), np.nan); P = np.full((n, m), np.nan); A = np.full((len(LAGS), n, m), np.nan)
    for k, x in enumerate(data):
        idx = x["d"] - d0
        H[k, idx] = True
        R[k, idx] = x["r"]; F[k, idx] = x["f"]; TV[k, idx] = x["tv"]; OK[k, idx] = x["okw"]; P[k, idx] = x["c"]
        A[:, k, idx] = x["A"]
    return R, F, H, OK, TV, P, A


def run(G, mode="tf", rng=None, legs=(1, 1), start=None):
    R, F, H, OK, TV, P, A = G
    n, m = R.shape
    w = np.zeros(n)
    pr = np.zeros(m); price = np.zeros(m); fund = np.zeros(m); cst = np.zeros(m)
    weekly, sizes, betas = [], [], []
    prev = None            # (idx, X) at the previous rebalance
    wk = 0.0
    t_first = 201
    first_trade = None
    for t in range(t_first, m - 1):
        if t > t_first:
            w[~H[:, t]] = 0.0
            pp = float(w @ R[:, t]); ff = float(-(w @ F[:, t]))
            day = pp + ff
            pr[t] += day; price[t] += pp; fund[t] += ff
            wk = (1 + wk) * (1 + day) - 1
            w = w * (1 + R[:, t]) / (1 + day) if 1 + day > 0 else w * 0
        if (t - t_first) % STEP:
            continue
        if any(w != 0):
            weekly.append(wk)
        wk = 0.0
        ok = H[:, t] & OK[:, t] & (np.nan_to_num(TV[:, t]) >= LIQ) & np.isfinite(A[:, :, t]).all(axis=0)
        idx = np.flatnonzero(ok)
        X = A[:, idx, t].T
        # 1) regression of the week just ended on last week's predictors (only data up to t)
        if prev is not None:
            pi, pX = prev
            alive = H[pi, t] & np.isfinite(P[pi, t])
            if alive.sum() >= 20:
                y = P[pi[alive], t] / P[pi[alive], t - STEP] - 1
                good = np.isfinite(y)
                Z = np.c_[np.ones(good.sum()), pX[alive][good]]
                b, *_ = np.linalg.lstsq(Z, y[good], rcond=None)
                betas.append(b[1:])
        prev = (idx, X)
        nw = np.zeros(n)
        go = len(idx) >= 20 and (mode != "tf" or len(betas) >= WARM) and (start is None or t >= start)
        if go:
            if first_trade is None:
                first_trade = t
            k5 = len(idx) // 5
            if rng is not None:
                order = rng.permutation(idx)
            elif mode == "tf":
                s = X @ np.mean(betas, axis=0)
                order = idx[np.argsort(s, kind="stable")]
            else:  # 28-day momentum
                s = P[idx, t] / P[idx, t - 28] - 1
                s = np.nan_to_num(s, nan=0.0)
                order = idx[np.argsort(s, kind="stable")]
            lo, hi = order[:k5], order[-k5:]
            nw[hi] = 0.5 / k5 * legs[0]
            nw[lo] = -0.5 / k5 * legs[1]
            sizes.append(len(idx))
        c = COST * float(np.abs(nw - w).sum())
        pr[t] -= c; cst[t] += c
        w = nw
    return pr, price, fund, cst, np.array(weekly), sizes, np.array(betas), first_trade


def sharpe(p):
    return round(float(p.mean() / p.std() * math.sqrt(365)), 2) if len(p) > 2 and p.std() > 0 else 0.0


def tstat(wk):
    return wk.mean() / (wk.std(ddof=1) / math.sqrt(len(wk))) if len(wk) > 2 and wk.std() > 0 else 0.0


def dd(p):
    eq = np.cumprod(1 + p)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def cagr(p):
    eq = np.prod(1 + p)
    return float(eq ** (365 / len(p)) - 1) if eq > 0 else -1.0


def scale_to_dd(p, target):
    lo, hi = 0.05, 10.0
    for _ in range(50):
        k = (lo + hi) / 2
        if dd(k * p) < target:
            hi = k
        else:
            lo = k
    return lo


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [feats(f36.prep(x)) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p68 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    span = np.arange(d0, live_end + 1)
    G = grid(data, d0, live_end)

    pr, price, fund, cst, weekly, sizes, betas, ft = run(G, "tf")
    a = ft
    sp, p = span[a:], pr[a:]
    st = vb.stats(sp, p)
    tw = tstat(weekly)
    yrs = len(p) / 365
    bm = betas.mean(axis=0) if len(betas) else np.zeros(len(LAGS))
    bt = (bm / (betas.std(axis=0, ddof=1) / math.sqrt(len(betas)))) if len(betas) > 2 else bm
    plac = []
    rng = np.random.default_rng(68)
    for _ in range(20):
        q, *_r = run(G, "tf", rng=rng, start=a)
        plac.append(sharpe(q[a:]))
    p95 = float(np.percentile(plac, 95))
    mo, *_r = run(G, "mom", start=a)
    sm = vb.stats(sp, mo[a:])
    core = (st["sharpe"] > st["hurdle"] and st["sharpe"] > p95 and min(st["halves"]) > 0 and tw >= 2)
    vb.note("p68 · trend factor", f"{'core ok' if core else 'core no'} — from {np.datetime64(int(sp[0]), 'D')}: Sharpe "
            f"{st['sharpe']} (halves {st['halves']}, hurdle {st['hurdle']}), {st['cagr_pct']}%/yr, DD {st['dd_pct']}%, "
            f"weeks {len(weekly)} mean {weekly.mean() * 100:+.2f}% t {tw:.2f}; placebo mean {np.mean(plac):.2f} p95 "
            f"{p95:.2f}; universe/week median {int(np.median(sizes)) if sizes else 0} (min {min(sizes) if sizes else 0}); "
            f"years {st['years']}")
    vb.note("p68 · betas", f"{len(betas)} weekly regressions; mean beta by MA lag {dict(zip(LAGS, np.round(bm, 3).tolist()))}, "
            f"t {dict(zip(LAGS, np.round(bt, 2).tolist()))}")
    vb.note("p68 · momentum 28d", f"same engine: Sharpe {sm['sharpe']} (halves {sm['halves']}), {sm['cagr_pct']}%/yr, DD "
            f"{sm['dd_pct']}%; corr with trend factor {np.corrcoef(p, mo[a:])[0, 1]:.2f}; years {sm['years']}")
    lo_leg, *_r = run(G, "tf", legs=(1, 0))
    hi_leg, *_r = run(G, "tf", legs=(0, 1))
    vb.note("p68 · parts", f"per year: price {price[a:].sum() / yrs * 100:+.1f}%, funding {fund[a:].sum() / yrs * 100:+.1f}%, "
            f"costs {-cst[a:].sum() / yrs * 100:.1f}%; long leg alone Sharpe {sharpe(lo_leg[a:])}, short leg alone "
            f"Sharpe {sharpe(hi_leg[a:])}")

    # lsn comparison on the common window
    try:
        import own_signals_study as OS
        box = {"daily": []}
        OS.ls_neutral(iv=True, ret_out=box)
        pts = sorted(box["daily"])
        ad = np.array([t // DAYMS for t, _v in pts], dtype=np.int64)
        av = np.array([v for _t, v in pts])
        L = {int(d): float(r) for d, r in zip(ad[1:], av[1:] / av[:-1] - 1)}
        T = {int(d): float(r) for d, r in zip(sp, p)}
        common = np.array(sorted(d for d in L if d in T), dtype=np.int64)
        x = np.array([L[d] for d in common]); y = np.array([T[d] for d in common])
        h = len(x) // 2
        k = scale_to_dd(y, dd(x))
        halves = [(sharpe(x[:h]), sharpe(y[:h])), (sharpe(x[h:]), sharpe(y[h:]))]
        beats = all(tf > ls for ls, tf in halves) and cagr(k * y) > cagr(x)
        ok = core and beats
        vb.note("p68 · vs lsn", f"{'PASS' if ok else 'no'} — {len(common)} common days "
                f"{np.datetime64(int(common[0]), 'D')}…{np.datetime64(int(common[-1]), 'D')}; Sharpe lsn {sharpe(x)} / TF "
                f"{sharpe(y)}; halves [lsn, TF] {halves}; TF scaled ×{k:.2f} to lsn DD {dd(x) * 100:.1f}% → CAGR "
                f"{cagr(k * y) * 100:.1f}% vs lsn {cagr(x) * 100:.1f}%; daily corr {np.corrcoef(x, y)[0, 1]:.2f}")
    except Exception as e:  # noqa: BLE001
        vb.note("p68 · vs lsn", f"lsn comparison failed: {e!r}", "warning")
    vb.note("p68 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
