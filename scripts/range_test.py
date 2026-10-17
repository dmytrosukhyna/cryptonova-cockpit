"""Range / intermediate-level trading with LIMIT orders (maker fees) — research 17.10.2026, docs/research/range.md.

Ideas tested on 18 coins, 15m bars 2023-01 → 2026-09:
  1. levels  — buy limit at an intermediate support (1h / 4h fractals, volume-profile nodes 7/30 d, round numbers, all of them),
               stop = level − m·ATR(1h), target = next level up or fixed R; mirror short at resistance (reported separately)
  2. grid    — when the range regime is on: limit buys every g % below the range middle, each sold at +g %;
               the whole grid is abandoned (stop-market) when price breaks the range low by b %
  3. mr      — range mean reversion: buy limit at the bottom 10–30 % of the N-day range, sell at the middle / top
  4. calm    — any of the above only while breadth < 0.4 (share of coins down > 3 % in 24 h), the time rule E is silent
Costs: maker 0.02 % per side for a limit entry and a limit target (0.04 % round trip); a stop or time exit is a market order:
taker 0.055 % + slippage 0.02 %. Fills: orders are computed at the close of hour h and rest during the NEXT hour only
(re-placed every hour while flat); a limit buy at P fills only if a later 15m bar trades at ≤ P·(1 − 0.01 %) (one tick through);
the fill bar can hit the stop (counted as a stop) but never the target; a target fills when a later high ≥ target·(1 + 0.01 %);
if one bar touches both stop and target → stop. Random baseline: same orders, but the entry depth (in ATR units) is taken from
a random other hour of the same coin, so the timing / volatility is identical and only the "level" information is removed.
Regimes: narrow (width below the coin's past-year median), ADX(4h) < 20, oscillating (many crossings of the N-day mean),
all with a net move ≤ ½ width; calm = breadth < 0.4. Verdict: nothing passes (docs/research/range.md).
Usage: python scripts/range_test.py [levels|grid|mr|search|search_lv|search_mr|search_grid|robust|portfolio|why|all]
(all ≈ 2 h on 1 core; features are cached next to the data as range_feat_v1.npz)"""
import sys, os, time, collections, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autopilot_v2 as ap
import ap_data

O, H, L, C, V, T, NS, START, DAY = ap.O, ap.H, ap.L, ap.C, ap.V, ap.T, ap.NS, ap.START, ap.DAY
SYMS = [str(s).replace('USDT', '') for s in ap.syms]
MAKER, TAKER, SLIP, TICK = 0.0002, 0.00055, 0.0002, 0.0001
C_TGT, C_MKT = 2 * MAKER, MAKER + TAKER + SLIP          # limit→limit round trip; limit entry → stop / market exit
NH = T // 4; H0 = START // 4                             # hourly grid; decision at the close of hour h = 15m bar 4h+3
DAYS = (T - START) / 96
YEARS = ('2023', '2024', '2025', '2026')
CACHE = os.path.join(os.environ.get('CNQ_RT_CACHE', ap_data.SRC), 'range_feat_v1.npz')     # derived features, rebuilt if missing
np.seterr(all='ignore')

# ----------------------------------------------------------------------------------------------- hourly bars + features
def hourly():
    def grp(x): return x[:, :NH * 4].reshape(NS, NH, 4)
    h1 = np.fmax.reduce(grp(H), 2); l1 = np.fmin.reduce(grp(L), 2); c1 = grp(C)[:, :, 3]; v1 = np.nansum(grp(V), 2)
    return h1, l1, c1, v1


H1, L1, C1, V1 = hourly()
YR = np.array([DAY[min(T - 1, 4 * h + 3)][:4] for h in range(NH)])


def roll(x, n, fn, minp=None):       # rolling over hours for every coin; x shape (NS, NH)
    df = pd.DataFrame(x.T)
    r = getattr(df.rolling(n, min_periods=minp or n), fn)()
    return r.to_numpy().T


def atr1h(n=14):
    pc = np.c_[np.full(NS, np.nan), C1[:, :-1]]
    tr = np.fmax(H1 - L1, np.fmax(abs(H1 - pc), abs(L1 - pc)))
    return roll(tr, n, 'mean')


def adx4h(n=14):
    m = NH // 4
    h4 = np.fmax.reduce(H1[:, :m * 4].reshape(NS, m, 4), 2); l4 = np.fmin.reduce(L1[:, :m * 4].reshape(NS, m, 4), 2)
    c4 = C1[:, :m * 4].reshape(NS, m, 4)[:, :, 3]
    out = np.full((NS, NH), np.nan)
    for k in range(NS):
        up = np.r_[0, np.diff(h4[k])]; dn = np.r_[0, -np.diff(l4[k])]
        pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
        pc = np.r_[np.nan, c4[k, :-1]]
        tr = np.fmax(h4[k] - l4[k], np.fmax(abs(h4[k] - pc), abs(l4[k] - pc)))
        s = pd.DataFrame(dict(tr=tr, p=pdm, n=ndm)).ewm(alpha=1 / n, adjust=False, ignore_na=True).mean()
        pdi = 100 * s.p / s.tr; ndi = 100 * s.n / s.tr; dx = 100 * abs(pdi - ndi) / (pdi + ndi)
        a = dx.ewm(alpha=1 / n, adjust=False, ignore_na=True).mean().to_numpy().copy()
        a[np.isnan(c4[k])] = np.nan
        # value of 4h bar q is known at the close of hour 4q+3 → used for hours 4q+3 … 4q+6
        hh = np.arange(NH); q = (hh - 3) // 4; okq = q >= 0
        out[k, okq] = a[q[okq]]
    return out


