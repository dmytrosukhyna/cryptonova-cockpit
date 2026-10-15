"""Hypothesis: when the market sells off broadly (≥70 % of coins down >3 % over 24h), is shorting profitable?
Variants (all causal, 15m bars, 10 coins 2020-09→2026-09, costs 0.07 % per side + 0.02 % slip):
  S1  short at the first 'stress' bar close; stop = 4h high + 0.25 ATR; target 1.5R; max hold 48h
  S2  short the bounce: after stress, wait for price to retrace ≥50 % of the last 4h drop (within 8h), short there; same stop/target
  S3  short at stress close, stop = 4h high, no target, exit when breadth recovers (<40 % coins down) or 48h
Baselines: random short entries with the same stop/target geometry; and LONG at stress (the 'buy the dip' reflex).
Reported in R per trade (risk = entry→stop), with IS/OOS split at 67 % of time."""
import sqlite3, sys, math, random
import numpy as np
sys.path.insert(0, '.')
conn = sqlite3.connect('data/binance_research.sqlite')
syms = [r[0] for r in conn.execute("select distinct symbol from candles where interval='15m'")]
D = {}
for s in syms:
    rows = conn.execute("select open_time, open, high, low, close from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
    D[s] = np.array(rows, dtype=float)
# common time grid
times = sorted(set.intersection(*[set(D[s][:, 0].astype(int)) for s in syms]))
times = np.array(times); T = len(times)
idx = {s: {int(t): i for i, t in enumerate(D[s][:, 0].astype(int))} for s in syms}
C = np.full((len(syms), T), np.nan); H = C.copy(); L = C.copy(); O = C.copy()
for k, s in enumerate(syms):
    ii = np.array([idx[s][int(t)] for t in times]); C[k] = D[s][ii, 4]; H[k] = D[s][ii, 2]; L[k] = D[s][ii, 3]; O[k] = D[s][ii, 1]
ret24 = C[:, 96:] / C[:, :-96] - 1                       # aligned to t index t>=96
breadth = (ret24 < -0.03).mean(axis=0)                    # share of coins down >3% over 24h, at bar t (t>=96)
def atr(k, n=14):
    tr = np.maximum(H[k, 1:] - L[k, 1:], np.maximum(abs(H[k, 1:] - C[k, :-1]), abs(L[k, 1:] - C[k, :-1])))
    a = np.full(T, np.nan); a[n:] = np.convolve(tr, np.ones(n) / n, 'valid')[:T - n]; return a
ATR = np.array([atr(k) for k in range(len(syms))])
FEE = 0.0007 + 0.0002
def run_trade(k, i, side, stop, target, max_bars, exit_rule=None):
    """side -1 short/+1 long, enter at open of bar i+1; stop-first inside a bar; returns R."""
    e = O[k, i + 1]; r = abs(e - stop)
    if r <= 0 or not np.isfinite(r): return None
    for j in range(i + 1, min(T - 1, i + 1 + max_bars)):
        hi, lo = H[k, j], L[k, j]
        if side == -1 and hi >= stop: return (-(stop - e) / r) - FEE * e / r
        if side == +1 and lo <= stop: return (-(e - stop) / r) - FEE * e / r
        if target is not None:
            if side == -1 and lo <= target: return ((e - target) / r) - FEE * e / r
            if side == +1 and hi >= target: return ((target - e) / r) - FEE * e / r
        if exit_rule and exit_rule(j): return side * (C[k, j] - e) / r - FEE * e / r
    j = min(T - 1, i + max_bars); return side * (C[k, j] - e) / r - FEE * e / r
def episodes(th=0.7, cool=96):
    out = []; last = -10**9
    for t in range(96, T - 200):
        if breadth[t - 96] >= th and breadth[t - 97] < th and t - last >= cool:
            out.append(t); last = t
    return out
eps = episodes()
print(f"coins {len(syms)}, bars {T}, stress episodes (first bar of ≥70% breadth, 24h cooldown): {len(eps)}")
cut = times[int(T * 0.67)]
def summarize(name, res):
    for lab, rs in (("IS", [r for t, r in res if times[t] < cut]), ("OOS", [r for t, r in res if times[t] >= cut]), ("ALL", [r for _, r in res])):
        if not rs: continue
        a = np.array(rs); print(f"  {name:28} {lab:3} n={len(a):4d} avgR={a.mean():+.3f} WR={(a>0).mean()*100:4.0f}% medR={np.median(a):+.2f} sumR={a.sum():+.0f}")
def strat(variant, side=-1, th_recover=0.4):
    res = []
    for t in eps:
        for k in range(len(syms)):
            a = ATR[k, t]
            if not np.isfinite(a) or a <= 0: continue
            hi4 = H[k, t - 16:t + 1].max(); lo4 = L[k, t - 16:t + 1].min()
            if variant == 'S1':
                stop = hi4 + 0.25 * a if side == -1 else lo4 - 0.25 * a
                e = O[k, t + 1]; r = abs(e - stop); tgt = e - 1.5 * r if side == -1 else e + 1.5 * r
                x = run_trade(k, t, side, stop, tgt, 192)
            elif variant == 'S2':
                drop = hi4 - C[k, t]; lvl = C[k, t] + 0.5 * drop; x = None
                for j in range(t + 1, min(T - 2, t + 33)):
                    if H[k, j] >= lvl:
                        stop = hi4 + 0.25 * a; e = O[k, j + 1]; r = abs(e - stop)
                        if r <= 0: break
                        x = run_trade(k, j, -1, stop, e - 1.5 * r, 192); break
            elif variant == 'S3':
                stop = hi4 + 0.25 * a
                x = run_trade(k, t, -1, stop, None, 192, exit_rule=lambda j: breadth[j - 96] < th_recover)
            if x is not None: res.append((t, x))
    return res
summarize("S1 short at stress", strat('S1'))
summarize("S1 LONG at stress (dip-buy)", strat('S1', side=+1))
summarize("S2 short the bounce", strat('S2'))
summarize("S3 short until breadth<40%", strat('S3'))
# random baseline for S1 short geometry
random.seed(1); res = []
for _ in range(len(eps)):
    t = random.randrange(200, T - 200)
    for k in range(len(syms)):
        a = ATR[k, t]
        if not np.isfinite(a) or a <= 0: continue
        hi4 = H[k, t - 16:t + 1].max(); stop = hi4 + 0.25 * a; e = O[k, t + 1]; r = abs(e - stop)
        if r <= 0: continue
        x = run_trade(k, t, -1, stop, e - 1.5 * r, 192)
        if x is not None: res.append((t, x))
summarize("random short, same geometry", res)
# by year for S1 short
res = strat('S1'); import datetime
years = {}
for t, r in res: years.setdefault(datetime.datetime.utcfromtimestamp(times[t] / 1000).year, []).append(r)
print("S1 short by year:", {y: (len(v), round(float(np.mean(v)), 2)) for y, v in sorted(years.items())})
