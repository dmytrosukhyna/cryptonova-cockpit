"""Unused derivative fields as entry signals — 18 coins, 15m, real (non-imputed) metrics 2023-01 → 2026-09, cost 0.14 % round trip.
Fields: long/short liquidations, spot vs futures CVD, top-trader long/short ratio (+ the panel's exact advice rules), taker
buy/sell volume ratio, perp-vs-index basis. Every signal: entry at the next 15m open, time exit after 4/8/24 h, catastrophe stop
−12 %, no overlapping trades on one coin. Baseline = random entry on the same coin, same year, same side, same hold (the exact
mean of all real bars of that coin-year, no sampling noise). "Typical level" thresholds use only the PRIOR 30 days (updated daily).
Pass rule: after costs positive in each of 2023/24/25/26, better than baseline in each year, day-clustered t ≥ 2, neighbours too.
Sections: bef liq cvd top panel taker basis deep portfolio  (default: all but deep/portfolio; portfolio runs the deep candidates)
Usage: python scripts/new_signals_test.py [section ...]      e.g.  python scripts/new_signals_test.py top panel
docs/research/new_signals.md"""
import sys, os, collections, warnings, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as AP
from ap_data import SRC

np.seterr(all='ignore'); warnings.filterwarnings('ignore', category=RuntimeWarning); warnings.filterwarnings('ignore', category=DeprecationWarning)
A, times, NS, T, START, COST = AP.A, AP.times, AP.NS, AP.T, AP.START, AP.COST
O, H, L, C, V, BR = AP.O, AP.H, AP.L, AP.C, AP.V, AP.BR
syms = [str(s).replace('USDT', '') for s in AP.syms]
YEARS = (2023, 2024, 2025, 2026)
YEAR = np.array([int(d[:4]) for d in AP.DAY]); YI = np.clip(YEAR - 2023, 0, 3)
DAYN = (times - times[0]) // 86_400_000
NDAYS = int(DAYN[-1] - DAYN[START] + 1)
IMP = A['is_imputed_metrics']
REAL = (IMP == 0) & np.isfinite(C) & (np.arange(T) >= START)[None, :]
REAL[:, -100:] = False
CALM = BR < 0.4                       # E (breadth ≥ 0.4) cannot fire
PANEL6 = [syms.index(c) for c in ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB')]
STOP = 0.12


def load_extra(cols=('ls_ratio_global', 'top_account_ratio')):
    """Extra CSV columns (not in ap_data) aligned to the same grid; cached as extra18.npz next to the CSVs."""
    cache = os.path.join(SRC, 'extra18.npz')
    if os.path.exists(cache):
        z = np.load(cache)
        if all(c in z for c in cols): return {c: z[c] for c in cols}
    import pandas as pd
    pos = {int(t): i for i, t in enumerate(times)}; out = {c: np.full((NS, T), np.nan) for c in cols}
    for k, s in enumerate(AP.syms):
        d = pd.read_csv(os.path.join(SRC, f'{s}_15m.csv'), usecols=['open_time_ms', *cols])
        ii = np.array([pos.get(int(t), -1) for t in d['open_time_ms']]); ok = ii >= 0
        for c in cols: out[c][k, ii[ok]] = pd.to_numeric(d[c], errors='coerce').values[ok]
    np.savez(cache, **out); return out


# ---------- helpers ----------
def rsum(x, n):
    """sum over the last n bars including the current one (NaN counted as 0)."""
    c = np.cumsum(np.nan_to_num(x), 1); o = np.full(c.shape, np.nan); o[:, n - 1] = c[:, n - 1]; o[:, n:] = c[:, n:] - c[:, :-n]; return o


def rmean(x, n):
    """mean over the last n bars; NaN unless all n values are finite."""
    ok = rsum(np.isfinite(x).astype(float), n); m = rsum(x, n) / n; m[ok < n] = np.nan; return m


def chg(x, n):
    o = np.full(x.shape, np.nan); o[:, n:] = x[:, n:] / x[:, :-n] - 1; return o


def diff(x, n):
    o = np.full(x.shape, np.nan); o[:, n:] = x[:, n:] - x[:, :-n]; return o


S0 = START - 96 * 32


def trail(x, kind, qs=(0.5,), win=2880, step=96):
    """Statistic of the PRIOR `win` bars, refreshed every `step` bars. kind: 'q' quantiles → [len(qs),NS,T];
    'ms' mean,std → [2,NS,T]; 'rank' percentile rank of the current value inside the prior window → [NS,T]."""
    if kind == 'rank': out = np.full((NS, T), np.nan)
    else: out = np.full((len(qs) if kind == 'q' else 2, NS, T), np.nan)
    for k in range(NS):
        for s in range(S0, T, step):
            w = x[k, s - win:s]; w = w[np.isfinite(w)]
            if len(w) < win // 2: continue
            if kind == 'q': out[:, k, s:s + step] = np.quantile(w, qs)[:, None]
            elif kind == 'ms': out[0, k, s:s + step] = w.mean(); out[1, k, s:s + step] = w.std()
            else:
                w.sort(); v = x[k, s:s + step]; r = np.searchsorted(w, v, 'left') / len(w); r[~np.isfinite(v)] = np.nan
                out[k, s:s + step] = r
    return out


def fwd(side, h):
    """net return of a trade entered at O[i+1], exit C[i+h], catastrophe stop 12 % checked on bars i+1..i+h."""
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full((NS, T), np.nan)
    for k in range(NS):
        e = np.full(T, np.nan); e[:-1] = O[k, 1:]
        ex = np.full(T, np.nan); ex[:-h] = C[k, h:]
        if side > 0:
            ext = np.full(T, np.nan); ext[:T - h] = np.nanmin(sw(L[k, 1:], h), 1)[:T - h]
            hit = ext <= e * (1 - STOP); g = ex / e - 1
        else:
            ext = np.full(T, np.nan); ext[:T - h] = np.nanmax(sw(H[k, 1:], h), 1)[:T - h]
            hit = ext >= e * (1 + STOP); g = 1 - ex / e
        out[k] = np.where(hit, -STOP, g) - COST
    return out


HOLDS = (16, 32, 96)
FWD = {(s, h): fwd(s, h) for s in (1, -1) for h in HOLDS}
BASE = {}
for key, f in FWD.items():
    b = np.full((NS, 4), np.nan)
    for k in range(NS):
        for y in range(4):
            m = REAL[k] & (YI == y) & np.isfinite(f[k]); b[k, y] = f[k, m].mean()
    BASE[key] = b

BEF = None


def bef():
    global BEF
    if BEF is None:
        BEF = AP.cand_B() + AP.cand_E(br=0.4) + AP.cand_F()
    return BEF


def bef_index():
    """per coin: sorted entry bars of all B/E/F candidates; plus the set of UTC days with any B/E/F entry."""
    per = collections.defaultdict(list); days = set()
    for c in bef(): per[c['k']].append(c['i']); days.add(int(DAYN[c['i']]))
    return {k: np.array(sorted(v)) for k, v in per.items()}, days


RES = []


def events(mask, gap, coins=None):
    m = mask & REAL
    ks, iss = [], []
    for k in (range(NS) if coins is None else coins):
        idx = np.where(m[k])[0]; p = 0; sel = []
        while p < len(idx):
            sel.append(idx[p]); p = np.searchsorted(idx, idx[p] + gap)
        ks += [k] * len(sel); iss += sel
    return np.array(ks, int), np.array(iss, int)


def test(name, mask, side=1, holds=HOLDS, coins=None, fam='', show=True):
    rows = []
    for h in holds:
        f = FWD[(side, h)]
        k, i = events(mask & np.isfinite(f), max(h, 16), coins)
        if len(k) < 20:
            if show: print(f"  {name:52} {h // 4:2d}h n={len(k)} — мало")
            break
        r = f[k, i]; b = BASE[(side, h)][k, YI[i]]; ex = r - b; d = DAYN[i]
        ud, inv = np.unique(d, return_inverse=True); dex = np.bincount(inv, ex) / np.bincount(inv)
        t = dex.mean() / (dex.std(ddof=1) / np.sqrt(len(dex))) if len(dex) > 2 else 0
        yr = {y: (r[YI[i] == y - 2023].mean() if (YI[i] == y - 2023).sum() >= 5 else np.nan) for y in YEARS}
        ye = {y: (ex[YI[i] == y - 2023].mean() if (YI[i] == y - 2023).sum() >= 5 else np.nan) for y in YEARS}
        yn = {y: int((YI[i] == y - 2023).sum()) for y in YEARS}
        calm = CALM[i]
        row = dict(name=name, fam=fam, side=side, h=h, n=len(r), per_day=len(r) / NDAYS, avg=r.mean(), wr=(r > 0).mean(), base=b.mean(),
                   edge=ex.mean(), dedge=dex.mean(), t=t, years=yr, yedge=ye, yn=yn, calm_share=calm.mean(), calm_avg=r[calm].mean() if calm.sum() >= 5 else np.nan,
                   calm_edge=ex[calm].mean() if calm.sum() >= 5 else np.nan, k=k, i=i, r=r)
        row['ok'] = bool(len(r) >= 60 and all(np.isfinite(yr[y]) and yr[y] > 0 and ye[y] > 0 for y in YEARS) and t >= 2 and dex.mean() > 0)
        rows.append(row); RES.append(row)
        if show:
            ys = ' '.join(f"{yr[y] * 100:+5.2f}({ye[y] * 100:+5.2f})" if np.isfinite(yr[y]) else '   —   (  —  )' for y in YEARS)
            print(f"  {name:52} {h // 4:2d}h n={len(r):5d} {len(r) / NDAYS:5.2f}/д avg={r.mean() * 100:+6.2f}% WR={(r > 0).mean() * 100:3.0f}% "
                  f"база={b.mean() * 100:+5.2f} край={ex.mean() * 100:+5.2f} по дням={dex.mean() * 100:+5.2f} t={t:+4.1f} | {ys} | тихо {calm.mean() * 100:3.0f}% "
                  f"край {row['calm_edge'] * 100 if np.isfinite(row['calm_edge']) else float('nan'):+5.2f}{'  ✔' if row['ok'] else ''}", flush=True)
    return rows


def header(t):
    print(f"\n=== {t}\n  {'вариант':52} hold n  сделок/день  средняя  WR  база(случайный вход)  край=средняя−база  по дням=край, сначала усреднённый внутри дня  t(по дням) | "
          f"по годам: средняя(край) 2023 2024 2025 2026 | доля тихих (breadth<0.4) и край в них", flush=True)


def baseline_table():
    header('Случайный вход (среднее по всем реальным барам той же монеты и года)')
    for side in (1, -1):
        for h in HOLDS:
            b = BASE[(side, h)]
            print(f"  {'лонг' if side > 0 else 'шорт'} {h // 4:2d}h: " + ' '.join(f"{y}:{np.nanmean(b[:, y - 2023]) * 100:+.2f}%" for y in YEARS))


# ---------- 1. liquidations ----------
def sec_liq():
    LL, SL = np.abs(A['long_liq_usd']), np.abs(A['short_liq_usd'])
    LL1, SL1 = rsum(LL, 4), rsum(SL, 4)
    ql = trail(LL1, 'q', (0.5, 0.99, 0.995)); qs = trail(SL1, 'q', (0.5, 0.99, 0.995))
    lr, sr = LL1 / ql[0], SL1 / qs[0]
    # data quality: share of per-bar long-liq "floor" (10th pct / median); ≥0.5 means the series sits on a constant base
    fl = {syms[k]: np.nanpercentile(LL[k, REAL[k]], 10) / np.nanpercentile(LL[k, REAL[k]], 50) for k in range(NS)}
    clean = [k for k in range(NS) if fl[syms[k]] < 0.5]
    print("\nликвидации: доля «пола» (10-й перцентиль / медиана за бар) — " + ', '.join(f"{s} {v:.2f}" for s, v in fl.items()))
    print("  монеты с живыми данными (пол < 0.5):", ', '.join(syms[k] for k in clean))
    header('1a. Всплеск ликвидаций ЛОНГОВ за 1 ч ≫ медианы за 30 дней → лонг')
    for m in (3, 5, 10, 20):
        for fl_usd in (0, 1e6, 5e6):
            test(f"LL1h ≥{m}× медианы, ≥${fl_usd / 1e6:.0f}M", (lr >= m) & (LL1 >= fl_usd), 1, fam=f'liqL_{m}_{fl_usd:.0e}')
    for q, j in ((0.99, 1), (0.995, 2)):
        test(f"LL1h ≥ {q * 100:.1f}-го перцентиля 30 дн", LL1 >= ql[j], 1, fam=f'liqLq_{q}')
    test("LL1h ≥5× медианы, только живые монеты", lr >= 5, 1, coins=clean, fam='liqL_clean_5')
    test("LL1h ≥10× медианы, только живые монеты", lr >= 10, 1, coins=clean, fam='liqL_clean_10')
    test("LL1h ≥5× и тихий рынок (breadth<0.4)", (lr >= 5) & CALM[None, :], 1, fam='liqL_calm_5')
    test("LL1h ≥10× и тихий рынок (breadth<0.4)", (lr >= 10) & CALM[None, :], 1, fam='liqL_calm_10')
    test("LL1h ≥99-го перц. и тихий рынок", (LL1 >= ql[1]) & CALM[None, :], 1, fam='liqLq_calm')
    header('1b. Всплеск ликвидаций ШОРТОВ (шорт-сквиз): против движения (шорт) и по движению (лонг)')
    for m in (5, 10, 20):
        test(f"SL1h ≥{m}× медианы → шорт", sr >= m, -1, fam=f'sqz_short_{m}')
        test(f"SL1h ≥{m}× медианы → лонг", sr >= m, 1, fam=f'sqz_long_{m}')
    test("SL1h ≥99-го перц. → шорт", SL1 >= qs[1], -1, fam='sqzq_short')
    test("SL1h ≥99-го перц. → лонг", SL1 >= qs[1], 1, fam='sqzq_long')


# ---------- 2. spot vs futures CVD ----------
def sec_cvd():
    sp, fu = A['spot_cvd_15m'], A['future_cvd_15m']
    for W, lab in ((16, '4ч'), (96, '24ч')):
        s, f = rsum(sp, W), rsum(fu, W); rw = chg(C, W)
        ms, mf = trail(s, 'ms'), trail(f, 'ms')
        zs, zf = (s - ms[0]) / ms[1], (f - mf[0]) / mf[1]
        header(f'2. Расхождение CVD спот/фьючерсы за {lab} (z к своему 30-дн. уровню)')
        for z in (0.0, 0.5, 1.0, 1.5, 2.0):
            test(f"{lab}: спот z≥+{z}, фьюч z≤−{z}, цена ≤+0.5% → лонг", (zs >= z) & (zf <= -z) & (rw <= 0.005), 1, fam=f'cvd{W}_L_{z}')
        for z in (0.5, 1.0, 1.5):
            test(f"{lab}: спот z≥+{z}, фьюч z≤−{z}, любая цена → лонг", (zs >= z) & (zf <= -z), 1, fam=f'cvd{W}_Lany_{z}')
            test(f"{lab}: спот z≤−{z}, фьюч z≥+{z}, цена ≥−0.5% → шорт", (zs <= -z) & (zf >= z) & (rw >= -0.005), -1, fam=f'cvd{W}_S_{z}')
            test(f"{lab}: спот z≤−{z}, фьюч z≥+{z}, цена ≥−0.5% → лонг", (zs <= -z) & (zf >= z) & (rw >= -0.005), 1, fam=f'cvd{W}_SL_{z}')
        for z in (1.0, 1.5):
            test(f"{lab}: спот z≥+{z}, фьюч z≤−{z}, цена ≤+0.5%, тихо", (zs >= z) & (zf <= -z) & (rw <= 0.005) & CALM[None, :], 1, fam=f'cvd{W}_Lcalm_{z}')


# ---------- 3. top-trader long/short ratio ----------
def decile_table(sig, label, h=96, step=96, coins=None):
    """samples once per day (00:00 UTC) per coin; next-h return minus coin-year baseline, by decile of `sig` (pooled cut points)."""
    f = FWD[(1, h)]; ks = range(NS) if coins is None else coins
    ii = np.arange(START, T - h - 2, step)
    K = np.repeat(np.array(list(ks)), len(ii)); I = np.tile(ii, len(list(ks)))
    s = sig[K, I]; r = f[K, I]; ok = np.isfinite(s) & np.isfinite(r) & REAL[K, I]
    K, I, s, r = K[ok], I[ok], s[ok], r[ok]; ex = r - BASE[(1, h)][K, YI[I]]
    cuts = np.quantile(s, np.linspace(0, 1, 11)[1:-1]); dec = np.searchsorted(cuts, s, 'right')
    print(f"\n  {label}: следующие {h // 4} ч, лонг, минус случайный вход; n={len(s)} (раз в сутки на монету)")
    print(f"  {'дециль':8} {'порог от':>9} {'n':>5} {'край':>7} " + ' '.join(f"{y:>7}" for y in YEARS))
    for d in range(10):
        m = dec == d; lo = s[m].min()
        print(f"  {d + 1:8d} {lo:9.3f} {m.sum():5d} {ex[m].mean() * 100:+6.2f}% " + ' '.join(f"{ex[m & (YI[I] == y - 2023)].mean() * 100:+6.2f}%" for y in YEARS))
    ics = []
    for y in YEARS:
        m = YI[I] == y - 2023
        a, b = np.argsort(np.argsort(s[m])), np.argsort(np.argsort(ex[m])); ics.append(np.corrcoef(a, b)[0, 1])
    print("  ранговая корреляция с будущей доходностью (IC) по годам: " + ' '.join(f"{y}:{v:+.3f}" for y, v in zip(YEARS, ics)))
    return ics


def sec_top():
    ex = load_extra()
    for fld, src in (('ls_ratio_top', A['ls_ratio_top']), ('top_account_ratio', ex['top_account_ratio'])):
        r = src.copy(); r[IMP != 0] = np.nan
        Ls = r / (1 + r)                                   # top-trader long share (what the panel shows)
        rk = trail(r, 'rank'); d24 = diff(Ls, 96); d24[~np.isfinite(diff(r, 96))] = np.nan
        md = trail(d24, 'ms'); zd = (d24 - md[0]) / md[1]
        print(f"\n######## {fld}")
        decile_table(rk, f"{fld}: уровень (перцентиль за 30 дней)")
        decile_table(d24, f"{fld}: изменение доли лонгов за 24 ч (п.п.)")
        decile_table(rk, f"{fld}: уровень, 6 монет панели", coins=PANEL6)
        header(f'3. {fld}: крайние значения')
        for q in (0.9, 0.95):
            test(f"{fld} ≥ {q * 100:.0f}-го перц. 30 дн → лонг", rk >= q, 1, fam=f'top_{fld}_hi_{q}')
            test(f"{fld} ≥ {q * 100:.0f}-го перц. 30 дн → шорт", rk >= q, -1, fam=f'top_{fld}_hiS_{q}')
            test(f"{fld} ≤ {100 - q * 100:.0f}-го перц. 30 дн → лонг", rk <= 1 - q, 1, fam=f'top_{fld}_lo_{q}')
        for z in (1.5, 2.0, 3.0):
            test(f"{fld}: рост доли лонгов за 24 ч z≥+{z} → лонг", zd >= z, 1, fam=f'top_{fld}_dz_{z}')
            test(f"{fld}: падение доли лонгов за 24 ч z≤−{z} → лонг", zd <= -z, 1, fam=f'top_{fld}_dzn_{z}')


def sec_panel():
    """Exact rules of the «Крупнейшие трейдеры рынка» card (web/app.html renderTopTraders). Live it uses Binance top-trader
    POSITION long share and Bybit all-accounts buy share; here: ls_ratio_top / top_account_ratio and Binance global (all accounts)."""
    ex = load_extra()
    g = ex['ls_ratio_global'].copy(); g[IMP != 0] = np.nan; crowd = g / (1 + g)
    for fld, src in (('ls_ratio_top', A['ls_ratio_top']), ('top_account_ratio', ex['top_account_ratio'])):
        r = src.copy(); r[IMP != 0] = np.nan; Ls = r / (1 + r); d = diff(Ls, 96)
        for cs, cl in ((PANEL6, '6 монет панели'), (None, '18 монет')):
            header(f'Подсказки панели «Крупнейшие трейдеры»: {fld}, {cl}')
            print(f"  доля баров с условием: крупные≥60%&толпа<50%: {np.nanmean(((Ls >= 0.6) & (crowd < 0.5))[REAL]) * 100:.1f}%  "
                  f"крупные≤45%&толпа>55%: {np.nanmean(((Ls <= 0.45) & (crowd > 0.55))[REAL]) * 100:.1f}%  "
                  f"Δ24ч≥+5пп: {np.nanmean((d >= 0.05)[REAL]) * 100:.1f}%  Δ24ч≤−5пп: {np.nanmean((d <= -0.05)[REAL]) * 100:.1f}%")
            a = (Ls >= 0.6) & (crowd < 0.5); b = (Ls <= 0.45) & (crowd > 0.55)
            test("«крупные покупают, толпа продаёт» → лонг", a, 1, coins=cs, fam=f'panel_{fld}_A_{cl}')
            test("«крупные продают, толпа покупает» → лонг (что если купить)", b, 1, coins=cs, fam=f'panel_{fld}_B_{cl}')
            test("«крупные продают, толпа покупает» → шорт", b, -1, coins=cs, fam=f'panel_{fld}_Bs_{cl}')
            test("«крупные нарастили покупки ≥5 п.п. за сутки» → лонг", d >= 0.05, 1, coins=cs, fam=f'panel_{fld}_C_{cl}')
            test("«крупные сократили покупки ≥5 п.п.» → лонг", d <= -0.05, 1, coins=cs, fam=f'panel_{fld}_D_{cl}')
            test("крупные ≥60% (без условия на толпу) → лонг", Ls >= 0.6, 1, coins=cs, fam=f'panel_{fld}_L60_{cl}')
            test("толпа <50% (без условия на крупных) → лонг", crowd < 0.5, 1, coins=cs, fam=f'panel_crowd_{fld}_{cl}')
    rkg = trail(g, 'rank')
    decile_table(rkg, "толпа (все счета Binance): уровень, перцентиль 30 дней")


# ---------- 4. taker volume ratio and basis ----------
def sec_taker():
    tr = A['taker_volume_ratio'].copy(); tr[IMP != 0] = np.nan
    buy, sell = V * tr / (1 + tr), V / (1 + tr)
    for W, lab in ((4, '1ч'), (16, '4ч')):
        x = rmean(buy, W) / rmean(sell, W)
        q = trail(x, 'q', (0.01, 0.02, 0.05, 0.95, 0.98, 0.99))
        header(f'4a. Тейкеры: покупки/продажи за {lab}, крайние продажи → лонг (откат), крайние покупки → шорт/лонг')
        for j, p in ((0, 1), (1, 2), (2, 5)):
            test(f"тейкер {lab} ≤ {p}-го перц. 30 дн → лонг", x <= q[j], 1, fam=f'taker{W}_lo_{p}')
        for j, p in ((0, 1), (2, 5)):
            test(f"тейкер {lab} ≤ {p}-го перц., тихо → лонг", (x <= q[j]) & CALM[None, :], 1, fam=f'taker{W}_localm_{p}')
        for th in (0.5, 0.6, 0.7):
            test(f"тейкер {lab} ≤ {th} → лонг", x <= th, 1, fam=f'taker{W}_abs_{th}')
        for j, p in ((5, 99), (4, 98)):
            test(f"тейкер {lab} ≥ {p}-го перц. → шорт", x >= q[j], -1, fam=f'taker{W}_hiS_{p}')
            test(f"тейкер {lab} ≥ {p}-го перц. → лонг", x >= q[j], 1, fam=f'taker{W}_hiL_{p}')


def feat_basis(n=4):
    bs = A['basis_index_bps'].copy(); bs[IMP != 0] = np.nan; return rmean(bs, n)


def feat_topacc_dz():
    r = load_extra()['top_account_ratio'].copy(); r[IMP != 0] = np.nan
    d24 = diff(r / (1 + r), 96); md = trail(d24, 'ms'); return (d24 - md[0]) / md[1]


def sec_basis():
    b1 = feat_basis(4)
    q = trail(b1, 'q', (0.01, 0.02, 0.05, 0.95, 0.99))
    header('4b. Базис фьючерс−индекс (среднее за 1 ч), глубоко отрицательный → лонг')
    for th in (-10, -15, -20, -30):
        test(f"базис ≤ {th} б.п. → лонг", b1 <= th, 1, fam=f'basis_abs_{th}')
    for j, p in ((0, 1), (1, 2), (2, 5)):
        test(f"базис ≤ {p}-го перц. 30 дн → лонг", b1 <= q[j], 1, fam=f'basis_lo_{p}')
        test(f"базис ≤ {p}-го перц., тихо → лонг", (b1 <= q[j]) & CALM[None, :], 1, fam=f'basis_localm_{p}')
    for j, p in ((4, 99), (3, 95)):
        test(f"базис ≥ {p}-го перц. → шорт", b1 >= q[j], -1, fam=f'basis_hiS_{p}')
        test(f"базис ≥ {p}-го перц. → лонг", b1 >= q[j], 1, fam=f'basis_hiL_{p}')


# ---------- deep dive of the variants that passed ----------
def analyze(name, mask, side, h):
    """robustness of one candidate: 15-min later entry, coins, day concentration, excess over a price-matched baseline
    (same year, same 4 h / 24 h price-change bucket, all coins), and the part that does not overlap B/E/F."""
    print(f"\n--- {name}, {h // 4} ч")
    row = test(name, mask, side, (h,))[0]
    md = np.zeros_like(mask); md[:, 1:] = mask[:, :-1]
    test(name + ' · вход на 15 мин позже', md, side, (h,))
    k, i, r = row['k'], row['i'], row['r']
    pc = collections.defaultdict(list)
    for kk, rr in zip(k, r): pc[syms[kk]].append(rr)
    print("  по монетам (n, средняя): " + ', '.join(f"{c} {len(v)} {np.mean(v) * 100:+.1f}%" for c, v in sorted(pc.items(), key=lambda x: -len(x[1]))))
    dd = collections.defaultdict(float)
    for ii, rr in zip(i, r): dd[int(DAYN[ii])] += rr
    top = sorted(dd.values(), reverse=True)
    print(f"  разных дней: {len(dd)}; доля суммарного результата от 10 лучших дней: {sum(top[:10]) / max(sum(top), 1e-9) * 100:.0f}%; "
          f"без 10 лучших дней средняя сделка {(sum(top) - sum(top[:10])) / len(r) * 100:+.2f}%")
    f = FWD[(side, h)]
    for W, lab in ((16, '4 ч'), (96, '24 ч')):
        rw = chg(C, W); bins = np.array([-np.inf, -0.15, -0.10, -0.07, -0.05, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.05, 0.1, np.inf])
        bi = np.clip(np.searchsorted(bins, rw, 'right') - 1, 0, len(bins) - 2)
        okm = REAL & np.isfinite(f) & np.isfinite(rw)
        tab = np.full((4, len(bins) - 1), np.nan)
        for y in range(4):
            for b in range(len(bins) - 1):
                m = okm & (YI[None, :] == y) & (bi == b)
                if m.sum() > 50: tab[y, b] = f[m].mean()
        pb = tab[YI[i], bi[k, i]]; ex = r - pb; ok = np.isfinite(ex)
        print(f"  против случайного входа с тем же движением цены за {lab}: край {np.nanmean(ex) * 100:+.2f}% | "
              + ' '.join(f"{y}:{np.nanmean(ex[ok & (YI[i] == y - 2023)]) * 100:+.2f}" for y in YEARS)
              + f" | типичное движение до входа {np.nanmedian(rw[k, i]) * 100:+.1f}%")
    per, days = bef_index()
    near = np.array([kk in per and np.any(np.abs(per[kk][max(0, np.searchsorted(per[kk], ii + 1) - 1):np.searchsorted(per[kk], ii + 1) + 1] - (ii + 1)) <= 96)
                     for kk, ii in zip(k, i)])
    nn = ~near
    print(f"  рядом с B/E/F (та же монета ±24 ч): {near.mean() * 100:.0f}%; только новые сделки: n={nn.sum()} ({nn.sum() / NDAYS:.2f}/день) "
          f"средняя {r[nn].mean() * 100:+.2f}% | " + ' '.join(f"{y}:{r[nn & (YI[i] == y - 2023)].mean() * 100:+.2f}" for y in YEARS))
    return row


CANDS = {}


def sec_deep():
    b1, b2, b8 = feat_basis(4), feat_basis(1), feat_basis(16)
    for th in (-20, -25, -30):
        for h in (32, 96):
            CANDS[f'basis{th}_{h}'] = analyze(f"базис (1 ч) ≤ {th} б.п. → лонг", b1 <= th, 1, h)
    analyze("базис (1 свеча) ≤ −20 б.п. → лонг", b2 <= -20, 1, 96)
    analyze("базис (4 ч) ≤ −20 б.п. → лонг", b8 <= -20, 1, 96)
    analyze("базис (1 ч) ≤ −20 б.п. и тихо → лонг", (b1 <= -20) & CALM[None, :], 1, 96)
    zd = feat_topacc_dz()
    for z in (1.5, 2.0, 2.5):
        CANDS[f'topacc{z}_96'] = analyze(f"top_account: рост доли лонгов за 24 ч z≥+{z} → лонг", zd >= z, 1, 96)
    LL1 = rsum(np.abs(A['long_liq_usd']), 4); ql = trail(LL1, 'q', (0.5,))
    CANDS['liq5_32'] = analyze("LL1h ≥5× медианы, ≥$5M → лонг", (LL1 / ql[0] >= 5) & (LL1 >= 5e6), 1, 32)


# ---------- B/E/F reference + overlap ----------
def overlap(row, per, days, win=96):
    k, i = row['k'], row['i'] + 1
    near = np.array([len(per.get(kk, [])) > 0 and np.any(np.abs(per[kk][max(0, np.searchsorted(per[kk], ii) - 1):np.searchsorted(per[kk], ii) + 1] - ii) <= win)
                     for kk, ii in zip(k, i)])
    sameday = np.array([int(DAYN[ii]) in days for ii in i])
    return near.mean(), sameday.mean()


def sec_bef():
    header('Для сравнения: правила автопилота B / E / F как сделки (их собственные выходы)')
    for nm in ('B', 'E', 'F'):
        cs = [c for c in bef() if c['rule'] == nm]; r = np.array([c['ret'] for c in cs]); i = np.array([c['i'] for c in cs])
        print(f"  {nm}: n={len(r)} {len(r) / NDAYS:.2f}/день avg={r.mean() * 100:+.2f}% WR={(r > 0).mean() * 100:.0f}% | "
              + ' '.join(f"{y}:{r[YI[i] == y - 2023].mean() * 100:+.2f}" for y in YEARS) + f" | тихих (breadth<0.4) {CALM[i].mean() * 100:.0f}%")
    days = {int(DAYN[c['i']]) for c in bef()}
    print(f"  дней с любой сделкой B/E/F: {len(days)} из {NDAYS} ({len(days) / NDAYS * 100:.0f}%)")


# ---------- portfolio ----------
def to_cands(row, rule='N'):
    out = []
    for k, i in zip(row['k'], row['i']):
        e = O[k, i + 1]; x, g = AP.walk(k, i + 1, 1 if row['side'] > 0 else -1, e * (1 - STOP) if row['side'] > 0 else e * (1 + STOP), None, row['h'] - 1)
        out.append(dict(rule=rule, k=int(k), i=int(i + 1), x=int(x), ret=g - COST, sf=STOP))
    return out


NORMA = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40)