def features():
    if os.path.exists(CACHE):
        z = np.load(CACHE); return {k: z[k] for k in z.files}
    t0 = time.time(); F = {}
    F['atr'] = atr1h()
    for N in (7, 14):
        w = 24 * N
        hn = roll(H1, w, 'max'); ln = roll(L1, w, 'min')
        F[f'hn{N}'], F[f'ln{N}'] = hn, ln
        width = (hn - ln) / ln
        F[f'w{N}'] = width
        cN = np.c_[np.full((NS, w), np.nan), C1[:, :-w]]
        F[f'slope{N}'] = abs(C1 - cN) / (hn - ln)
        # narrowness vs the coin's own past year: width percentile threshold from completed days only
        for q in (0.3, 0.5):
            d = width[:, 23::24]                                       # end-of-day values
            thr = pd.DataFrame(d.T).rolling(365, min_periods=120).quantile(q).to_numpy().T
            th = np.full((NS, NH), np.nan); hh = np.arange(NH); dd = hh // 24 - 1; ok = dd >= 0
            th[:, ok] = thr[:, dd[ok]]                                  # day d threshold used during day d+1
            F[f'wq{N}_{int(q * 100)}'] = th
    F['adx'] = adx4h()
    F['br'] = ap.BR[np.minimum(4 * np.arange(NH) + 3, T - 1)][None, :].repeat(NS, 0)
    print(f'features {time.time() - t0:.0f}s', flush=True)
    F.update(levels_all(F['atr']))
    np.savez(CACHE, **F)
    return F


# ------------------------------------------------------------------------------------------------------- level families
def pivots(hi, lo, k):
    """Fractal pivots: bar m is a swing low if its low is the lowest of m−k … m+k (ties → first). Known after bar m+k."""
    n = len(hi)
    from numpy.lib.stride_tricks import sliding_window_view as sw
    if n < 2 * k + 1: return np.zeros(0), np.zeros(0)
    wl = sw(lo, 2 * k + 1); wh = sw(hi, 2 * k + 1)
    with np.errstate(invalid='ignore'):
        isl = (np.nanargmin(np.where(np.isnan(wl), np.inf, wl), 1) == k) & np.isfinite(lo[k:n - k])
        ish = (np.nanargmax(np.where(np.isnan(wh), -np.inf, wh), 1) == k) & np.isfinite(hi[k:n - k])
    m_l = np.flatnonzero(isl) + k; m_h = np.flatnonzero(ish) + k
    m = np.r_[m_l, m_h]; p = np.r_[lo[m_l], hi[m_h]]
    o = np.argsort(m, kind='stable')
    return m[o], p[o]


def build_sr(k, lv_start, lv_end, lv_price, dmins, atr):
    """lv_start/lv_end: hour range during which each level is valid. Nearest support/resistance per hour (python loop)."""
    o = np.argsort(lv_start, kind='stable'); s, e, p = lv_start[o], lv_end[o], lv_price[o]
    res = {d: (np.full(NH, np.nan), np.full(NH, np.nan)) for d in dmins}
    j0 = 0
    active_p = np.zeros(0); active_e = np.zeros(0, dtype=np.int64)
    for h in range(max(0, H0 - 24 * 31), NH):
        j1 = j0
        while j1 < len(s) and s[j1] <= h: j1 += 1
        if j1 > j0:
            active_p = np.r_[active_p, p[j0:j1]]; active_e = np.r_[active_e, e[j0:j1]]; j0 = j1
        if len(active_e) and active_e.min() <= h:
            keep = active_e > h; active_p = active_p[keep]; active_e = active_e[keep]
        c = C1[k, h]; a = atr[k, h]
        if not len(active_p) or not np.isfinite(c) or not np.isfinite(a): continue
        for d in dmins:
            b = active_p[active_p < c - d * a]; u = active_p[active_p > c + d * a]
            if len(b): res[d][0][h] = b.max()
            if len(u): res[d][1][h] = u.min()
    return res


def lv_fractal(k, tf, kk, lookback_h):
    if tf == 1:
        hi, lo = H1[k], L1[k]
    else:
        m = NH // tf
        hi = np.fmax.reduce(H1[k, :m * tf].reshape(m, tf), 1); lo = np.fmin.reduce(L1[k, :m * tf].reshape(m, tf), 1)
    mm, pp = pivots(hi, lo, kk)
    conf = (mm + kk) * tf + (tf - 1)              # decision hour at whose close the pivot is first known (bar m+k closed)
    born = mm * tf
    return conf, born + lookback_h, pp


def lv_volume(k, days, bw=0.003, every=4, thr=1.3):
    """High-volume nodes of the last `days` days (15m typical price × quote volume, log bins of bw), refreshed every 4 h."""
    st, en, pr = [], [], []
    lb = np.log1p(bw); w = days * 96
    tp = (H[k] + L[k] + C[k]) / 3; vv = np.nan_to_num(V[k])
    for h in range(max(0, H0 - 24), NH, every):
        i = 4 * h + 3; a = max(0, i - w + 1)
        x = tp[a:i + 1]; ok = np.isfinite(x) & (vv[a:i + 1] > 0)
        if ok.sum() < w // 2: continue
        b = np.floor(np.log(x[ok]) / lb).astype(np.int64); b0 = b.min()
        hist = np.bincount(b - b0, weights=vv[a:i + 1][ok])
        if len(hist) < 5: continue
        sm = np.convolve(hist, [0.25, 0.5, 0.25], 'same')
        pk = np.flatnonzero((sm[1:-1] >= sm[:-2]) & (sm[1:-1] > sm[2:]) & (sm[1:-1] >= thr * sm[sm > 0].mean())) + 1
        for j in pk:
            st.append(h); en.append(h + every); pr.append(np.exp((b0 + j + 0.5) * lb))
    return np.array(st, dtype=np.int64), np.array(en, dtype=np.int64), np.array(pr)


