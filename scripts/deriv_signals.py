"""Derivatives-data signals on 18 coins, 15m, real (non-imputed) metrics only — 2023-01 → 2026-09.
Each signal: entry next 15m open, time exits 4/8/24h, catastrophe stop −12 % (adverse), net of 0.14 % round-trip costs.
Day-clustered (equal-weight basket per day per signal) to avoid counting one crash 18 times. Random baseline = same side,
random real-metric bars. Usage: python scripts/deriv_signals.py"""
import sys, collections, datetime as dt, numpy as np
sys.path.insert(0, 'scripts')
from ap_data import load
syms, times, A = load()
O, H, L, C, V = A['open'], A['high'], A['low'], A['close'], A['volume_quote']
LL, SL = np.abs(A['long_liq_usd']), np.abs(A['short_liq_usd'])      # long / short liquidations, $ per bar
OI, FR, IMP = A['open_interest_usd'], A['funding_rate_pct'], A['is_imputed_metrics']
NS, T = C.shape; COST = 0.0014
REAL = (IMP == 0) & np.isfinite(C) & (times >= 1672531200000)[None, :]
YEAR = np.array([dt.datetime.utcfromtimestamp(t / 1000).year for t in times]); DAY = np.array([dt.datetime.utcfromtimestamp(t / 1000).strftime('%Y-%m-%d') for t in times])
r24 = np.full_like(C, np.nan); r24[:, 96:] = C[:, 96:] / C[:, :-96] - 1
avail = np.isfinite(r24).sum(0); BR = np.where(avail >= 8, (r24 < -0.03).sum(0) / np.maximum(avail, 1), 0)


def roll_mean(x, n):
    x0 = np.nan_to_num(x); cs = np.cumsum(x0, axis=1); out = np.full_like(x0, np.nan); out[:, n:] = (cs[:, n:] - cs[:, :-n]) / n; return out


LLm, SLm, Vm = roll_mean(LL, 2880), roll_mean(SL, 2880), roll_mean(V, 96)
OI16 = np.full_like(OI, np.nan); OI16[:, 16:] = OI[:, 16:] / OI[:, :-16] - 1
R1 = np.full_like(C, np.nan); R1[:, 1:] = C[:, 1:] / C[:, :-1] - 1
R4h = np.full_like(C, np.nan); R4h[:, 16:] = C[:, 16:] / C[:, :-16] - 1


def trade(k, i, sg, hold, cat=0.12):
    e = O[k, i + 1]
    if not np.isfinite(e): return None
    end = min(T - 1, i + hold)
    for j in range(i + 1, end + 1):
        if sg > 0 and L[k, j] <= e * (1 - cat): return -cat - COST
        if sg < 0 and H[k, j] >= e * (1 + cat): return -cat - COST
    if not np.isfinite(C[k, end]): return None
    return sg * (C[k, end] / e - 1) - COST


def test(name, mask, sg, holds=(16, 32, 96), gap=16):
    mask = mask & REAL; mask[:, :3000] = False; mask[:, -200:] = False
    ev = []
    for k in range(NS):
        last = -10 ** 9
        for i in np.where(mask[k])[0]:
            if i - last >= gap: ev.append((k, i)); last = i
    if len(ev) < 20:
        print(f"{name:42} n={len(ev)} — мало"); return
    for h in holds:
        rs = [(k, i, trade(k, i, sg, h)) for k, i in ev]; rs = [x for x in rs if x[2] is not None]
        a = np.array([x[2] for x in rs]) * 100
        bd = collections.defaultdict(list)
        for k, i, r in rs: bd[DAY[i]].append(r)
        dr = np.array([np.mean(v) for v in bd.values()]) * 100
        yr = collections.defaultdict(list)
        for k, i, r in rs: yr[YEAR[i]].append(r)
        ys = ' '.join(f"{y}:{np.mean(v) * 100:+.2f}" for y, v in sorted(yr.items()))
        print(f"{name:42} {h // 4:3d}h n={len(a):5d} avg={a.mean():+.3f}% WR={(a > 0).mean() * 100:3.0f}% | days={len(dr):4d} dayavg={dr.mean():+.3f}% dayWR={(dr > 0).mean() * 100:3.0f}% | {ys}", flush=True)


rng = np.random.default_rng(5)
base = np.zeros_like(REAL); idx = np.argwhere(REAL); pick = idx[rng.choice(len(idx), 6000, replace=False)]; base[pick[:, 0], pick[:, 1]] = True
print("coins", NS, "real bars from 2023:", int(REAL.sum()))
test("BASE random long", base.copy(), +1); test("BASE random short", base.copy(), -1)
# 1. long-liquidation spike (real $) + red candle → fade long
for z in (5, 10, 20):
    test(f"LIQ long-liq ≥{z}× 30d avg & red bar", (LL >= z * LLm) & (LLm > 0) & (R1 < 0), +1)
test("LIQ long-liq ≥10× & bar ≤ −1.5%", (LL >= 10 * LLm) & (LLm > 0) & (R1 <= -0.015), +1)
test("LIQ long-liq ≥10× & stress ≥0.7", (LL >= 10 * LLm) & (LLm > 0) & (BR >= 0.7)[None, :], +1)
# 2. short-squeeze spike → fade short
for z in (5, 10, 20):
    test(f"SQZ short-liq ≥{z}× & green bar → short", (SL >= z * SLm) & (SLm > 0) & (R1 > 0), -1)
# 3. funding extremes (per 8h, %)
test("FUND funding ≤ −0.05% → long", FR <= -0.05, +1, gap=96)
test("FUND funding ≤ −0.10% → long", FR <= -0.10, +1, gap=96)
test("FUND funding ≥ 0.05% → short", FR >= 0.05, -1, gap=96)
test("FUND funding ≥ 0.10% → short", FR >= 0.10, -1, gap=96)
# 4. OI flush: open interest −8 % in 4h with price −5 % → long; OI build +15 % with price +5 % → short (late longs)
test("OI flush OI4h ≤ −8% & px4h ≤ −5% → long", (OI16 <= -0.08) & (R4h <= -0.05), +1)
test("OI flush OI4h ≤ −12% & px4h ≤ −7% → long", (OI16 <= -0.12) & (R4h <= -0.07), +1)
test("OI build OI4h ≥ +15% & px4h ≥ +5% → short", (OI16 >= 0.15) & (R4h >= 0.05), -1)
test("OI build OI4h ≥ +15% & px4h ≥ +5% → long", (OI16 >= 0.15) & (R4h >= 0.05), +1)
# 5. current E rule on this 18-coin set (real-metric period) for comparison
cs = np.cumsum(np.nan_to_num(V), axis=1); vav = np.full_like(V, np.nan); vav[:, 97:] = (cs[:, 96:-1] - cs[:, :-97]) / 96
test("E climax (−1.5%, 2× vol, stress≥0.7)", (C / O - 1 <= -0.015) & (V >= 2 * vav) & (BR >= 0.7)[None, :], +1)