def portfolio(rows):
    """Adds each candidate signal as a share-of-equity rule with its own cap (n_share × n_open), using the free rule slot 'S'
    of AP.simulate (same mechanics as F) — the default rules and behaviour are untouched."""
    base = bef(); eq, tr, cu = AP.simulate(base, **NORMA); r0, _ = AP.report(eq, tr, cu)
    print(f"\n=== Портфель «норма»: B+E+F  ${r0['final']:,}  просадка {r0['maxdd'] * 100:.1f}%  сделок {r0['n']}  годы {r0['years']}")
    per, days = bef_index()
    for row in rows:
        cn = [dict(c, rule='S') for c in to_cands(row)]
        ov, sd = overlap(row, per, days)
        dr = collections.defaultdict(list); db = collections.defaultdict(list)
        for c in cn: dr[int(DAYN[c['i']])].append(c['ret'])
        for c in base: db[int(DAYN[c['i']])].append(c['ret'])
        common = sorted(set(dr) & set(db))
        corr = np.corrcoef([np.mean(dr[d]) for d in common], [np.mean(db[d]) for d in common])[0, 1] if len(common) > 10 else np.nan
        print(f"\n  {row['name']} {row['h'] // 4}h: n={row['n']} · B/E/F на той же монете ±24ч: {ov * 100:.0f}% · в дни с B/E/F: {sd * 100:.0f}% "
              f"· корреляция дневных результатов в общие дни ({len(common)}): {corr:+.2f}")
        for sh, op in ((0.05, 2), (0.1, 2), (0.1, 3), (0.15, 3), (0.2, 3)):
            eq, tr, cu = AP.simulate(base + cn, rules=('B', 'E', 'F', 'S'), s_share=sh, s_open=op, **NORMA); r, _ = AP.report(eq, tr, cu)
            better = all(r['years'].get(y, 0) >= r0['years'].get(y, 0) for y in r0['years'])
            print(f"    доля {sh * 100:4.0f}% ×{op}: ${r['final']:>8,}  просадка {r['maxdd'] * 100:6.1f}%  новое правило ${r['rules'].get('S', 0):>7,}  "
                  f"годы {r['years']}  {'лучше в каждом году' if better else 'хуже хотя бы в одном году'}"
                  f"{'' if r['maxdd'] >= r0['maxdd'] - 0.005 else ', просадка хуже'}", flush=True)