def nice_step(p, frac):
    raw = p * frac; e = 10 ** np.floor(np.log10(raw)); m = raw / e
    cand = np.array([1, 2, 2.5, 5, 10])
    best = cand[np.argmin(abs(np.log(m[..., None] / cand)), -1)]
    return best * e


def levels_all(atr, dmins=(0.25, 0.5, 1.0)):
    t0 = time.time(); out = {}
    fam_names = ('fr1h', 'fr4h', 'vp7', 'vp30')
    for d in dmins:
        for f in fam_names + ('rn',):
            out[f'sup_{f}_{d}'] = np.full((NS, NH), np.nan); out[f'res_{f}_{d}'] = np.full((NS, NH), np.nan)
    for k in range(NS):
        fams = {'fr1h': lv_fractal(k, 1, 3, 24 * 7), 'fr4h': lv_fractal(k, 4, 2, 24 * 30),
                'vp7': lv_volume(k, 7), 'vp30': lv_volume(k, 30)}
        for f, (s, e, p) in fams.items():
            r = build_sr(k, s, e, p, dmins, atr)
            for d in dmins:
                out[f'sup_{f}_{d}'][k], out[f'res_{f}_{d}'][k] = r[d]
        print(f'  levels {SYMS[k]} {time.time() - t0:.0f}s', flush=True)
    # round numbers ≈ 1 % apart (step 1/2/2.5/5 × 10^n closest to 1 % of the price)
    st = nice_step(np.where(np.isfinite(C1), C1, 1.0), 0.01)
    for d in dmins:
        out[f'sup_rn_{d}'] = np.floor((C1 - d * atr) / st) * st
        out[f'res_rn_{d}'] = np.ceil((C1 + d * atr) / st) * st
    return out


# --------------------------------------------------------------------------------------------------------- simulation
def exit_trade(k, j, p, sl, tp, side, hold):
    """Position opened by a limit fill at p on 15m bar j. Stop can hit on bar j (worst case), the limit target only later."""
    if side > 0:
        if L[k, j] <= sl: return j, sl / p - 1 - C_MKT, 'S'
    else:
        if H[k, j] >= sl: return j, 1 - sl / p - C_MKT, 'S'
    end = min(T - 1, j + hold)
    lo = L[k, j + 1:end + 1]; hi = H[k, j + 1:end + 1]
    if side > 0:
        s = np.flatnonzero(lo <= sl); t = np.flatnonzero(hi >= tp * (1 + TICK)) if np.isfinite(tp) else np.zeros(0, int)
    else:
        s = np.flatnonzero(hi >= sl); t = np.flatnonzero(lo <= tp * (1 - TICK)) if np.isfinite(tp) else np.zeros(0, int)
    js = s[0] if len(s) else 10 ** 9; jt = t[0] if len(t) else 10 ** 9
    if js == jt == 10 ** 9:
        while end > j and not np.isfinite(C[k, end]): end -= 1
        return end, side * (C[k, end] / p - 1) - C_MKT, 'T'
    if js <= jt:
        x = j + 1 + js; o = O[k, x]
        fill = (min(sl, o) if side > 0 else max(sl, o)) if np.isfinite(o) else sl     # a gap through the stop fills at the open
        return x, side * (fill / p - 1) - C_MKT, 'S'
    return j + 1 + jt, side * (tp / p - 1) - C_TGT, 'P'


def sim_orders(P, SL, TP, OK, side, hold=192, tick=TICK, coins=None):
    """P/SL/TP/OK: (NS, NH) arrays computed at the close of hour h. The limit order rests during hour h+1 (bars 4h+4 … 4h+7);
    if it does not fill it is re-placed with the next hour's values. One position per coin at a time."""
    tr = []
    for k in (range(NS) if coins is None else coins):
        free = 0
        for h in np.flatnonzero(OK[k]):
            if h < H0: continue
            i = 4 * h + 3
            if i < free: continue
            if i + 5 + hold >= T: break
            p = P[k, h]
            if side > 0:
                f = np.flatnonzero(L[k, i + 1:i + 5] <= p * (1 - tick))
            else:
                f = np.flatnonzero(H[k, i + 1:i + 5] >= p * (1 + tick))
            if not len(f): continue
            j = i + 1 + f[0]
            x, r, how = exit_trade(k, j, p, SL[k, h], TP[k, h], side, hold)
            tr.append((k, j, x, r, how, abs(p - SL[k, h]) / p))
            free = x + 1
    return tr


def stats(tr, label='', show=True, base=None):
    """Per-trade summary: n, trades/day, avg net %, win-rate, per-year sum (% of one position) and avg, coins positive."""
    if not tr:
        if show: print(f'{label:58} none')
        return dict(n=0, avg=0, years={y: 0 for y in YEARS}, ok=False, tpd=0)
    a = np.array([t[3] for t in tr]); ky = collections.defaultdict(list); kc = collections.defaultdict(float)
    for t in tr: ky[DAY[t[1]][:4]].append(t[3]); kc[t[0]] += t[3]
    ys = {y: float(np.sum(ky[y])) * 100 for y in YEARS}; ya = {y: (float(np.mean(ky[y])) * 100 if ky[y] else 0) for y in YEARS}
    pos = sum(1 for v in kc.values() if v > 0)
    w = a[a > 0].sum(); l = -a[a < 0].sum()
    hows = collections.Counter(t[4] for t in tr)
    r = dict(n=len(a), tpd=len(a) / DAYS, avg=a.mean() * 100, wr=(a > 0).mean(), pf=w / l if l > 0 else 9.9, years=ys, yavg=ya,
             ok=all(ys[y] > 0 for y in YEARS), coins_pos=pos, coins=len(kc), hows=dict(hows))
    if show:
        b = '' if base is None else f" | rnd {base:+.3f}%"
        print(f"{label:58} n={r['n']:6d} {r['tpd']:5.2f}/d avg {r['avg']:+.3f}% WR {r['wr'] * 100:3.0f}% PF {r['pf']:.2f} "
              f"yrs avg% {' '.join(f'{ya[y]:+.2f}' for y in YEARS)} coins+ {pos}/{len(kc)} {'ALL-YEARS+' if r['ok'] else ''}{b}", flush=True)
    return r


