"""Hypothesis (from 15.10.2026): inside a broad sell-off the sharp liquidation candles get bought — is there a
systematic long there if we don't use the tight 1%-stop that our risk-off research showed loses (−0.20R)?
Variants (causal, 15m bars, 10 coins 2020-09→2026-09, costs 0.07%/side + 0.02% slip), entries only while
breadth ≥ 70% (stress):
  E1  'climax' bar: 15m drop ≤ −1.5% with volume ≥ 3× its 24h average → long at next open
  E2  'second test': price prints a 24h low within 0.3% of a low made ≥ 3h earlier (double bottom) → long at next open
Exits: (a) tight stop 1.5×ATR15 + 1.5R target (our old geometry) | (b) wide stop = low − 4×ATR15, time exit 8h |
       (c) no stop, time exit 4h / 8h / 24h (return in % of notional, so sizing decides the R)
Baseline: random long entries during the same stress bars with the same exits."""
import sqlite3, sys, random
import numpy as np
conn = sqlite3.connect('data/binance_research.sqlite')
syms = [r[0] for r in conn.execute("select distinct symbol from candles where interval='15m'")]
D = {}
for s in syms:
    rows = conn.execute("select open_time, open, high, low, close, volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
    D[s] = np.array(rows, dtype=float)
times = np.array(sorted(set.intersection(*[set(D[s][:, 0].astype(int)) for s in syms]))); T = len(times)
idx = {s: {int(t): i for i, t in enumerate(D[s][:, 0].astype(int))} for s in syms}
C = np.full((len(syms), T), np.nan); H = C.copy(); L = C.copy(); O = C.copy(); V = C.copy()
for k, s in enumerate(syms):
    ii = np.array([idx[s][int(t)] for t in times]); C[k] = D[s][ii, 4]; H[k] = D[s][ii, 2]; L[k] = D[s][ii, 3]; O[k] = D[s][ii, 1]; V[k] = D[s][ii, 5]
ret24 = C[:, 96:] / C[:, :-96] - 1
breadth = np.zeros(T); breadth[96:] = (ret24 < -0.03).mean(axis=0)
def atr(k, n=14):
    tr = np.maximum(H[k, 1:] - L[k, 1:], np.maximum(abs(H[k, 1:] - C[k, :-1]), abs(L[k, 1:] - C[k, :-1])))
    a = np.full(T, np.nan); a[n:] = np.convolve(tr, np.ones(n) / n, 'valid')[:T - n]; return a
ATR = np.array([atr(k) for k in range(len(syms))])
VAVG = np.full_like(V, np.nan)
for k in range(len(syms)):
    cs = np.cumsum(np.nan_to_num(V[k])); VAVG[k, 96:] = (cs[96:] - cs[:-96]) / 96
FEE = 0.0007 + 0.0002
cut = times[int(T * 0.67)]

def trade(k, i, stop, target, max_bars):
    e = O[k, i + 1]
    if not np.isfinite(e): return None
    for j in range(i + 1, min(T - 1, i + 1 + max_bars)):
        if stop is not None and L[k, j] <= stop: return (stop / e - 1) - 2 * FEE
        if target is not None and H[k, j] >= target: return (target / e - 1) - 2 * FEE
    j = min(T - 1, i + max_bars); return (C[k, j] / e - 1) - 2 * FEE

def entries_E1():
    out = []
    for k in range(len(syms)):
        drop = C[k, 1:] / O[k, 1:] - 1
        for i in np.where((drop <= -0.015) & (V[k, 1:] >= 3 * VAVG[k, 1:]) & (breadth[1:] >= 0.7))[0] + 1:
            if i < 200 or i > T - 200: continue
            out.append((k, i))
    return out

def entries_E2():
    out = []
    for k in range(len(syms)):
        lo24 = np.full(T, np.nan)
        for i in range(96, T): lo24[i] = L[k, i - 96:i].min()
        last = -10**9
        for i in range(200, T - 200):
            if breadth[i] < 0.7 or i - last < 48: continue
            if L[k, i] <= lo24[i] * 1.003 and L[k, i] >= lo24[i] * 0.997:
                j = int(np.argmin(L[k, i - 96:i])) + i - 96
                if i - j >= 12:
                    out.append((k, i)); last = i
    return out

def run(name, ents):
    random.seed(7)
    stress_bars = np.where(breadth >= 0.7)[0]; stress_bars = stress_bars[(stress_bars > 200) & (stress_bars < T - 200)]
    rnd = [(random.randrange(len(syms)), int(random.choice(stress_bars))) for _ in range(min(3000, len(ents) * 3))]
    for lab, E in (("", ents), ("random", rnd)):
        for exit_name, fn in (("tight 1.5ATR / 1.5R", lambda k, i: trade(k, i, L[k, i] - 1.5 * ATR[k, i], O[k, i + 1] + 1.5 * (O[k, i + 1] - (L[k, i] - 1.5 * ATR[k, i])), 192)),
                              ("wide 4ATR stop, 8h", lambda k, i: trade(k, i, L[k, i] - 4 * ATR[k, i], None, 32)),
                              ("no stop, 4h", lambda k, i: trade(k, i, None, None, 16)),
                              ("no stop, 8h", lambda k, i: trade(k, i, None, None, 32)),
                              ("no stop, 24h", lambda k, i: trade(k, i, None, None, 96))):
            res = [(i, fn(k, i)) for k, i in E]
            res = [(i, r) for i, r in res if r is not None]
            for sp, rs in (("IS", [r for i, r in res if times[i] < cut]), ("OOS", [r for i, r in res if times[i] >= cut]), ("ALL", [r for _, r in res])):
                if not rs: continue
                a = np.array(rs) * 100
                print(f"{name + ' ' + lab:14} {exit_name:22} {sp:3} n={len(a):5d} avg={a.mean():+.3f}% WR={(a > 0).mean() * 100:4.0f}% med={np.median(a):+.2f}% worst={a.min():+.1f}%")
        print()

print(f"coins {len(syms)}, bars {T}, stress bars {(breadth >= 0.7).sum()}")
run("E1 climax", entries_E1())
run("E2 2nd test", entries_E2())

# ---- E1 / no stop 24h: by year and clustered by day (one equal-weight basket per day) ----
import datetime as dt, collections
E = entries_E1()
res = [(k, i, trade(k, i, None, None, 96)) for k, i in E]
by_year = collections.defaultdict(list); by_day = collections.defaultdict(list)
for k, i, r in res:
    if r is None: continue
    d = dt.datetime.utcfromtimestamp(times[i] / 1000)
    by_year[d.year].append(r); by_day[d.strftime('%Y-%m-%d')].append(r)
print("E1 no-stop 24h by year:", {y: f"n={len(v)} avg={np.mean(v)*100:+.2f}% WR={np.mean(np.array(v)>0)*100:.0f}%" for y, v in sorted(by_year.items())})
days = sorted(by_day.items()); dr = np.array([np.mean(v) for _, v in days]) * 100
print(f"clustered by day: days={len(days)} avg day-basket={dr.mean():+.2f}% WR={(dr>0).mean()*100:.0f}% worst day={dr.min():+.1f}% best={dr.max():+.1f}% days/yr≈{len(days)/6:.0f}")
yr = collections.defaultdict(list)
for d, v in days: yr[d[:4]].append(np.mean(v))
print("day-baskets by year:", {y: f"n={len(v)} avg={np.mean(v)*100:+.2f}% sum={np.sum(v)*100:+.0f}%" for y, v in sorted(yr.items())})

# ---- catastrophe stop (−8% / −12%) with the 24h time exit, clustered by day ----
for cs in (0.08, 0.12):
    res = [(i, trade(k, i, O[k, i + 1] * (1 - cs) if np.isfinite(O[k, i + 1]) else None, None, 96)) for k, i in E]
    bd = collections.defaultdict(list)
    for i, r in res:
        if r is not None: bd[dt.datetime.utcfromtimestamp(times[i] / 1000).strftime('%Y-%m-%d')].append(r)
    dr = np.array([np.mean(v) for v in bd.values()]) * 100
    print(f"catastrophe stop {cs*100:.0f}%: days={len(dr)} avg={dr.mean():+.2f}% WR={(dr>0).mean()*100:.0f}% worst day={dr.min():+.1f}%")

# ---- robustness grid (python scripts/exhaustion_long.py --grid): drop / volume / breadth / hold, day-baskets ----
if '--grid' in sys.argv:
    def grid(drop, vol, bth, hold):
        bd = collections.defaultdict(list)
        for k in range(len(syms)):
            d_ = C[k, 1:] / O[k, 1:] - 1
            for i in np.where((d_ <= drop) & (V[k, 1:] >= vol * VAVG[k, 1:]) & (breadth[1:] >= bth))[0] + 1:
                if i < 200 or i > T - 200: continue
                r = trade(k, i, O[k, i + 1] * 0.88, None, hold)
                if r is not None: bd[dt.datetime.utcfromtimestamp(times[i] / 1000).strftime('%Y-%m-%d')].append(r)
        dr = np.array([np.mean(v) for v in bd.values()]) * 100 if bd else np.array([0.0])
        oos = np.array([np.mean(v) for d, v in bd.items() if d >= '2024-09']) * 100 if bd else np.array([0.0])
        return len(dr), dr.mean(), (dr > 0).mean() * 100, dr.min(), oos.mean() if len(oos) else float('nan')
    print("\nGRID (catastrophe stop 12%, day-baskets): drop vol breadth hold → days avg WR worst OOS(2024-09→)")
    for drop in (-0.01, -0.015, -0.02):
        for vol in (2.0, 3.0, 4.0):
            for bth in (0.6, 0.7, 0.8):
                for hold in (48, 96, 144):
                    n, a, w, mn, o = grid(drop, vol, bth, hold)
                    print(f"{drop:+.3f} {vol:.0f}x {bth:.1f} {hold//4:3d}h → days={n:4d} avg={a:+.2f}% WR={w:3.0f}% worst={mn:+.1f}% OOS={o:+.2f}%")
