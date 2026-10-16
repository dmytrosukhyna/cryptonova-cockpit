"""Higher-frequency candidates (goal ≥10 signals/day over 18 coins), 15m bars 2023-01 → 2026-09, costs 0.14% round trip.
Accept only: avg net > 0 in every year, enough trades/day. Usage: python scripts/freq_test.py"""
import sys, collections, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as ap
O, H, L, C, V, T, NS, START, DAY, COST = ap.O, ap.H, ap.L, ap.C, ap.V, ap.T, ap.NS, ap.START, ap.DAY, ap.COST
DAYS = (T - START) / 96
def agg(x, n):  # rolling helpers on 15m
    out = np.full_like(x, np.nan); cs = np.nancumsum(x, 1); out[:, n:] = (cs[:, n:] - cs[:, :-n]) / n; return out
def summ(nm, tr):
    if not tr: print(f"{nm:44} none"); return
    a = np.array([t[2] for t in tr]); y = collections.defaultdict(list)
    for k, i, r in tr: y[DAY[i][:4]].append(r)
    ys = {k: round(np.mean(v) * 100, 2) for k, v in sorted(y.items())}
    ok = all(v > 0 for v in ys.values())
    print(f"{nm:44} {len(a)/DAYS:5.1f}/day avg {a.mean()*100:+.3f}% WR {(a>0).mean()*100:3.0f}% years {ys} {'✅' if ok else ''}", flush=True)
def events(mask, sg, hold, stop=None, tgt_fn=None, gap=None):
    gap = gap or hold; tr = []
    for k in range(NS):
        last = -10**9
        for i in np.where(mask[k])[0]:
            if i < START or i - last < gap or i + hold + 2 >= T or not np.isfinite(O[k, i + 1]): continue
            last = i; e = O[k, i + 1]
            st = None if stop is None else e * (1 - sg * stop)
            tg = None if tgt_fn is None else tgt_fn(k, i)
            x, g = ap.walk(k, i + 1, sg, st, tg, hold); tr.append((k, i + 1, g - COST))
    return tr
R = np.full_like(C, np.nan); R[:, 1:] = C[:, 1:] / C[:, :-1] - 1
def ret(n): r = np.full_like(C, np.nan); r[:, n:] = C[:, n:] / C[:, :-n] - 1; return r
r4, r24, r1 = ret(16), ret(96), ret(4)
with np.errstate(all='ignore'):
    # 1) cross-sectional reversal: every 4h long the 3 worst 24h coins (vs basket), hold 4/8/24h
    for hold in (16, 32, 96):
        for side in (1, -1):
            tr = []
            for i in range(START, T - hold - 2, 16):
                col = r24[:, i]; ok = np.isfinite(col) & np.isfinite(O[:, i + 1])
                if ok.sum() < 10: continue
                idx = np.argsort(np.where(ok, col, np.inf if side > 0 else -np.inf))[:3] if side > 0 else np.argsort(np.where(ok, col, -np.inf))[::-1][:3]
                for k in idx:
                    x, g = ap.walk(k, i + 1, side, None, None, hold); tr.append((k, i + 1, g - COST))
            summ(f"XS reversal {'long losers' if side>0 else 'short winners'} hold {hold//4}h", tr)
    # 2) 1h Bollinger stretch long: close 2.5σ below 20h mean (on 15m, n=80), exit at mean or hold
    m80 = agg(C, 80); sd = np.sqrt(np.maximum(agg(C * C, 80) - m80 ** 2, 0))
    for z in (2.5, 3.0):
        for sg in (1, -1):
            mask = (C < m80 - z * sd) if sg > 0 else (C > m80 + z * sd)
            for hold in (16, 48):
                summ(f"BB {z}σ {'long' if sg>0 else 'short'} exit mean/{hold//4}h stop 4%", events(mask, sg, hold, 0.04, lambda k, i: m80[k, i], gap=16))
    # 3) trend swing: 4h breakout of 5-day high while above 20-day mean, hold 3 days, stop 6%
    hi5 = np.full_like(H, np.nan)
    for k in range(NS):
        import numpy.lib.stride_tricks as st
    m20d = agg(C, 1920)
    hh = np.full_like(C, np.nan)
    for k in range(NS):
        s = C[k]; w = 480
        from numpy.lib.stride_tricks import sliding_window_view as sw
        hh[k, w:] = np.nanmax(sw(s, w)[:-1], 1)
    for hold in (96, 288):
        summ(f"Swing breakout 5d high & >20d mean hold {hold//96}d stop 6%", events((C > hh) & (C > m20d), 1, hold, 0.06, gap=96))
    # 4) relaxed B: 15m drop ≤ -2.5σ_30d with lower wick ≥0.5, hold 8h stop 3%
    sig = np.sqrt(agg(np.nan_to_num(R) ** 2, 2880)); rng = H - L
    wick = (np.minimum(O, C) - L) / np.where(rng > 0, rng, np.nan)
    for z in (2.5, 3.0):
        for hold in (8, 32):
            summ(f"Drop {z}σ + wick, hold {hold//4}h stop 3%", events((R <= -z * sig) & (wick >= 0.5), 1, hold, 0.03, gap=8))
    # 5) 4h dip in uptrend: r4 ≤ -3% while price > 20d mean, hold 24h stop 5%
    for d in (-0.03, -0.05):
        summ(f"Dip {d*100:.0f}% 4h in uptrend hold 24h stop 5%", events((r4 <= d) & (C > m20d), 1, 96, 0.05, gap=32))