# ----------------------------------------------------------------------------------------------------------- regimes
_XC = {}


def crossings(N):
    """How many times the 1h close crossed its N-day mean during the last N days (many crossings = price oscillates)."""
    if N not in _XC:
        sma = roll(C1, 24 * N, 'mean'); sg = np.sign(C1 - sma); cr = np.zeros_like(C1)
        cr[:, 1:] = (sg[:, 1:] != sg[:, :-1]) & np.isfinite(sg[:, 1:]) & np.isfinite(sg[:, :-1])
        _XC[N] = roll(cr, 24 * N, 'sum')
    return _XC[N]


def regime(F, kind, N=14, q=50, slope=0.5):
    """'range' = N-day width below the coin's own past-year median (q) and net move ≤ slope·width;
    'adx' = ADX(4h) < 20 and net move ≤ slope·width; 'osc' = ≥ 7 (N=7) / 13 (N=14) crossings of the N-day mean
    (top quarter) and net move ≤ slope·width; 'calm' = breadth < 0.4; 'X+calm' = both."""
    base, _, c = kind.partition('+')
    sl = F[f'slope{N}'] <= slope
    m = {'none': np.isfinite(C1),
         'range': (F[f'w{N}'] <= F[f'wq{N}_{q}']) & sl,
         'adx': (F['adx'] < 20) & sl,
         'osc': (crossings(N) >= (9 if N == 7 else 13)) & sl,
         'calm': F['br'] < 0.4}[base]
    return m & (F['br'] < 0.4) if c == 'calm' else m


# ------------------------------------------------------------------------------------------------- 1. level strategies
def level_orders(F, fam, side, d=0.5, m=1.0, tgt='next', dmax=4.0, min_rr=0.5):
    """Long: limit buy at the nearest support ≥ d·ATR below the close (≤ dmax·ATR), stop = support − m·ATR,
    target = nearest resistance above the close (tgt='next') or entry + tgt·risk. Short = mirror."""
    atr = F['atr']
    fams = ('fr1h', 'fr4h', 'vp7', 'vp30', 'rn') if fam == 'all' else (fam,)
    if side > 0:
        lvl = np.fmax.reduce([F[f'sup_{f}_{d}'] for f in fams]); nxt = np.fmin.reduce([F[f'res_{f}_{d}'] for f in fams])
        P = lvl; SL = P - m * atr
        TP = nxt if tgt == 'next' else P + tgt * (P - SL)
        OK = np.isfinite(P) & (C1 - P <= dmax * atr) & (P < C1) & np.isfinite(TP) & (TP - P >= min_rr * (P - SL))
    else:
        lvl = np.fmin.reduce([F[f'res_{f}_{d}'] for f in fams]); nxt = np.fmax.reduce([F[f'sup_{f}_{d}'] for f in fams])
        P = lvl; SL = P + m * atr
        TP = nxt if tgt == 'next' else P - tgt * (SL - P)
        OK = np.isfinite(P) & (P - C1 <= dmax * atr) & (P > C1) & np.isfinite(TP) & (P - TP >= min_rr * (SL - P))
    return P, SL, TP, OK


def randomize(F, P, SL, TP, OK, side, seed):
    """Random baseline: same hours, same stop/target distances in ATR units, but the entry depth below (above) the close is
    the depth of a random other valid hour of the same coin — the level itself is replaced by a random price."""
    rng = np.random.default_rng(seed); atr = F['atr']
    P2, SL2, TP2 = P.copy(), SL.copy(), TP.copy()
    for k in range(NS):
        hs = np.flatnonzero(OK[k])
        if not len(hs): continue
        dep = (C1[k, hs] - P[k, hs]) / atr[k, hs]; sd = (P[k, hs] - SL[k, hs]) / atr[k, hs]; td = (TP[k, hs] - P[k, hs]) / atr[k, hs]
        pm = rng.permutation(len(hs))
        P2[k, hs] = C1[k, hs] - dep[pm] * atr[k, hs]
        SL2[k, hs] = P2[k, hs] - sd * atr[k, hs]; TP2[k, hs] = P2[k, hs] + td * atr[k, hs]
    return P2, SL2, TP2


def rnd_avg(F, P, SL, TP, OK, side, seeds=(1, 2, 3), hold=192):
    vals = []
    for s in seeds:
        P2, SL2, TP2 = randomize(F, P, SL, TP, OK, side, s)
        tr = sim_orders(P2, SL2, TP2, OK & np.isfinite(P2), side, hold)
        vals.append(np.mean([t[3] for t in tr]) * 100 if tr else 0)
    return float(np.mean(vals)), vals


def run_levels(F):
    print('\n=== 1. intermediate levels (limit entry at the level, d=0.5 ATR, stop 1 ATR below the level, hold ≤ 48 h) ===')
    res = {}
    for side in (1, -1):
        for fam in ('fr1h', 'fr4h', 'vp7', 'vp30', 'rn', 'all'):
            for tgt in ('next', 1.0, 2.0):
                for reg in ('none', 'range', 'calm', 'range+calm'):
                    P, SL, TP, OK = level_orders(F, fam, side, tgt=tgt)
                    OKr = OK & regime(F, reg)
                    tr = sim_orders(P, SL, TP, OKr, side)
                    lab = f"{'L' if side > 0 else 'S'} {fam:5} tgt={tgt!s:4} {reg}"
                    res[lab] = stats(tr, lab)
    return res


