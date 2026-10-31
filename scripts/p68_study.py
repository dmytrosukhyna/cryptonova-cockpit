"""Backlog #68 (31.10.2026): the «trend factor» for the cross-section of crypto — does it beat plain 28-day momentum?

Public idea: the trend factor (Han, Zhou & Zhu; the crypto version in JFQA, 2024): normalised moving averages of 3…200
days combined by cross-sectional regressions predict next-period returns better than plain momentum.
Rule, fixed BEFORE the run (one configuration, no tuning):
  every 7 days at the daily close t: universe = every perpetual with ≥ 200 bars, 21 clean days up to t and 20-day average
  turnover ≥ $20M (the liquid set of «Нейтральная корзина», #52, #65);
  features A_L = SMA_L(close) / close, L ∈ {3, 5, 10, 20, 50, 100, 200};
  at every past rebalance s the cross-sectional OLS of the next 7-day price return (winsorised 1 %/99 %) on the 7 features
  gives β_s; at t the forecast uses the mean of all β_s whose 7-day window has ENDED by t (expanding, ≥ 26 weeks, no
  look-ahead); LONG the top forecast quintile, SHORT the bottom quintile, 0.5× gross per side, equal weights.
  Baseline, same universe, same dates, same construction: score = 28-day return (our momentum of «Нейтральная корзина»).
  Returns = close-to-close + real funding; costs 0.05 % taker + 0.02 % slippage per unit of turnover at each rebalance.
  A coin whose data ends (delisted) is closed at its last close.
Data: data.binance.vision 1d klines + fundingRate, 2020-01 → 2026-09, every USDT-M perpetual, delisted included.
Honesty: placebo = random quintiles on the same universe, 20 draws. Variants: 1 → luck hurdle N = 528.
PASS (fixed now): Sharpe > luck hurdle AND > placebo p95; in BOTH halves Sharpe > 0 AND > the momentum baseline's;
weekly t ≥ 2; DD ≥ −25 %.
Reported, not a gate: momentum alone, correlation of the two, legs, price/funding/costs, 2024+, years, mean β signs.
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

N_LUCK = 528
STEP = 7
LIQ = 20e6
LS = (3, 5, 10, 20, 50, 100, 200)
MIN_BETAS = 26
COST = vb.FEE + vb.SLIP


def feats(x):
    c, q = x["c"], x["q"]
    n = len(c)
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    r[~x["cont"]] = 0.0
    cs = np.r_[0.0, np.cumsum(c)]
    A = np.full((len(LS), n), np.nan)
    for j, L in enumerate(LS):
        if n >= L:
            A[j, L - 1:] = (cs[L:] - cs[:-L]) / L / c[L - 1:]
    tv = np.full(n, np.nan); ok = np.zeros(n, bool); mom = np.full(n, np.nan)
    qs = np.r_[0.0, np.cumsum(q)]
    tv[19:] = (qs[20:] - qs[:-20]) / 20
    cont = x["cont"].astype(int); ccs = np.r_[0, np.cumsum(cont)]
    ok[20:] = (ccs[21:] - ccs[:-21]) == 21
    mom[28:] = c[28:] / c[:-28] - 1
    x["r"], x["A"], x["tv"], x["okw"], x["mom"] = r, A, tv, ok, mom
    return x


def grid(data, d0, d1):
    n, m = len(data), d1 - d0 + 1
    R = np.zeros((n, m)); F = np.zeros((n, m)); H = np.zeros((n, m), bool); OK = np.zeros((n, m), bool)
    C = np.full((n, m), np.nan); TV = np.full((n, m), np.nan); MOM = np.full((n, m), np.nan)
    A = np.full((len(LS), n, m), np.nan)
    for k, x in enumerate(data):
        idx = x["d"] - d0
        H[k, idx] = True
        R[k, idx] = x["r"]; F[k, idx] = x["f"]; C[k, idx] = x["c"]
        TV[k, idx] = x["tv"]; OK[k, idx] = x["okw"]; MOM[k, idx] = x["mom"]
        A[:, k, idx] = x["A"]
    return R, F, H, OK, C, TV, MOM, A


def eligible(G, t):
    R, F, H, OK, C, TV, MOM, A = G
    ok = H[:, t] & OK[:, t] & (np.nan_to_num(TV[:, t]) >= LIQ) & np.isfinite(MOM[:, t]) & np.isfinite(A[:, :, t]).all(0)
    return np.flatnonzero(ok)


def betas(G, t_first, m):
    """β_s for every rebalance s (regression of the s → s+7 return on features at s); available from s + 7."""
    R, F, H, OK, C, TV, MOM, A = G
    out = {}
    for s in range(t_first, m - STEP, STEP):
        idx = eligible(G, s)
        if len(idx) < 20:
            continue
        y = C[idx, s + STEP] / C[idx, s] - 1
        g = np.isfinite(y)
        idx, y = idx[g], y[g]
        if len(idx) < 20:
            continue
        lo, hi = np.percentile(y, [1, 99]); y = np.clip(y, lo, hi)
        X = np.c_[np.ones(len(idx)), A[:, idx, s].T]
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        out[s] = b[1:]
    return out


def run(G, mode, B=None, start=None, rng=None, legs=(1, 1), cost=COST):
    R, F, H, OK, C, TV, MOM, A = G
    n, m = R.shape
    w = np.zeros(n)
    pr = np.zeros(m); price = np.zeros(m); fund = np.zeros(m); cst = np.zeros(m)
    weekly, sizes = [], []
    wk = 0.0
    keys = sorted(B) if B else []
    for t in range(start, m - 1):
        if t > start:
            w[~H[:, t]] = 0.0
            pp = float(w @ R[:, t]); ff = float(-(w @ F[:, t]))
            day = pp + ff
            pr[t] += day; price[t] += pp; fund[t] += ff
            wk = (1 + wk) * (1 + day) - 1
            w = w * (1 + R[:, t]) / (1 + day) if 1 + day > 0 else w * 0
        if (t - start) % STEP == 0:
            if t > start:
                weekly.append(wk)
            wk = 0.0
            idx = eligible(G, t)
            nw = np.zeros(n)
            if len(idx) >= 20:
                k5 = len(idx) // 5
                if rng is not None:
                    order = rng.permutation(idx)
                else:
                    if mode == "trend":
                        bs = [B[s] for s in keys if s + STEP <= t]
                        score = A[:, idx, t].T @ np.mean(bs, axis=0)
                    else:
                        score = MOM[idx, t]
                    order = idx[np.argsort(score, kind="stable")]
                lo, hi = order[:k5], order[-k5:]
                nw[hi] = 0.5 / k5 * legs[0]
                nw[lo] = -0.5 / k5 * legs[1]
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
    vb.note("p68 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    span = np.arange(d0, live_end + 1)
    m = len(span)
    G = grid(data, d0, live_end)
    t_first = next(t for t in range(m) if len(eligible(G, t)) >= 20)
    B = betas(G, t_first, m)
    ks = sorted(B)
    start = ks[MIN_BETAS - 1] + STEP          # the first date with ≥ 26 finished regressions
    mb = np.mean([B[s] for s in ks], axis=0)
    vb.note("p68 · betas", f"{len(B)} weekly regressions from day {t_first}; trading from {str(np.datetime64(int(span[start]), 'D'))}; "
            f"mean β (L=3…200): {', '.join(f'{v:+.2f}' for v in mb)}")

    res = {}
    for mode in ("trend", "mom"):
        pr, price, fund, cst, weekly, sizes = run(G, mode, B, start)
        res[mode] = (pr[start:], price[start:], fund[start:], cst[start:], weekly, sizes)
    sp = span[start:]
    yrs = len(sp) / 365
    stT = vb.stats(sp, res["trend"][0]); stM = vb.stats(sp, res["mom"][0])
    twT = tstat(res["trend"][4]); twM = tstat(res["mom"][4])
    plac = []
    rng = np.random.default_rng(68)
    for _ in range(20):
        q, *_ = run(G, "trend", B, start, rng=rng)
        plac.append(sharpe(q[start:]))
    p95 = float(np.percentile(plac, 95))
    ok = (stT["sharpe"] > stT["hurdle"] and stT["sharpe"] > p95 and min(stT["halves"]) > 0
          and all(a > b for a, b in zip(stT["halves"], stM["halves"])) and twT >= 2 and stT["dd_pct"] >= -25)
    m24 = sp >= int(np.datetime64("2024-01-01").astype("datetime64[D]").astype(np.int64))
    sz = res["trend"][5]
    vb.note("p68 · trend", f"{'PASS' if ok else 'no'} — Sharpe {stT['sharpe']} (halves {stT['halves']}, hurdle {stT['hurdle']}), "
            f"{stT['cagr_pct']}%/yr, DD {stT['dd_pct']}%, weeks {len(res['trend'][4])} mean {res['trend'][4].mean() * 100:+.2f}% "
            f"t {twT:.2f}; placebo mean {np.mean(plac):.2f} p95 {p95:.2f}; universe median {int(np.median(sz))} "
            f"(min {min(sz)}, max {max(sz)}); 2024+ Sharpe {sharpe(res['trend'][0][m24])}")
    vb.note("p68 · momentum 28d", f"Sharpe {stM['sharpe']} (halves {stM['halves']}), {stM['cagr_pct']}%/yr, DD {stM['dd_pct']}%, "
            f"weekly t {twM:.2f}; 2024+ Sharpe {sharpe(res['mom'][0][m24])}; daily corr trend↔mom "
            f"{np.corrcoef(res['trend'][0], res['mom'][0])[0, 1]:.2f}")
    lo_leg, *_r = run(G, "trend", B, start, legs=(1, 0))
    hi_leg, *_r = run(G, "trend", B, start, legs=(0, 1))
    T = res["trend"]
    yr = {}
    for y in range(2020, 2027):
        a = int(np.datetime64(f"{y}-01-01").astype("datetime64[D]").astype(np.int64))
        b = int(np.datetime64(f"{y + 1}-01-01").astype("datetime64[D]").astype(np.int64))
        mk = (sp >= a) & (sp < b)
        if mk.sum() > 30:
            yr[y] = (round(T[0][mk].sum() * 100, 1), round(res["mom"][0][mk].sum() * 100, 1))
    vb.note("p68 · parts", f"trend per year: price {T[1].sum() / yrs * 100:+.1f}%, funding {T[2].sum() / yrs * 100:+.1f}%, "
            f"costs {-T[3].sum() / yrs * 100:.1f}%; long leg alone Sharpe {sharpe(lo_leg[start:])}, short leg alone "
            f"Sharpe {sharpe(hi_leg[start:])}; years (trend %, mom %): {yr}")
    vb.note("p68 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
