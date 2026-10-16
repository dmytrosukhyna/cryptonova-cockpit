"""Hypotheses 16.10: (1) bounce from the channel border in the direction of the big trend; (2) false breakout of a
20-bar extreme, then entry on the return (Gerchik-style). 18 coins, 4h bars from 15m, 2020-09 → 2026-09, costs 0.14 % per
round trip, stop first inside a bar. Results in R (risk units) with per-year averages and a random-entry baseline."""
import sys, collections, datetime as dt, numpy as np
sys.path.insert(0, 'scripts')
from ap_data import load
syms, times, A = load()
NS = len(syms); COST = 0.0014


def bars4(k):
    O, H, L, C = A['open'][k], A['high'][k], A['low'][k], A['close'][k]
    n = len(C) // 16
    o = O[:n * 16].reshape(n, 16)[:, 0]; h = np.nanmax(H[:n * 16].reshape(n, 16), 1); l = np.nanmin(L[:n * 16].reshape(n, 16), 1); c = C[:n * 16].reshape(n, 16)[:, -1]
    t = times[:n * 16].reshape(n, 16)[:, 0]
    ok = np.isfinite(o) & np.isfinite(c) & np.isfinite(h) & np.isfinite(l)
    return o[ok], h[ok], l[ok], c[ok], t[ok]


def atr(h, l, c, n=14):
    tr = np.r_[h[0] - l[0], np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
    out = np.full(len(c), np.nan); out[n - 1:] = np.convolve(tr, np.ones(n) / n, 'valid'); return out


def trade(o, h, l, c, i, side, stop, tgt, hold):
    e = o[i]; sg = 1 if side > 0 else -1; r = sg * (e - stop)
    if not r > 0: return None
    for j in range(i, min(len(c) - 1, i + hold)):
        if (l[j] <= stop) if sg > 0 else (h[j] >= stop): return (-r - COST * e) / r
        if tgt is not None and ((h[j] >= tgt) if sg > 0 else (l[j] <= tgt)): return (abs(tgt - e) - COST * e) / r
    j = min(len(c) - 1, i + hold); return (sg * (c[j] - e) - COST * e) / r


def report(name, res):
    if not res: print(f"{name:52} n=0"); return
    a = np.array([r for _, r in res]); y = collections.defaultdict(list)
    for t, r in res: y[dt.datetime.utcfromtimestamp(t / 1000).year].append(r)
    print(f"{name:52} n={len(a):5d} avgR={a.mean():+.3f} WR={(a > 0).mean() * 100:3.0f}% | " + ' '.join(f"{k}:{np.mean(v):+.2f}" for k, v in sorted(y.items())), flush=True)


def channel_bounce(N=60, tgt_mode='mid', stop_atr=0.5, hold=30, trend_filter=True, rnd=False, seed=1):
    out = []; rng = np.random.default_rng(seed)
    for k in range(NS):
        o, h, l, c, t = bars4(k)
        if len(c) < 400: continue
        a = atr(h, l, c); sma = np.full(len(c), np.nan); cs = np.cumsum(c); sma[1199:] = (cs[1199:] - np.r_[0, cs[:-1200]][: len(cs) - 1199]) / 1200   # 200 days of 4h
        x = np.arange(N); last = -10 ** 9
        for i in range(max(N, 1200), len(c) - hold - 2):
            if i - last < 6: continue
            seg = c[i - N:i]; b, m = np.polyfit(x, seg, 1)  # slope b, intercept m
            mid = m + b * (N - 1 + 1); res = seg - (m + b * x); up = res.max(); dn = -res.min()
            slope_up = b > 0; trend_up = c[i - 1] > sma[i - 1]
            if rnd:
                if rng.random() < 0.02:
                    side = 1 if rng.random() < 0.5 else -1
                    stop = c[i - 1] - side * 1.5 * a[i - 1]; tg = c[i - 1] + side * 1.5 * a[i - 1]
                    r_ = trade(o, h, l, c, i, side, stop, tg, hold)
                    if r_ is not None: out.append((t[i], r_)); last = i
                continue
            lowb, upb = mid - dn, mid + up
            if slope_up and (trend_up or not trend_filter) and l[i - 1] <= lowb and c[i - 1] > lowb:
                stop = min(l[i - 1], lowb) - stop_atr * a[i - 1]; tg = mid if tgt_mode == 'mid' else upb
                r_ = trade(o, h, l, c, i, 1, stop, tg, hold)
                if r_ is not None: out.append((t[i], r_)); last = i
            elif (not slope_up) and ((not trend_up) or not trend_filter) and h[i - 1] >= upb and c[i - 1] < upb:
                stop = max(h[i - 1], upb) + stop_atr * a[i - 1]; tg = mid if tgt_mode == 'mid' else lowb
                r_ = trade(o, h, l, c, i, -1, stop, tg, hold)
                if r_ is not None: out.append((t[i], r_)); last = i
    return out


def false_break(N=20, back=3, tgt_r=2.0, hold=30, longs=True, shorts=True):
    out = []
    for k in range(NS):
        o, h, l, c, t = bars4(k)
        if len(c) < 300: continue
        a = atr(h, l, c); last = -10 ** 9
        for i in range(N + back + 2, len(c) - hold - 2):
            if i - last < 6: continue
            lo = l[i - N - back:i - back].min(); hi = h[i - N - back:i - back].max()
            # broke below the 20-bar low within the last `back` bars and closed back above it now → long
            if longs and l[i - back:i].min() < lo and c[i - 1] > lo and c[i - 2] <= lo + 0.2 * a[i - 1]:
                stop = l[i - back:i].min() - 0.3 * a[i - 1]; e = o[i]; tg = e + tgt_r * (e - stop)
                r_ = trade(o, h, l, c, i, 1, stop, tg, hold)
                if r_ is not None: out.append((t[i], r_)); last = i
            elif shorts and h[i - back:i].max() > hi and c[i - 1] < hi and c[i - 2] >= hi - 0.2 * a[i - 1]:
                stop = h[i - back:i].max() + 0.3 * a[i - 1]; e = o[i]; tg = e - tgt_r * (stop - e)
                r_ = trade(o, h, l, c, i, -1, stop, tg, hold)
                if r_ is not None: out.append((t[i], r_)); last = i
    return out


if "--fb" in sys.argv:
    for N in (20, 50):
        report(f"ложный пробой N={N} цель 2R, оба направления", false_break(N=N))
        report(f"ложный пробой N={N} только лонг", false_break(N=N, shorts=False))
        report(f"ложный пробой N={N} только шорт", false_break(N=N, longs=False))
    report("ложный пробой N=20 цель 1R", false_break(N=20, tgt_r=1.0))
    sys.exit()
report("BASE случайный вход, стоп/цель 1.5 ATR", channel_bounce(rnd=True))
for N in (60, 120):
    for tm in ('mid', 'far'):
        report(f"коридор N={N} цель={tm} по тренду", channel_bounce(N=N, tgt_mode=tm))
report("коридор N=60 цель=mid БЕЗ фильтра тренда", channel_bounce(N=60, trend_filter=False))
report("коридор N=60 цель=mid стоп 1 ATR", channel_bounce(N=60, stop_atr=1.0))
for N in (20, 50):
    report(f"ложный пробой N={N} цель 2R, оба направления", false_break(N=N))
    report(f"ложный пробой N={N} только лонг", false_break(N=N, shorts=False))
    report(f"ложный пробой N={N} только шорт", false_break(N=N, longs=False))
report("ложный пробой N=20 цель 1R", false_break(N=20, tgt_r=1.0))