# ------------------------------------------------------------------------------------------------------- 2. range grid
def grid_episode(k, i0, M, lo, g, b, life, tick=TICK, hi=None):
    """Grid started after the close of 15m bar i0: limit buys at M(1−g), M(1−2g) … down to the range low `lo`, each lot sold
    at +g %. A buy level is (re)armed only after a close above it (so it is never a marketable order). Stop-market for the
    whole grid at lo·(1−b) (all open lots sold, taker + slippage); otherwise the grid lives `life` bars, then open lots are
    closed at market. Returns lot round trips [(k, buy_bar, exit_bar, ret, how)] and the end bar."""
    levels = []
    j = 1
    while M * (1 - g * j) >= lo * (1 - 1e-9): levels.append(M * (1 - g * j)); j += 1
    end = min(T - 1, i0 + life)
    lo_s, hi_s, cl_s = L[k, i0 + 1:end + 1], H[k, i0 + 1:end + 1], C[k, i0 + 1:end + 1]
    stop = lo * (1 - b)
    sb = np.flatnonzero(lo_s <= stop)
    jb = sb[0] if len(sb) else None                      # index (relative to i0+1) of the break bar
    jend = jb if jb is not None else len(lo_s) - 1
    if hi is not None:                                   # upside breakout (close above the range high + b): grid closed
        su = np.flatnonzero(cl_s[:jend] > hi * (1 + b))
        if len(su): jend = su[0]
    out = []
    cl_prev = np.r_[C[k, i0], cl_s]                     # close known before each bar
    for p in levels:
        cur = 0
        while cur <= jend:
            arm = np.flatnonzero(cl_prev[cur:jend + 1] > p)
            if not len(arm): break
            a = cur + arm[0]                              # order rests from bar a (close of bar a−1 was above p)
            f = np.flatnonzero(lo_s[a:jend + 1] <= p * (1 - tick))
            if not len(f): break
            jb_ = a + f[0]
            if jb is not None and jb_ == jb:              # bought on the break bar → stopped on the same bar
                out.append((k, i0 + 1 + jb_, i0 + 1 + jb_, stop / p - 1 - C_MKT, 'B')); break
            tp = p * (1 + g)
            s = np.flatnonzero(hi_s[jb_ + 1:jend] >= tp * (1 + tick)) if jend > jb_ + 1 else np.zeros(0, int)
            if len(s):
                xs = jb_ + 1 + s[0]
                out.append((k, i0 + 1 + jb_, i0 + 1 + xs, g - C_TGT, 'P')); cur = xs; continue
            if jb is not None:                            # still holding when the range breaks
                o = O[k, i0 + 1 + jb]; fill = min(stop, o) if np.isfinite(o) else stop
                out.append((k, i0 + 1 + jb_, i0 + 1 + jb, fill / p - 1 - C_MKT, 'B'))
            else:                                         # grid expired: close at market
                e = jend
                while e > jb_ and not np.isfinite(cl_s[e]): e -= 1
                out.append((k, i0 + 1 + jb_, i0 + 1 + e, cl_s[e] / p - 1 - C_MKT, 'T'))
            break
    return out, i0 + 1 + jend, len(levels), jb is not None


def run_grid_cfg(F, OK, N=14, g=0.01, b=0.02, life_d=7, min_levels=2, max_levels=12, nfix=0):
    """One grid at a time per coin; a new grid can start at any hour close when OK (regime) holds and the previous ended."""
    hn, ln = F[f'hn{N}'], F[f'ln{N}']
    lots, eps = [], []
    for k in range(NS):
        free = 0
        for h in np.flatnonzero(OK[k]):
            if h < H0: continue
            i = 4 * h + 3
            if i < free or i + 5 >= T: continue
            M = (hn[k, h] + ln[k, h]) / 2; lo = ln[k, h]
            if not (np.isfinite(M) and np.isfinite(lo) and M > lo): continue
            if nfix:                                   # nfix levels evenly between the middle and the range low
                gg = (1 - lo / M) / nfix; nlev = nfix
            else:                                      # fixed step g, at most max_levels levels below the middle
                gg = g; nlev = int(np.floor((1 - lo / M) / g + 1e-9))
            if nlev < min_levels: continue
            lo_eff = M * (1 - gg * min(nlev, max_levels))
            tr, e, nl, broke = grid_episode(k, i, M, lo_eff if nlev > max_levels else lo, gg, b, life_d * 96, hi=hn[k, h])
            lots += tr
            pnl = sum(t[3] for t in tr)
            eps.append(dict(k=k, i=i + 1, x=max(e, i + 1), ret=pnl / max(nl, 1), nl=nl, broke=broke, nlots=len(tr), pnl=pnl,
                            brk=sum(t[3] for t in tr if t[4] == 'B')))
            free = e + 1
    return lots, eps