def summary():
    print("\n=== Итог: варианты, прошедшие проверку (плюс в каждом году, лучше случайного входа в каждом году, t≥2, n≥60)")
    ok = [r for r in RES if r['ok']]
    for r in sorted(ok, key=lambda r: -r['edge']):
        print(f"  {r['name']:52} {r['h'] // 4:2d}h n={r['n']:5d} avg={r['avg'] * 100:+.2f}% край={r['edge'] * 100:+.2f} t={r['t']:+.1f} "
              + ' '.join(f"{y}:{r['years'][y] * 100:+.2f}" for y in YEARS))
    if not ok: print("  — нет —")
    fams = collections.defaultdict(list)
    for r in RES: fams[r['fam'].rsplit('_', 1)[0] if r['fam'] else r['name']].append(r['ok'])
    print("\n  по семействам (доля прошедших ячеек сетки, все удержания):")
    for f, v in fams.items(): print(f"    {f:32} {sum(v)}/{len(v)}")
    return ok


if __name__ == '__main__':
    secs = [a for a in sys.argv[1:] if not a.startswith('-')] or ['bef', 'liq', 'cvd', 'top', 'panel', 'taker', 'basis']
    print(f"18 монет, {NDAYS} дней (2023-01-01 → {AP.DAY[-1]}), издержки {COST * 100:.2f}% за сделку, стоп −12%, вход на следующей свече")
    baseline_table()
    fn = dict(bef=sec_bef, liq=sec_liq, cvd=sec_cvd, top=sec_top, panel=sec_panel, taker=sec_taker, basis=sec_basis, deep=sec_deep)
    for s in secs:
        if s in fn: fn[s]()
    ok = summary()
    if 'portfolio' in secs:
        if not CANDS: sec_deep()
        portfolio([CANDS[c] for c in ('basis-20_96', 'basis-20_32', 'basis-30_96', 'topacc2.0_96', 'topacc1.5_96', 'liq5_32')])