def grid_stats(lots, eps, label):
    if not eps:
        print(f'{label:58} none'); return dict(ok=False, years={y: 0 for y in YEARS}, tpd=0, n=0)
    ky = collections.defaultdict(float); kb = collections.defaultdict(float)
    for e in eps: ky[DAY[e['i']][:4]] += e['ret'] * 100; kb[DAY[e['i']][:4]] += e['brk'] / max(e['nl'], 1) * 100
    tot = sum(e['ret'] for e in eps) * 100; brk = sum(e['brk'] / max(e['nl'], 1) for e in eps) * 100
    nb = sum(e['broke'] for e in eps)
    ok = all(ky[y] > 0 for y in YEARS)
    kc = collections.defaultdict(float)
    for e in eps: kc[e['k']] += e['ret']
    a = np.array([e['ret'] for e in eps]) * 100
    print(f"{label:58} grids={len(eps):5d} lots/d {len(lots) / DAYS:5.2f} avg/grid {a.mean():+.3f}% "
          f"breaks {nb:4d} ({nb / len(eps) * 100:2.0f}%) break-loss {brk:+7.1f}% total {tot:+7.1f}% "
          f"yrs {' '.join(f'{ky[y]:+6.1f}' for y in YEARS)} coins+ {sum(v > 0 for v in kc.values())}/{len(kc)} "
          f"{'ALL-YEARS+' if ok else ''}", flush=True)
    return dict(ok=ok, years=dict(ky), tpd=len(lots) / DAYS, n=len(eps), avg=a.mean(), tot=tot, brk=brk, nb=nb)


def run_grid(F):
    print('\n=== 2. range grid (returns in % of the capital reserved for the whole grid; break-loss = lots sold by the stop) ===')
    res = {}
    for reg in ('none', 'range', 'range+calm'):
        for N in (7, 14):
            OK = regime(F, reg, N=N)
            for g in (0.005, 0.01, 0.02, 'n4', 'n8'):
                for b in (0.01, 0.03):
                    nf = int(g[1:]) if isinstance(g, str) else 0
                    lots, eps = run_grid_cfg(F, OK, N=N, g=g if not nf else 0, b=b, life_d=N, nfix=nf)
                    gl = f"{g * 100:.1f}%" if not nf else f"{nf} lv"
                    lab = f"grid {reg:10} N={N:2d} g={gl:5} b={b * 100:.0f}% life={N}d"
                    res[lab] = grid_stats(lots, eps, lab)
    return res


# ---------------------------------------------------------------------------------------------- 3. range mean reversion
def mr_orders(F, side, N=14, f=0.15, t=0.5, s=0.15):
    """Long: limit buy at low + f·width of the N-day range, target low + t·width, stop low − s·width. Short = mirror at the top."""
    hn, ln = F[f'hn{N}'], F[f'ln{N}']; W = hn - ln
    if side > 0:
        P = ln + f * W; TP = ln + t * W; SL = ln - s * W; OK = np.isfinite(P) & (C1 > P * (1 + 0.001))
    else:
        P = hn - f * W; TP = hn - t * W; SL = hn + s * W; OK = np.isfinite(P) & (C1 < P * (1 - 0.001))
    return P, SL, TP, OK


def run_mr(F):
    print('\n=== 3. range mean reversion with limits (hold ≤ 72 h) ===')
    res = {}
    for side in (1, -1):
        for reg in ('range', 'range+calm', 'none'):
            for N in (7, 14):
                for f in (0.1, 0.2):
                    for t in (0.5, 0.8):
                        P, SL, TP, OK = mr_orders(F, side, N, f, t)
                        tr = sim_orders(P, SL, TP, OK & regime(F, reg, N=N), side, hold=288)
                        lab = f"MR {'L' if side > 0 else 'S'} {reg:10} N={N:2d} f={f} t={t}"
                        res[lab] = stats(tr, lab)
    return res


# ------------------------------------------------------------------------------------------- 4. broad parameter search
REGS = ('none', 'range', 'adx', 'osc', 'calm')


def run_search(F, out_json=None, parts=('lv', 'mr', 'grid')):
    """Every family × parameters × regime; prints one line each. The best lines are re-checked against the random baseline
    and neighbouring parameters in run_robust()."""
    import json
    rows = []
    def keep(fam, prm, r):
        rows.append(dict(fam=fam, prm=prm, n=r['n'], tpd=r.get('tpd', 0), avg=r.get('avg', 0), years=r['years'], ok=r['ok']))
    print('\n=== 4a. levels search (long; short below) ===')
    for side in ((1, -1) if 'lv' in parts else ()):
        for fam in ('all', 'fr1h', 'fr4h', 'vp30'):
            for d in (0.25, 0.5, 1.0):
                for m in (0.5, 1.0, 2.0):
                    for tgt in (0.5, 1.0, 'next'):
                        P, SL, TP, OK = level_orders(F, fam, side, d=d, m=m, tgt=tgt)
                        for reg in REGS if side > 0 else ('none', 'calm'):
                            tr = sim_orders(P, SL, TP, OK & regime(F, reg), side)
                            lab = f"LV {'L' if side > 0 else 'S'} {fam:4} d={d} m={m} tgt={tgt} {reg}"
                            keep('lv', dict(side=side, fam=fam, d=d, m=m, tgt=tgt, reg=reg), stats(tr, lab))
    print('\n=== 4b. range mean-reversion search ===')
    for side in ((1, -1) if 'mr' in parts else ()):
        for N in (7, 14):
            for f in (0.1, 0.2, 0.3):
                for t in (0.5, 0.8):
                    for s_ in (0.1, 0.25):
                        P, SL, TP, OK = mr_orders(F, side, N, f, t, s_)
                        for reg in REGS:
                            tr = sim_orders(P, SL, TP, OK & regime(F, reg, N=N), side, hold=288)
                            lab = f"MR {'L' if side > 0 else 'S'} N={N} f={f} t={t} s={s_} {reg}"
                            keep('mr', dict(side=side, N=N, f=f, t=t, s=s_, reg=reg), stats(tr, lab))
    print('\n=== 4c. grid search ===')
    for reg in (REGS if 'grid' in parts else ()):
        for N in (7, 14):
            OK = regime(F, reg, N=N)
            for g in (0.01, 0.02, 'n4'):
                for b in (0.01, 0.03, 0.10, 1.0):
                    for life in (N, 30):
                        nf = int(g[1:]) if isinstance(g, str) else 0
                        lots, eps = run_grid_cfg(F, OK, N=N, g=0 if nf else g, b=b, life_d=life, nfix=nf)
                        lab = f"GR {reg:5} N={N} g={g} b={b} life={life}"
                        r = grid_stats(lots, eps, lab)
                        keep('grid', dict(reg=reg, N=N, g=g, b=b, life=life), r)
    if out_json:
        json.dump(rows, open(out_json, 'w'))
    return rows


# ---------------------------------------------------------------------------- 5. best candidates: baseline, neighbours
def cfg_trades(F, kind, prm, tick=TICK):
    if kind == 'lv':
        P, SL, TP, OK = level_orders(F, prm['fam'], prm['side'], d=prm['d'], m=prm['m'], tgt=prm['tgt'])
        return sim_orders(P, SL, TP, OK & regime(F, prm['reg']), prm['side'], tick=tick), (P, SL, TP, OK & regime(F, prm['reg']))
    P, SL, TP, OK = mr_orders(F, prm['side'], prm['N'], prm['f'], prm['t'], prm['s'])
    OK = OK & regime(F, prm['reg'], N=prm['N'])
    return sim_orders(P, SL, TP, OK, prm['side'], hold=288, tick=tick), (P, SL, TP, OK)


GRIDS = dict(lv=dict(d=(0.25, 0.5, 1.0), m=(0.5, 1.0, 2.0), tgt=(0.5, 1.0, 'next')),
             mr=dict(N=(7, 14), f=(0.1, 0.2, 0.3), t=(0.5, 0.8), s=(0.1, 0.25)))


def per_coin(tr):
    kc = collections.defaultdict(list)
    for t in tr: kc[t[0]].append(t[3])
    return ' '.join(f"{SYMS[k]}{np.mean(v) * 100:+.2f}" for k, v in sorted(kc.items()))


def run_robust(F, best):
    """best: list of (kind, prm) — the strongest lines of the search. For each: random baseline (3 seeds), one-step
    neighbours of every parameter, fill strictness (touch / 5 ticks), zero maker fee, per-coin averages."""
    print('\n=== 5. best candidates: random baseline, neighbours, fill strictness, per coin ===')
    for kind, prm in best:
        tr, (P, SL, TP, OK) = cfg_trades(F, kind, prm)
        ra, vals = rnd_avg(F, P, SL, TP, OK, prm['side'], hold=192 if kind == 'lv' else 288)
        r = stats(tr, f"{kind} {prm}", base=ra)
        print(f"   random seeds avg %: {' '.join(f'{v:+.3f}' for v in vals)}")
        print(f"   per coin avg %: {per_coin(tr)}")
        for tk in (0.0, 0.0005):
            t2, _ = cfg_trades(F, kind, prm, tick=tk); stats(t2, f"   fill tick {tk * 100:.2f}%")
        a = np.array([t[3] + (C_TGT if t[4] == 'P' else C_MKT) - (0 if t[4] == 'P' else TAKER + SLIP) for t in tr])
        print(f"   with zero maker fee: avg {a.mean() * 100:+.3f}%   gross (no costs at all): "
              f"{np.mean([t[3] + (C_TGT if t[4] == 'P' else C_MKT) for t in tr]) * 100:+.3f}%")
        for key, vals_ in GRIDS[kind].items():
            for v in vals_:
                if v == prm[key]: continue
                q = dict(prm); q[key] = v
                t2, _ = cfg_trades(F, kind, q); stats(t2, f"   {key}={v}")


def run_grid_robust(F, prm):
    print(f"\n=== 5g. grid {prm}: neighbours and random start (no regime) ===")
    def one(q, lab):
        nf = int(q['g'][1:]) if isinstance(q['g'], str) else 0
        lots, eps = run_grid_cfg(F, regime(F, q['reg'], N=q['N']), N=q['N'], g=0 if nf else q['g'], b=q['b'], life_d=q['life'], nfix=nf)
        return grid_stats(lots, eps, lab), lots, eps
    _, lots, eps = one(prm, 'chosen')
    kc = collections.defaultdict(float)
    for e in eps: kc[e['k']] += e['ret'] * 100
    print('   per coin total %:', ' '.join(f"{SYMS[k]}{v:+.0f}" for k, v in sorted(kc.items())))
    for key, vals in dict(N=(7, 14), g=(0.01, 0.02, 'n4'), b=(0.01, 0.03, 0.10, 1.0), life=(7, 14, 30), reg=REGS).items():
        for v in vals:
            if v == prm[key]: continue
            q = dict(prm); q[key] = v; one(q, f"   {key}={v}")
    return lots, eps


# ------------------------------------------------------------------------------------------------------- 6. portfolio
def portfolio(cands_R, r_share, r_open, label='', base_r=None):
    import pickle
    pk = os.path.join(os.path.dirname(CACHE), 'range_base_cands.pkl')
    if os.path.exists(pk):
        base = pickle.load(open(pk, 'rb'))
    else:
        base = ap.cand_B() + ap.cand_E(0.4) + ap.cand_F() + ap.cand_D(); pickle.dump(base, open(pk, 'wb'))
    p = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40, d_share=0.15, d_open=3)
    rules = ('B', 'E', 'F', 'D') + (('R',) if r_share > 0 else ())
    eq, tr, cu = ap.simulate(base + cands_R, rules=rules, r_share=r_share, r_open=r_open, **p)
    r, _ = ap.report(eq, tr, cu)
    better = '' if base_r is None else ('  every year better' if all(r['years'].get(y, 0) > base_r['years'].get(y, 0) for y in YEARS) else '')
    ys = ' '.join(f"{y}:{r['years'].get(y, 0):>7,}" for y in YEARS)
    print(f"{label:46} final ${r['final']:>9,} CAGR {r['cagr'] * 100:+6.1f}% maxDD {r['maxdd'] * 100:6.1f}% n={r['n']:5d} "
          f"years {ys} R=${r['rules'].get('R', 0):,}{better}", flush=True)
    return r


def run_portfolio(F, best, grid_prm=None):
    """«норма + D» with and without rule R (share of equity per trade, own cap, does not block other rules)."""
    print('\n=== 6. portfolio: «норма + D» + rule R (r_share of equity per trade, at most r_open at once) ===')
    base = portfolio([], 0, 0, 'норма + D (no R)')
    sets = [(f"{kind} {prm['side']:+d} {prm.get('fam', '')}{prm.get('N', '')} {prm['reg']}", to_cands(cfg_trades(F, kind, prm)[0]))
            for kind, prm in best]
    if grid_prm:
        nf = int(grid_prm['g'][1:]) if isinstance(grid_prm['g'], str) else 0
        _, eps = run_grid_cfg(F, regime(F, grid_prm['reg'], N=grid_prm['N']), N=grid_prm['N'], g=0 if nf else grid_prm['g'],
                              b=grid_prm['b'], life_d=grid_prm['life'], nfix=nf)
        sets.append((f"grid {grid_prm['reg']} N={grid_prm['N']}", [dict(rule='R', k=e['k'], i=e['i'], x=e['x'], ret=e['ret'], sf=0.05) for e in eps]))
    for lab, cands in sets:
        for rs, ro in ((0.05, 3), (0.10, 3), (0.10, 6), (0.20, 6)):
            portfolio(cands, rs, ro, f"+ {lab} {rs:.2f}×{ro}", base_r=base)
    return base


def to_cands(tr):
    return [dict(rule='R', k=t[0], i=t[1], x=t[2], ret=t[3], sf=max(t[5], 1e-4)) for t in tr]


# --------------------------------------------------------------------------------------------------------- 7. why
def run_why(F):
    """(1) a narrow & flat range is usually followed by a WIDER one (compression → breakout); (2) a market entry at the same
    hours with a symmetric 1-ATR stop/target is ≈ 0 before costs (engine sanity), while the limit entry at a level is slightly
    negative before costs — the fill itself is adverse (it fills when price pushes through)."""
    print('\n=== 7. why ===')
    sl = slice(H0, None)
    hi = roll(H1, 168, 'max'); lo = roll(L1, 168, 'min')
    fr = np.full_like(C1, np.nan); fr[:, :-168] = ((hi - lo) / lo)[:, 168:]
    ratio = (fr / F['w7'])[:, sl]
    for reg in ('range', 'adx', 'osc'):
        m = regime(F, reg, N=14)[:, sl]
        print(f"next-7-day range ÷ last-7-day range: after '{reg}' {np.nanmedian(ratio[m]):.2f}, other hours {np.nanmedian(ratio[~m]):.2f}")
    P, SL, TP, OK = level_orders(F, 'all', 1, tgt=1.0)
    tr = sim_orders(P, SL, TP, OK, 1)
    g = np.mean([t[3] + (C_TGT if t[4] == 'P' else C_MKT) for t in tr]) * 100
    res = []
    for k in range(NS):
        free = 0
        for h in np.flatnonzero(OK[k]):
            i = 4 * h + 3
            if h < H0 or i < free or i + 200 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; x, r = ap.walk(k, i + 1, 1, e - F['atr'][k, h], e + F['atr'][k, h], 192); res.append(r); free = x + 1
    print(f"limit buy at level (all, d=0.5, stop 1 ATR, 1R) gross {g:+.3f}% per trade; market entry same hours, ±1 ATR, gross "
          f"{np.mean(res) * 100:+.3f}% ({len(res)} trades)")


BEST = [('lv', dict(side=1, fam='all', d=0.25, m=2.0, tgt=0.5, reg='calm')),     # most trades in calm markets
        ('lv', dict(side=1, fam='vp30', d=1.0, m=2.0, tgt=0.5, reg='none')),     # best level line of 540
        ('mr', dict(side=1, N=14, f=0.3, t=0.8, s=0.25, reg='none')),            # best mean-reversion line (no range filter)
        ('mr', dict(side=1, N=14, f=0.3, t=0.8, s=0.25, reg='calm'))]
BEST_GRIDS = [dict(reg='none', N=7, g='n4', b=1.0, life=30),                       # best grid total (no stop, 30 days)
              dict(reg='osc', N=14, g=0.02, b=1.0, life=30)]                       # best grid with a range filter


if __name__ == '__main__':
    F = features()
    what = sys.argv[1:] or ['all']
    if 'levels' in what or 'all' in what: run_levels(F)
    if 'grid' in what or 'all' in what: run_grid(F)
    if 'mr' in what or 'all' in what: run_mr(F)
    if 'search' in what or 'all' in what: run_search(F, os.path.join(os.path.dirname(CACHE), 'range_search.json'))
    for part in ('lv', 'mr', 'grid'):
        if f'search_{part}' in what: run_search(F, os.path.join(os.path.dirname(CACHE), f'range_search_{part}.json'), parts=(part,))
    if 'robust' in what or 'all' in what:
        run_robust(F, BEST)
        for gp in BEST_GRIDS: run_grid_robust(F, gp)
    if 'portfolio' in what or 'all' in what:
        run_portfolio(F, BEST[:1] + BEST[2:], grid_prm=BEST_GRIDS[0])
        run_portfolio(F, [], grid_prm=BEST_GRIDS[1])
    if 'why' in what or 'all' in what: run_why(F)
