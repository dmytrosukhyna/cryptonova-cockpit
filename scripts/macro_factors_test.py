"""Macro factors and crypto — which outside markets move BTC and the 18-coin basket, does any of them lead it, do scheduled
events beyond FOMC/CPI/NFP matter, and is anything usable by the autopilot. docs/research/macro_factors.md

Data
  crypto   scripts/ap_data.py — 18 coins, Binance futures 15m, 2020-09 → 2026-09-09 (main sample 2023–2026, 2021–2022 = extra check)
  factors  FRED + Yahoo history fetched in GitHub Actions (research-macro.yml → scripts/macro_factors_fetch.py), decoded to
           $CNQ_MACRO_JSON (default <scratchpad>/macro/factors.json): daily since 2021, Yahoo hourly for the last 730 days
  events   scripts/macro_events.json (FOMC/CPI/NFP) + scripts/macro_events_ext.json (BoJ, ECB, PCE, PPI, GDP, Jackson Hole,
           Deribit expiry, US crypto legislation / SEC)
Alignment: a daily factor value is stamped at its own market close (US cash 16:00 ET, VIX 16:15, Treasury yields 15:30,
           noon-NY FX fixings 12:00, oil 14:30, Nikkei 15:00/15:30 JST; DST-aware). The «same-day» BTC return runs between two
           such closes; «next» BTC windows start 15 minutes after the close → no look-ahead.
Sections (default: all)
  explain   same-day correlation and beta of BTC / basket to every factor, per year; multivariate; rolling 90-day; regimes
  hourly    Yahoo 1h (Oct 2024 → Sep 2026): same-hour correlation, does the factor's hour lead BTC's next 1h / 4h
  predict   daily lead: factor move on day d → BTC next 24 h / 72 h; circular-shift null for the whole family
  extreme   extreme-move event study (USDJPY −2 %, 10Y ±15 bp, oil ±5 %, DXY ±1 %, VIX +30 %, Nasdaq ±3 % …), declustered,
            per year, 2021–22 check, circular-shift null with family-wise max-|t|
  diverge   cross-asset divergence (BTC flat while Nasdaq rallies, residual vs Nasdaq beta …) → BTC next 5 days
  liquidity Treasury cash (TGA) + Fed reverse repo (RRP): weekly changes vs BTC (Fed balance sheet / M2 not reachable)
  nikkei    the one lead that survived: BTC against the Nikkei's day — 8 rule variants, per year, 2021–22, shift null
  events    BoJ, ECB, PCE, PPI, GDP, Jackson Hole, Deribit expiry, crypto legislation: returns + volatility vs the same weekday and
            clock in quiet weeks; placebo calendars
  autopilot pre-registered macro filters in autopilot_v2.simulate «норма»+D; per year; random-date null
Usage: python scripts/macro_factors_test.py [section ...]"""
import sys, os, json, pickle, collections, warnings, datetime as dt, zoneinfo, numpy as np
sys.path.insert(0, 'scripts')
from ap_data import load, SRC

np.seterr(all='ignore'); warnings.filterwarnings('ignore')
SCRATCH = os.path.dirname(os.path.dirname(SRC.rstrip('/')))
FJSON = os.environ.get('CNQ_MACRO_JSON', os.path.join(SCRATCH, 'macro', 'factors.json'))
HERE = os.path.dirname(os.path.abspath(__file__))
NY, TOK, UTC = zoneinfo.ZoneInfo('America/New_York'), zoneinfo.ZoneInfo('Asia/Tokyo'), dt.timezone.utc
YEARS = (2023, 2024, 2025, 2026)
OUT = []


def P(s=''):
    print(s, flush=True); OUT.append(str(s))


# ------------------------------------------------------------------------------------------------ crypto
syms, times, A = load()
SY = [str(s).replace('USDT', '') for s in syms]
BTC = SY.index('BTC')
O = A['open']; LO = np.log(O); NS, T = LO.shape
T0, STEP = int(times[0]), 900_000
TEND = int(times[-1])
LP = LO[BTC]
_lr = np.nan_to_num(np.diff(LP, append=LP[-1]))
CUMV = np.concatenate([[0.0], np.cumsum(_lr * _lr)])           # CUMV[j] − CUMV[i] = sum of squared 15m BTC returns in [i, j)


def ix(t_ms):
    """bar index whose open is at t (ms); vectorised; −1 outside the data."""
    t = np.asarray(t_ms, np.int64)
    i = (t - T0) // STEP
    ok = (t >= T0) & (t <= TEND) & ((t - T0) % STEP == 0)
    return np.where(ok, i, -1)


def wret(t0, t1, asset='BTC'):
    """log return between two times (ms), vectorised; NaN outside the data. basket = equal-weight mean over coins (≥ 5)."""
    i, j = ix(t0), ix(t1)
    ok = (i >= 0) & (j >= 0)
    ii, jj = np.where(ok, i, 0), np.where(ok, j, 0)
    if asset == 'BTC':
        r = LP[jj] - LP[ii]
    else:
        d = LO[:, jj] - LO[:, ii]; n = np.isfinite(d).sum(0)
        r = np.where(n >= 5, np.nanmean(d, 0), np.nan)
    return np.where(ok, r, np.nan)


def wvol(t0, t1):
    """BTC realised volatility (sqrt of summed squared 15m log returns) between two times."""
    i, j = ix(t0), ix(t1)
    ok = (i >= 0) & (j >= 0)
    return np.where(ok, np.sqrt(np.maximum(CUMV[np.where(ok, j, 0)] - CUMV[np.where(ok, i, 0)], 0)), np.nan)


def tstat(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) == 0: return np.nan
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def corr_t(x, y):
    m = np.isfinite(x) & np.isfinite(y); x, y = x[m], y[m]; n = len(x)
    if n < 10: return np.nan, np.nan, n
    r = np.corrcoef(x, y)[0, 1]
    return r, r * np.sqrt((n - 2) / max(1 - r * r, 1e-12)), n


def year_of(t_ms):
    return np.array([dt.datetime.fromtimestamp(t / 1000, UTC).year for t in np.asarray(t_ms)], int)


# ------------------------------------------------------------------------------------------------ factors
RAW = json.load(open(FJSON)) if os.path.exists(FJSON) else {}


def at_local(d, hm, tz):
    h, m = map(int, hm.split(':'))
    return int(dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz).timestamp() * 1000)


# name → (series key | ('minus', key1, key2), kind, close time (tz, HH:MM) or callable(date) → ms, label)
#   kind: 'log' = log change, 'bp' = change in basis points (series in %)
#   close times checked against the hourly bars (17.10.2026): CME futures daily close = last trade 17:00 ET; DXY 15:00 ET;
#   ^TNX 15:00 ET; Yahoo FX «daily» value of date D = price at ≈ 00:00 London of D; Treasury par yields ≈ 15:30 ET quotes
LON = zoneinfo.ZoneInfo('Europe/London')


def n225_close(d):
    return at_local(d, '15:30' if d >= dt.date(2024, 11, 5) else '15:00', TOK)


def fx_close(d):
    return at_local(d, '00:00', LON)


DAILY = {
    'US10Y': ('treasury:UST10Y:1d', 'bp', (NY, '15:30'), 'доходность 10-летних США'),
    'US2Y': ('treasury:UST2Y:1d', 'bp', (NY, '15:30'), 'доходность 2-летних США'),
    '2s10s': (('minus', 'treasury:UST10Y:1d', 'treasury:UST2Y:1d'), 'bp', (NY, '15:30'), 'наклон кривой 10Y−2Y'),
    'REAL10Y': ('treasury:REAL10Y:1d', 'bp', (NY, '15:30'), 'реальная доходность 10Y (TIPS)'),
    'BE10Y': (('minus', 'treasury:UST10Y:1d', 'treasury:REAL10Y:1d'), 'bp', (NY, '15:30'), 'ожидаемая инфляция 10Y'),
    'DXY': ('yahoo:DX-Y.NYB:1d', 'log', (NY, '15:00'), 'индекс доллара DXY'),
    'USDJPY': ('yahoo:JPY=X:1d', 'log', fx_close, 'доллар/иена'),
    'EURUSD': ('yahoo:EURUSD=X:1d', 'log', fx_close, 'евро/доллар'),
    'WTI': ('yahoo:CL=F:1d', 'log', (NY, '17:00'), 'нефть WTI'),
    'BRENT': ('yahoo:BZ=F:1d', 'log', (NY, '17:00'), 'нефть Brent'),
    'GOLD': ('yahoo:GC=F:1d', 'log', (NY, '17:00'), 'золото'),
    'COPPER': ('yahoo:HG=F:1d', 'log', (NY, '17:00'), 'медь'),
    'SPX': ('yahoo:^GSPC:1d', 'log', (NY, '16:00'), 'S&P 500'),
    'NASDAQ': ('yahoo:^IXIC:1d', 'log', (NY, '16:00'), 'Nasdaq'),
    'RUSSELL': ('yahoo:RTY=F:1d', 'log', (NY, '17:00'), 'Russell 2000 (фьючерс)'),
    'VIX': ('cboe:VIX:1d', 'log', (NY, '16:15'), 'VIX (страх по акциям)'),
    'MOVE': ('yahoo:^MOVE:1d', 'log', (NY, '16:00'), 'MOVE (страх по облигациям)'),
    'HYG': ('yahoo:HYG:1d', 'log', (NY, '16:00'), 'мусорные облигации (ETF HYG)'),
    'NIKKEI': ('yahoo:^N225:1d', 'log', n225_close, 'Nikkei 225'),
}
MAIN = ['NASDAQ', 'SPX', 'RUSSELL', 'VIX', 'DXY', 'USDJPY', 'US10Y', 'US2Y', 'REAL10Y', 'WTI', 'BRENT', 'GOLD', 'COPPER', 'MOVE',
        'HYG', 'NIKKEI', 'EURUSD', 'BE10Y', '2s10s']


def series(key):
    s = RAW.get(key)
    if not s: return None, None, {}
    return np.array(s['t'], np.int64), np.array(s['v'], float), s.get('meta', {})


def obs_dates(key, t, meta):
    """observation dates: FRED stamps 00:00 UTC of the date; Yahoo daily bars carry the session date in the exchange zone."""
    if not key.startswith('yahoo:'):                         # official daily series: 00:00 UTC of the observation date
        return [dt.datetime.fromtimestamp(x, UTC).date() for x in t]
    tz = zoneinfo.ZoneInfo(meta.get('tz') or 'America/New_York')
    return [dt.datetime.fromtimestamp(x, tz).date() for x in t]


_DCACHE = {}


def _by_date(key):
    t, v, meta = series(key)
    if t is None or len(t) < 50: return None
    keep = {}
    for d, x in zip(obs_dates(key, t, meta), v):
        if d.weekday() < 5: keep[d] = x                       # last value of a date wins
    return keep


def daily(name):
    """dict(date, close_ms, value, chg (vs previous obs), prev_close_ms) for a daily factor."""
    if name in _DCACHE: return _DCACHE[name]
    key, kind, close, _ = DAILY[name]
    if isinstance(key, tuple):
        a, b = _by_date(key[1]), _by_date(key[2])
        keep = {d: a[d] - b[d] for d in a if b and d in b} if a else None
    else:
        keep = _by_date(key)
    if not keep: _DCACHE[name] = None; return None
    ds = sorted(keep); v = np.array([keep[d] for d in ds])
    cms = np.array([close(d) if callable(close) else at_local(d, close[1], close[0]) for d in ds], np.int64)
    chg = np.full(len(v), np.nan)
    if kind == 'log': chg[1:] = np.log(v[1:] / v[:-1])
    else: chg[1:] = (v[1:] - v[:-1]) * 100.0                    # % → bp
    gap = np.r_[np.inf, np.diff(cms) / 86_400_000]
    chg[gap > 6] = np.nan                                       # skip long data holes
    prev = np.r_[0, cms[:-1]]
    r = dict(date=np.array(ds), close=cms, value=v, chg=chg, prev=prev, kind=kind, year=np.array([d.year for d in ds]))
    _DCACHE[name] = r
    return r


def unit(name):
    return 'bp' if DAILY[name][1] == 'bp' else '%'


def scale(name):
    return 1.0 if DAILY[name][1] == 'bp' else 100.0


# ------------------------------------------------------------------------------------------------ 1. explanatory
def explain():
    P('=== 1. SAME-DAY: BTC / basket return between two closes of the factor (log %, factor in % or bp) ===')
    P('corr (t) per year; beta = BTC % per 1 % (or per 10 bp) of the factor; n = days. 2021–22 = extra check.')
    rows = []
    hdr = f"{'factor':10} {'asset':6} " + ' '.join(f"{y:>12}" for y in ('2021-22',) + YEARS) + f" {'2023-26':>12} {'beta23-26':>9} {'n':>5}"
    P(hdr)
    for name in MAIN:
        d = daily(name)
        if d is None: P(f'{name:10} (no data)'); continue
        for asset in ('BTC', 'basket'):
            rb = wret(d['prev'], d['close'], asset) * 100
            f = d['chg'] * (100 if DAILY[name][1] != 'bp' else 1)                 # % or bp
            cells = []
            for ys in ((2021, 2022),) + tuple((y,) for y in YEARS) + (YEARS,):
                m = np.isin(d['year'], ys)
                r, t, n = corr_t(f[m], rb[m]); cells.append(f"{r:+.2f}({t:+4.1f})" if np.isfinite(r) else '      -     ')
            m = np.isin(d['year'], YEARS) & np.isfinite(f) & np.isfinite(rb)
            per = 10.0 if DAILY[name][1] == 'bp' else 1.0
            beta = np.polyfit(f[m], rb[m], 1)[0] * per if m.sum() > 20 else np.nan
            rows.append((name, asset, cells, beta, m.sum()))
            P(f"{name:10} {asset:6} " + ' '.join(f"{c:>12}" for c in cells) + f" {beta:+9.2f} {m.sum():5d}")
    # multivariate on a common NY calendar (16:00 ET to 16:00 ET BTC window; factors' own daily changes on that date)
    P('\n--- multivariate (2023–2026): BTC daily on standardised factor changes, OLS t-stats per year ---')
    spx = daily('SPX'); dates = spx['date']; close = spx['close']; prev = spx['prev']
    names = ['NASDAQ', 'DXY', 'USDJPY', 'US10Y', 'WTI', 'GOLD', 'VIX', 'HYG']
    X = []
    for nm in names:
        d = daily(nm); mp = dict(zip(d['date'], d['chg'])); X.append(np.array([mp.get(x, np.nan) for x in dates]))
    X = np.array(X).T; y = wret(prev, close) * 100; yr = np.array([x.year for x in dates])
    P(f"{'year':7} " + ' '.join(f"{n:>8}" for n in names) + '   R²')
    for ys in tuple((y_,) for y_ in YEARS) + (YEARS,):
        m = np.isin(yr, ys) & np.isfinite(X).all(1) & np.isfinite(y)
        Z = (X[m] - X[m].mean(0)) / X[m].std(0); Z = np.c_[np.ones(m.sum()), Z]
        b, res, *_ = np.linalg.lstsq(Z, y[m], rcond=None)
        e = y[m] - Z @ b; s2 = e @ e / (len(e) - Z.shape[1]); se = np.sqrt(np.diag(s2 * np.linalg.inv(Z.T @ Z)))
        r2 = 1 - (e @ e) / ((y[m] - y[m].mean()) @ (y[m] - y[m].mean()))
        lab = str(ys[0]) if len(ys) == 1 else '2023-26'
        P(f"{lab:7} " + ' '.join(f"{b[k + 1] / se[k + 1]:+8.1f}" for k in range(len(names))) + f"  {r2:.2f}  n={m.sum()}")
    # rolling 90-day correlation with Nasdaq and a few others: quarterly table
    P('\n--- correlation by quarter (BTC vs factor, same day) ---')
    qn = ['NASDAQ', 'VIX', 'DXY', 'USDJPY', 'US10Y', 'GOLD', 'WTI']
    P(f"{'quarter':8} " + ' '.join(f"{n:>7}" for n in qn))
    for y_ in range(2021, 2027):
        for q in range(4):
            cells = []
            for nm in qn:
                d = daily(nm); m = (d['year'] == y_) & (np.array([(x.month - 1) // 3 for x in d['date']]) == q)
                rb = wret(d['prev'], d['close']) * 100
                r, t, n = corr_t(d['chg'][m], rb[m]); cells.append(f"{r:+7.2f}" if np.isfinite(r) and n >= 30 else '      -')
            if any(c.strip() != '-' for c in cells): P(f"{y_}Q{q + 1}  " + ' '.join(cells))
    d = daily('NASDAQ'); rb = wret(d['prev'], d['close']) * 100; f = d['chg'] * 100
    m = np.isin(d['year'], YEARS)
    rc = []
    idx = np.where(m)[0]
    for k in range(90, len(idx)):
        w = idx[k - 90:k]; r, _, n = corr_t(f[w], rb[w])
        if np.isfinite(r): rc.append(r)
    rc = np.array(rc)
    P(f"rolling 90-day corr BTC–Nasdaq 2023–26: median {np.median(rc):+.2f}, p10 {np.percentile(rc, 10):+.2f}, p90 {np.percentile(rc, 90):+.2f}, "
      f"min {rc.min():+.2f}, max {rc.max():+.2f}; share of windows > 0.3: {(rc > 0.3).mean() * 100:.0f}%, < 0: {(rc < 0).mean() * 100:.0f}%")
    # regimes: VIX level (previous close), size of the Nasdaq move, BTC trend
    P('\n--- regimes (2023–2026): BTC–Nasdaq same-day corr and beta ---')
    vix = daily('VIX'); vmap = dict(zip(vix['date'], vix['value']))
    vprev = np.array([vmap.get(x, np.nan) for x in d['date']]); vprev = np.r_[np.nan, vprev[:-1]]
    btc_d = wret(d['close'] - 200 * 86_400_000, d['close'])            # 200-day BTC return as trend proxy
    for lab, mm in (('VIX < 15', vprev < 15), ('VIX 15–20', (vprev >= 15) & (vprev < 20)), ('VIX 20–25', (vprev >= 20) & (vprev < 25)),
                    ('VIX ≥ 25', vprev >= 25), ('|Nasdaq| < 1%', np.abs(f) < 1), ('|Nasdaq| ≥ 1.5%', np.abs(f) >= 1.5),
                    ('Nasdaq ≤ −1.5%', f <= -1.5), ('Nasdaq ≥ +1.5%', f >= 1.5), ('BTC 200d up', btc_d > 0), ('BTC 200d down', btc_d <= 0)):
        mk = m & mm & np.isfinite(f) & np.isfinite(rb)
        r, t, n = corr_t(f[mk], rb[mk])
        b = np.polyfit(f[mk], rb[mk], 1)[0] if n > 10 else np.nan
        P(f"{lab:16} n={n:4d} corr {r:+.2f} (t {t:+.1f}) beta {b:+.2f}  mean BTC {np.nanmean(rb[mk]):+.2f}%  mean Nasdaq {np.nanmean(f[mk]):+.2f}%")
    return rows


# ------------------------------------------------------------------------------------------------ 2a. hourly
HOURLY = ['ES=F', 'NQ=F', 'RTY=F', 'DX-Y.NYB', 'JPY=X', 'EURUSD=X', 'CL=F', 'BZ=F', 'GC=F', 'HG=F', '^TNX', '^VIX', '2YY=F', 'ZN=F', '^N225']


def hourly_series(sym):
    t, v, meta = series(f'yahoo:{sym}:1h')
    if t is None or len(t) < 200: return None
    t = t * 1000
    r = np.full(len(v), np.nan); r[1:] = np.log(v[1:] / v[:-1])
    ok = np.r_[False, np.diff(t) == 3_600_000]                   # only consecutive full hours (no overnight / weekend gaps)
    t = t - t % STEP                                             # ^TNX bars start at :20 → nearest 15m bar below
    r[~ok] = np.nan
    return t, r


def shift_null(f, g, k_min=24 * 7, reps=300, rng=None):
    """circular shift of f against g: |corr t| distribution (keeps both series' own clustering)."""
    rng = rng or np.random.default_rng(5)
    n = len(f); out = []
    for _ in range(reps):
        s = int(rng.integers(k_min, n - k_min)); _, t, _ = corr_t(np.roll(f, s), g); out.append(abs(t))
    return np.array(out)


def hourly():
    P('\n=== 2a. HOURLY (Yahoo 1h, last 730 days ∩ crypto data): same hour and lead (factor hour → BTC next 1 h / 4 h) ===')
    rng = np.random.default_rng(3)
    allnull = collections.defaultdict(list); res = []
    P(f"{'factor':9} {'n':>6} {'same-hour corr(t)':>18} {'US cash':>8} {'other':>8} | {'→BTC +1h corr(t)':>17} {'→BTC +1..4h':>14} {'BTC→factor +1h':>15}")
    for sym in HOURLY:
        hs = hourly_series(sym)
        if hs is None: P(f"{sym:9} no data"); continue
        t, r = hs
        b0 = wret(t, t + 3_600_000) * 100; b1 = wret(t + 3_600_000, t + 7_200_000) * 100; b4 = wret(t + 3_600_000, t + 5 * 3_600_000) * 100
        bprev = wret(t - 3_600_000, t) * 100                    # BTC hour before the factor's hour (does BTC lead?)
        f = r * 100
        hrs = np.array([dt.datetime.fromtimestamp(x / 1000, NY).hour + dt.datetime.fromtimestamp(x / 1000, NY).minute / 60 for x in t])
        cash = (hrs >= 9.5) & (hrs < 16)
        c0 = corr_t(f, b0); cc = corr_t(f[cash], b0[cash]); co = corr_t(f[~cash], b0[~cash])
        c1 = corr_t(f, b1); c4 = corr_t(f, b4); cb = corr_t(bprev, f)
        m = np.isfinite(f) & np.isfinite(b1)
        n1 = shift_null(f[m], b1[m], rng=rng); m4 = np.isfinite(f) & np.isfinite(b4); n4 = shift_null(f[m4], b4[m4], rng=rng)
        allnull['1h'].append(n1); allnull['4h'].append(n4)
        res.append((sym, c1[1], c4[1], (n1 >= abs(c1[1])).mean(), (n4 >= abs(c4[1])).mean()))
        P(f"{sym:9} {c0[2]:6d} {c0[0]:+8.2f}({c0[1]:+6.1f}) {cc[0]:+8.2f} {co[0]:+8.2f} | {c1[0]:+8.3f}({c1[1]:+5.1f}) {c4[0]:+7.3f}({c4[1]:+5.1f}) "
              f"{cb[0]:+8.3f}({cb[1]:+5.1f})")
    # family-wise: max |t| over all factors under independent circular shifts
    if res:
        mx1 = np.max(np.array(allnull['1h']), 0); mx4 = np.max(np.array(allnull['4h']), 0)
        P(f"\nnull (circular shifts ≥ 1 week, 300 reps): per-test |t| p95 ≈ {np.percentile(np.concatenate(allnull['1h']), 95):.1f}; "
          f"family max-|t| p95: +1h {np.percentile(mx1, 95):.1f}, +1..4h {np.percentile(mx4, 95):.1f}")
        for sym, t1, t4, p1, p4 in res:
            P(f"   {sym:9} lead +1h t {t1:+5.1f} p {p1:.2f} family p {(mx1 >= abs(t1)).mean():.2f} | +1..4h t {t4:+5.1f} p {p4:.2f} family p {(mx4 >= abs(t4)).mean():.2f}")
    # big hourly moves of the factor (|z| ≥ 3 on its own hourly std) → BTC next 4 h, sign-adjusted
    P('\n--- big factor hours (|move| ≥ 3σ of its hourly returns): BTC same hour and next 4 h in the factor\'s direction ---')
    for sym in HOURLY:
        hs = hourly_series(sym)
        if hs is None: continue
        t, r = hs
        s = np.nanstd(r); big = np.abs(r) >= 3 * s
        sg = np.sign(r)
        b0 = wret(t, t + 3_600_000) * 100; b4 = wret(t + 3_600_000, t + 5 * 3_600_000) * 100
        idx = np.where(big & np.isfinite(b4))[0]
        keep = []; last = -10 ** 18
        for i in idx:
            if t[i] - last >= 4 * 3_600_000: keep.append(i); last = t[i]
        keep = np.array(keep, int)
        if len(keep) < 5: continue
        x0, x4 = sg[keep] * b0[keep], sg[keep] * b4[keep]
        P(f"{sym:9} n={len(keep):3d} (σ {s * 100:.2f}%) BTC same hour {np.nanmean(x0):+.2f}% (t {tstat(x0):+.1f}) | next 4 h {np.nanmean(x4):+.2f}% (t {tstat(x4):+.1f}, "
          f"same direction {(x4 > 0).mean() * 100:.0f}%)")


# ------------------------------------------------------------------------------------------------ 2b. daily lead
def predict():
    P('\n=== 2b. DAILY LEAD: factor change on day d → BTC from close+15 min to +24 h / +72 h (log %) ===')
    rng = np.random.default_rng(9)
    P(f"{'factor':10} {'n':>5} " + ' '.join(f"{'+24h ' + str(y):>13}" for y in ('21-22',) + YEARS) + f" {'+24h 23-26':>13} {'+72h 23-26':>13}")
    tests = []
    for name in MAIN:
        d = daily(name)
        if d is None: continue
        f = d['chg'] * (100 if DAILY[name][1] != 'bp' else 1)
        t0 = d['close'] + 900_000
        y24 = wret(t0, t0 + 86_400_000) * 100; y72 = wret(t0, t0 + 3 * 86_400_000) * 100
        cells = []
        for ys in ((2021, 2022),) + tuple((y,) for y in YEARS):
            m = np.isin(d['year'], ys); r, t, n = corr_t(f[m], y24[m]); cells.append(f"{r:+.2f}({t:+4.1f})" if np.isfinite(r) else '-')
        m = np.isin(d['year'], YEARS)
        r24, t24, n = corr_t(f[m], y24[m]); r72, t72, _ = corr_t(f[m], y72[m])
        tests.append((name, f[m], y24[m], y72[m], t24, t72))
        P(f"{name:10} {n:5d} " + ' '.join(f"{c:>13}" for c in cells) + f" {r24:+.2f}({t24:+4.1f})  {r72:+.2f}({t72:+4.1f})")
    # family null: circular shift of each factor's change series against the BTC outcomes (≥ 20 obs)
    mx = []
    for _ in range(400):
        best = 0
        for name, f, y24, y72, *_ in tests:
            n = len(f); s = int(rng.integers(20, n - 20))
            for y in (y24, y72):
                _, t, _ = corr_t(np.roll(f, s), y)
                if np.isfinite(t): best = max(best, abs(t))
        mx.append(best)
    mx = np.array(mx)
    P(f"\nfamily ({len(tests)} factors × 2 horizons): null max-|t| median {np.median(mx):.1f}, p95 {np.percentile(mx, 95):.1f}")
    for name, f, y24, y72, t24, t72 in sorted(tests, key=lambda x: -max(abs(x[4]), abs(x[5])))[:6]:
        P(f"   {name:10} t24 {t24:+.1f} (family p {(mx >= abs(t24)).mean():.2f}) | t72 {t72:+.1f} (family p {(mx >= abs(t72)).mean():.2f})")


# ------------------------------------------------------------------------------------------------ 2c. extreme moves
def conditions():
    """(label, factor, test(chg) → bool array, sign for the 'expected' crypto direction (−1 = risk-off))"""
    return [
        ('USDJPY ≤ −1.5% (иена резко дорожает)', 'USDJPY', lambda c: c <= -1.5, -1),
        ('USDJPY ≤ −2%', 'USDJPY', lambda c: c <= -2.0, -1),
        ('USDJPY ≥ +1.5%', 'USDJPY', lambda c: c >= 1.5, +1),
        ('US10Y ≥ +15 bp', 'US10Y', lambda c: c >= 15, -1),
        ('US10Y ≥ +10 bp', 'US10Y', lambda c: c >= 10, -1),
        ('US10Y ≤ −15 bp', 'US10Y', lambda c: c <= -15, +1),
        ('US10Y ≤ −10 bp', 'US10Y', lambda c: c <= -10, +1),
        ('US2Y ≥ +15 bp', 'US2Y', lambda c: c >= 15, -1),
        ('US2Y ≤ −15 bp', 'US2Y', lambda c: c <= -15, +1),
        ('WTI ≥ +5%', 'WTI', lambda c: c >= 5, -1),
        ('WTI ≤ −5%', 'WTI', lambda c: c <= -5, +1),
        ('DXY ≥ +1%', 'DXY', lambda c: c >= 1, -1),
        ('DXY ≤ −1%', 'DXY', lambda c: c <= -1, +1),
        ('VIX ≥ +30%', 'VIX', lambda c: c >= np.log(1.3) * 100, -1),
        ('VIX ≥ +20%', 'VIX', lambda c: c >= np.log(1.2) * 100, -1),
        ('VIX ≤ −15%', 'VIX', lambda c: c <= np.log(0.85) * 100, +1),
        ('Nasdaq ≤ −3%', 'NASDAQ', lambda c: c <= -3, -1),
        ('Nasdaq ≤ −2%', 'NASDAQ', lambda c: c <= -2, -1),
        ('Nasdaq ≥ +3%', 'NASDAQ', lambda c: c >= 3, +1),
        ('Nasdaq ≥ +2%', 'NASDAQ', lambda c: c >= 2, +1),
        ('S&P ≤ −2%', 'SPX', lambda c: c <= -2, -1),
        ('Gold ≥ +2.5%', 'GOLD', lambda c: c >= 2.5, 0),
        ('Gold ≤ −2.5%', 'GOLD', lambda c: c <= -2.5, 0),
        ('Nikkei ≤ −4%', 'NIKKEI', lambda c: c <= -4, -1),
        ('HYG ≤ −1% (мусорные облигации)', 'HYG', lambda c: c <= -1, -1),
        ('MOVE ≥ +15%', 'MOVE', lambda c: c >= np.log(1.15) * 100, -1),
    ]


def ev_outcomes(name, cond, years, gap_days=3):
    """event closes (declustered) and the outcome arrays for a condition; sample = all days of the factor in `years`."""
    d = daily(name)
    f = d['chg'] * (100 if DAILY[name][1] != 'bp' else 1)
    t0 = d['close'] + 900_000
    o24 = wret(t0, t0 + 86_400_000) * 100; o72 = wret(t0, t0 + 3 * 86_400_000) * 100
    b24 = wret(t0, t0 + 86_400_000, 'basket') * 100
    v24 = wvol(t0, t0 + 86_400_000) * 100
    same = wret(d['prev'], d['close']) * 100
    samp = np.isin(d['year'], years) & np.isfinite(o72) & np.isfinite(f)
    hit = np.zeros(len(f), bool)
    with np.errstate(invalid='ignore'):
        raw = cond(f) & samp
    last = -10 ** 18
    for i in np.where(raw)[0]:
        if d['close'][i] - last >= gap_days * 86_400_000: hit[i] = True; last = d['close'][i]
    return d, samp, hit, dict(o24=o24, o72=o72, b24=b24, v24=v24, same=same)


def ev_stat(hit, samp, y):
    """excess mean of event days over the other days; t uses max(event var, other-days var) so that 3–5 similar event
    days cannot produce a huge t (stable under the shift null)."""
    e = y[hit & samp]; b = y[samp & ~hit]
    e, b = e[np.isfinite(e)], b[np.isfinite(b)]
    if len(e) < 2 or len(b) < 10: return np.nan, np.nan, len(e)
    vb = np.var(b, ddof=1); se = np.sqrt(max(np.var(e, ddof=1), vb) / len(e) + vb / len(b))
    ex = e.mean() - b.mean()
    return ex, ex / se if se > 0 else np.nan, len(e)


def extreme():
    P('\n=== 2c. EXTREME MOVES of a factor on day d → BTC from its close+15 min: excess over all other days (log %) ===')
    P('declustered: a repeat within 3 days is the same event. vol× = BTC realised vol next 24 h / other days.')
    rng = np.random.default_rng(17)
    rows = []
    P(f"{'condition':38} {'n':>3} {'BTC same day':>12} {'+24h ex (t)':>14} {'+72h ex (t)':>14} {'basket+24h':>11} {'vol×24h':>7} | "
      f"+72h by year 2023/24/25/26 (n) | 2021-22 n, +72h ex (t)")
    for lab, name, cond, sgn in conditions():
        if daily(name) is None: continue
        d, samp, hit, Y = ev_outcomes(name, cond, YEARS)
        e24, t24, n = ev_stat(hit, samp, Y['o24']); e72, t72, _ = ev_stat(hit, samp, Y['o72']); eb, tb, _ = ev_stat(hit, samp, Y['b24'])
        vr = np.nanmean(Y['v24'][hit & samp]) / np.nanmean(Y['v24'][samp & ~hit])
        sd = np.nanmean(Y['same'][hit & samp])
        py = []
        for y_ in YEARS:
            m = d['year'] == y_
            ex, _, k = ev_stat(hit & m, samp & m, Y['o72']); py.append(f"{ex:+5.1f}({k})" if k >= 1 and np.isfinite(ex) else '   -   ')
        d2, s2, h2, Y2 = ev_outcomes(name, cond, (2021, 2022))
        e2, t2, n2 = ev_stat(h2, s2, Y2['o72'])
        rows.append(dict(lab=lab, name=name, cond=cond, n=n, t24=t24, t72=t72, e24=e24, e72=e72, vr=vr))
        P(f"{lab:38} {n:3d} {sd:+11.2f}% {e24:+6.2f} ({t24:+4.1f}) {e72:+6.2f} ({t72:+4.1f}) {eb:+10.2f}% {vr:7.2f} | {' '.join(py)} | {n2:3d} {e2:+6.2f} ({t2:+.1f})")
    # circular-shift null: shift the event dates of each condition along that factor's own day list (keeps clustering)
    P('\n--- null: every condition\'s event days circularly shifted by a random 20…(N−20) trading days, 1000 reps ---')
    reps = 1000; mx = np.zeros(reps); per = collections.defaultdict(list)
    prep = []
    for lab, name, cond, sgn in conditions():
        if daily(name) is None: continue
        d, samp, hit, Y = ev_outcomes(name, cond, YEARS)
        prep.append((lab, samp, hit, Y))
    prep_f = [x for x in prep if (x[1] & x[2]).sum() >= 5]          # the family: conditions with ≥ 5 events
    for r in range(reps):
        best = 0
        for lab, samp, hit, Y in prep_f:
            s = int(rng.integers(20, len(hit) - 20)); h = np.roll(hit, s)
            for key in ('o24', 'o72'):
                _, t, k = ev_stat(h, samp, Y[key])
                if np.isfinite(t):
                    per[(lab, key)].append(abs(t)); best = max(best, abs(t))
        mx[r] = best
    P(f"family: {len(prep_f)} conditions with ≥ 5 events × 2 horizons = {2 * len(prep_f)} tests; null max-|t| median {np.median(mx):.1f}, "
      f"p95 {np.percentile(mx, 95):.1f}; with no effect ≈ {0.05 * 2 * len(prep_f):.1f} tests show p < 0.05 by chance")
    sig = 0
    for row in rows:
        if row['n'] < 5: continue
        for key, t in (('o24', row['t24']), ('o72', row['t72'])):
            if not np.isfinite(t): continue
            p = (np.array(per[(row['lab'], key)]) >= abs(t)).mean(); pf = (mx >= abs(t)).mean()
            if p < 0.05: sig += 1
            if p < 0.10:
                P(f"   {row['lab']:38} {'+24h' if key == 'o24' else '+72h'} t {t:+.1f}: per-test p {p:.3f}, family p {pf:.2f}")
    P(f"tests with per-test p < 0.05: {sig}")
    # volatility after shocks (robust or not?): next-24h BTC vol ratio vs the same null
    P('\n--- BTC volatility in the next 24 h after the shock (ratio to other days) and its shift-null p ---')
    for lab, samp, hit, Y in prep:
        v = Y['v24']; real = np.nanmean(v[hit & samp]) / np.nanmean(v[samp & ~hit]); k = (hit & samp).sum()
        if k < 3: continue
        nl = []
        for _ in range(300):
            h = np.roll(hit, int(rng.integers(20, len(hit) - 20)))
            if (h & samp).sum(): nl.append(np.nanmean(v[h & samp]) / np.nanmean(v[samp & ~h]))
        nl = np.array(nl)
        P(f"   {lab:38} n={k:3d} vol× {real:.2f}  (null median {np.median(nl):.2f}, p {(nl >= real).mean():.3f})")
    return rows


# ------------------------------------------------------------------------------------------------ 2d. divergence
def diverge():
    P('\n=== 2d. CROSS-ASSET DIVERGENCE → BTC next 5 days (from Nasdaq close + 15 min), declustered 5 days ===')
    d = daily('NASDAQ'); rng = np.random.default_rng(21)
    c = d['close']; nq = np.log(d['value'])
    n5 = np.full(len(c), np.nan); n5[5:] = (nq[5:] - nq[:-5]) * 100
    b5 = wret(np.r_[np.full(5, 0), c[:-5]], c) * 100; b5[:5] = np.nan
    fut = wret(c + 900_000, c + 900_000 + 5 * 86_400_000) * 100
    fut1 = wret(c + 900_000, c + 900_000 + 86_400_000) * 100
    # residual vs rolling 60-day beta of BTC on Nasdaq (daily)
    rb = wret(d['prev'], d['close']) * 100; rn = d['chg'] * 100
    beta = np.full(len(c), np.nan)
    for i in range(60, len(c)):
        x, y = rn[i - 60:i], rb[i - 60:i]; m = np.isfinite(x) & np.isfinite(y)
        if m.sum() > 40: beta[i] = np.polyfit(x[m], y[m], 1)[0]
    res5 = b5 - beta * n5
    sd = np.array([np.nanstd(res5[max(0, i - 250):i]) if i > 60 else np.nan for i in range(len(c))])
    z = res5 / sd
    gold = daily('GOLD'); gm = dict(zip(gold['date'], np.log(gold['value'])))
    g = np.array([gm.get(x, np.nan) for x in d['date']]); g5 = np.full(len(c), np.nan); g5[5:] = (g[5:] - g[:-5]) * 100
    conds = [('Nasdaq 5d ≥ +3% и BTC 5d ≤ 0 (BTC отстаёт)', (n5 >= 3) & (b5 <= 0)),
             ('Nasdaq 5d ≤ −3% и BTC 5d ≥ 0 (BTC держится)', (n5 <= -3) & (b5 >= 0)),
             ('Nasdaq 5d ≥ +2% и BTC 5d ≤ −2%', (n5 >= 2) & (b5 <= -2)),
             ('остаток BTC к Nasdaq z ≤ −1.5 (BTC хуже беты)', z <= -1.5),
             ('остаток BTC к Nasdaq z ≥ +1.5 (BTC лучше беты)', z >= 1.5),
             ('золото 5d ≥ +3% и BTC 5d ≤ 0', (g5 >= 3) & (b5 <= 0))]
    samp0 = np.isfinite(fut) & np.isfinite(n5) & np.isfinite(b5)
    P(f"{'condition':46} {'n':>3} {'+1d ex (t)':>13} {'+5d ex (t)':>13} | +5d by year 2023/24/25/26 (n) | 2021-22 n +5d ex | shift-null p(+5d)")
    for lab, cm in conds:
        out = {}
        for ys in (YEARS, (2021, 2022)):
            samp = samp0 & np.isin(d['year'], ys)
            hit = np.zeros(len(c), bool); last = -10 ** 18
            for i in np.where(cm & samp)[0]:
                if c[i] - last >= 5 * 86_400_000: hit[i] = True; last = c[i]
            out[ys] = (samp, hit)
        samp, hit = out[YEARS]
        e1, t1, n = ev_stat(hit, samp, fut1); e5, t5, _ = ev_stat(hit, samp, fut)
        py = []
        for y_ in YEARS:
            m = d['year'] == y_; ex, _, k = ev_stat(hit & m, samp & m, fut); py.append(f"{ex:+5.1f}({k})" if k and np.isfinite(ex) else '   -   ')
        s2, h2 = out[(2021, 2022)]; e2, t2, n2 = ev_stat(h2, s2, fut)
        nl = []
        for _ in range(500):
            h = np.roll(hit, int(rng.integers(20, len(hit) - 20))); _, tt, _ = ev_stat(h, samp, fut)
            if np.isfinite(tt): nl.append(abs(tt))
        p = (np.array(nl) >= abs(t5)).mean() if np.isfinite(t5) else np.nan
        P(f"{lab:46} {n:3d} {e1:+6.2f} ({t1:+4.1f}) {e5:+6.2f} ({t5:+4.1f}) | {' '.join(py)} | {n2:3d} {e2:+6.2f} ({t2:+.1f}) | {p:.2f}")


# ------------------------------------------------------------------------------------------------ 2e. liquidity
def liquidity():
    """Liquidity drains that are reachable from Actions: Treasury cash account (TGA, Daily Treasury Statement) and the Fed
    reverse repo (RRP, NY Fed). The Fed balance sheet (WALCL) and M2 sit on FRED, which does not answer from Actions —
    so «net liquidity» = −(TGA + RRP) changes only (QT was a slow, steady drift in 2023–2026)."""
    P('\n=== 2e. LIQUIDITY: Treasury cash (TGA) + Fed reverse repo (RRP) — money pulled out of markets (weekly) ===')
    rng = np.random.default_rng(31)
    tg, vg, _ = series('fiscal:TGA:1d'); tr, vr, _ = series('nyfed:RRP:1d')
    if tg is None or tr is None: P('no liquidity data'); return
    g = dict(zip(tg, vg)); r = dict(zip(tr, vr))
    days = sorted(set(g) & set(r))
    # weekly: Wednesday values (or the last day before), BTC Thursday 00:00 UTC → next Thursday
    wk, drain = [], []
    for d in days:
        x = dt.datetime.fromtimestamp(d, UTC)
        if x.weekday() == 2: wk.append(d * 1000); drain.append(g[d] + r[d])
    wk = np.array(wk, np.int64); drain = np.array(drain)
    thu = wk + 86_400_000
    dd = np.r_[np.nan, np.diff(drain)]                           # $ bln per week; + = more money parked away (drain)
    yr = year_of(wk)
    P(f"weeks {len(wk)}; drain (TGA+RRP) {drain[np.isin(yr, YEARS)].min():.0f}…{drain[np.isin(yr, YEARS)].max():.0f} $ bln in 2023–26")
    for lab, ys in (('2023-26', YEARS), ('2022', (2022,))):
        m = np.isin(yr, ys)
        cells = []
        for k in (0, 1, 2, 4):
            y = wret(thu - 7 * 86_400_000, thu) * 100 if k == 0 else wret(thu + (k - 1) * 7 * 86_400_000, thu + k * 7 * 86_400_000) * 100
            x_, y_ = dd[m], y[m]; ok = np.isfinite(x_) & np.isfinite(y_); x_, y_ = x_[ok], y_[ok]
            if len(x_) < 20: continue
            rr, t, n = corr_t(x_, y_)
            nul = [abs(corr_t(np.roll(x_, int(rng.integers(8, len(x_) - 8))), y_)[1]) for _ in range(500)]
            cells.append(f"{'same week' if k == 0 else f'+{k} wk'}: {rr:+.2f} (t {t:+.1f}, p {(np.array(nul) >= abs(t)).mean():.2f}, n {n})")
        P(f"  {lab}: Δ drain vs BTC " + ' | '.join(cells))
    l13 = np.full(len(drain), np.nan); l13[13:] = drain[13:] - drain[:-13]
    f13 = wret(thu, thu + 13 * 7 * 86_400_000) * 100
    m = np.isin(yr, YEARS) & np.isfinite(l13) & np.isfinite(f13)
    sub = np.arange(m.sum())[::13]
    r1, t1, n1 = corr_t(l13[m], f13[m]); r2, t2, n2 = corr_t(l13[m][sub], f13[m][sub])
    P(f"  13-week Δ drain → BTC next 13 weeks: corr {r1:+.2f} (overlapping n={n1}); non-overlapping n={n2}: corr {r2:+.2f} (t {t2:+.1f})")
    P('  (Fed balance sheet WALCL and M2 are on FRED, which times out from GitHub Actions — not tested.)')


# ------------------------------------------------------------------------------------------------ 3. scheduled events
EXT = os.path.join(HERE, 'macro_events_ext.json'); BASE = os.path.join(HERE, 'macro_events.json')


def load_ext():
    ev = []
    for path in (BASE, EXT):
        if not os.path.exists(path): continue
        for e in json.load(open(path))['events']:
            if e.get('status') == 'cancelled' or not e.get('utc'): continue
            u = dt.datetime.strptime(e['utc'], '%Y-%m-%dT%H:%MZ').replace(tzinfo=UTC)
            ev.append(dict(e, t=int(u.timestamp() * 1000), year=u.year))
    return ev


EVX = load_ext()
MAJOR_US = ('FOMC', 'CPI', 'NFP', 'PCE', 'PPI', 'GDP')
_MAJ_T = np.array(sorted(e['t'] for e in EVX if e['type'] in MAJOR_US), np.int64)


def near(arr, t, h):
    j = np.searchsorted(arr, t - h * 3_600_000)
    return j < len(arr) and arr[j] <= t + h * 3_600_000


def ev_tz(e):
    return zoneinfo.ZoneInfo(e.get('tz') or 'America/New_York') if e.get('tz') not in (None, 'UTC') else UTC


def baselines(e, same_t, kmax=12):
    """same weekday + local clock ±1..kmax weeks; no event of this type within ±72 h, no major US release within ±24 h."""
    tz = ev_tz(e); loc = dt.datetime.fromtimestamp(e['t'] / 1000, tz); out = []
    for k in list(range(-kmax, 0)) + list(range(1, kmax + 1)):
        x = loc + dt.timedelta(weeks=k); t = int(x.astimezone(UTC).timestamp() * 1000); t -= t % STEP
        if near(same_t, t, 72) or near(_MAJ_T, t, 24): continue
        if ix(t - 48 * 3_600_000) < 0 or ix(t + 72 * 3_600_000) < 0: continue
        out.append(t)
    return out


EWIN = [(-24, 0), (0, 1), (1, 4), (4, 24), (24, 72)]
BOJ_WIN = [(-24, 0), (0, 4), (4, 24), (24, 72), (72, 120)]
EV_TYPES = ('BOJ', 'ECB', 'PCE', 'PPI', 'GDP', 'JACKSON_HOLE', 'OPEX', 'OPEX_Q', 'CRYPTO_POLICY')


def ev_list(typ, years=YEARS):
    if typ == 'OPEX_Q':
        es = [e for e in EVX if e['type'] == 'OPEX' and e.get('quarterly')]
    elif typ == 'OPEX':
        es = [e for e in EVX if e['type'] == 'OPEX' and not e.get('quarterly')]
    else:
        es = [e for e in EVX if e['type'] == typ]
    out = []
    for e in es:
        if e['year'] not in years: continue
        e = dict(e)
        e['t'] -= e['t'] % STEP                                  # 10:05 ET (Jackson Hole 2023) → the 15m bar that contains it
        if typ == 'BOJ': e['t'] -= 3_600_000                     # 12:00 JST nominal → window starts 02:00 UTC (11:00 JST)
        if ix(e['t'] - 48 * 3_600_000) >= 0 and ix(e['t'] + 120 * 3_600_000) >= 0: out.append(e)
    return out


def ev_table(typ, tmap=None, years=YEARS):
    """per event: (year, [ (ret, base_ret, vol, base_vol) per window ]) ; tmap: placebo time per real event time."""
    es = ev_list(typ, years)
    same_t = np.array(sorted(e['t'] for e in es), np.int64)
    wins = BOJ_WIN if typ == 'BOJ' else EWIN
    rows = []
    for e in es:
        bl = baselines(e, same_t)
        if len(bl) < 3: continue
        t = e['t'] if tmap is None else tmap.get(e['t'])
        if t is None: continue
        bl = np.array(bl, np.int64); rec = []
        for w0, w1 in wins:
            a, b = t + w0 * 3_600_000, t + w1 * 3_600_000
            r = float(wret(a, b)) * 100; v = float(wvol(a, b)) * 100
            rb = wret(bl + w0 * 3_600_000, bl + w1 * 3_600_000) * 100; vb = wvol(bl + w0 * 3_600_000, bl + w1 * 3_600_000) * 100
            rec.append((r, np.nanmean(rb), v, np.exp(np.nanmean(np.log(vb))), np.nanmean(np.abs(rb))))   # geometric baseline vol: no Jensen bias
        rows.append((e['year'], rec, e))
    return rows, wins


def events():
    P('\n=== 3. SCHEDULED EVENTS beyond FOMC/CPI/NFP (BTC; baseline = same weekday + local clock ±1..12 weeks, no same-type event '
      'within ±72 h, no FOMC/CPI/NFP/PCE/PPI/GDP within ±24 h) ===')
    P('ret ex = BTC log return minus the baseline mean, % (t); vol× = exp(mean(log event vol − mean log baseline vol)) (t of the log ratio)')
    rng = np.random.default_rng(41)
    summary = {}
    for typ in EV_TYPES:
        rows, wins = ev_table(typ)
        if len(rows) < 3: P(f'\n{typ}: {len(rows)} usable events — skipped'); continue
        yrs = collections.Counter(y for y, _, _ in rows)
        P(f"\n--- {typ}: n={len(rows)} ({', '.join(f'{y}:{yrs[y]}' for y in sorted(yrs))}) ---")
        for k, (w0, w1) in enumerate(wins):
            ex = np.array([r[k][0] - r[k][1] for _, r, _ in rows]); lv = np.log(np.array([r[k][2] / r[k][3] for _, r, _ in rows]))
            yy = np.array([y for y, _, _ in rows])
            py = ' '.join(f"{ex[yy == y].mean():+5.2f}/{np.exp(np.nanmean(lv[yy == y])):.2f}" if (yy == y).any() else '     -    ' for y in YEARS)
            summary[(typ, w0, w1)] = (ex.mean(), tstat(ex), np.exp(np.nanmean(lv)), tstat(lv))
            P(f"  {w0:+4d}…{w1:+4d}h ret ex {ex.mean():+6.2f}% (t {tstat(ex):+4.1f}) | vol× {np.exp(np.nanmean(lv)):.2f} (t {tstat(lv):+4.1f}) "
              f"| |ret| {np.mean([abs(r[k][0]) for _, r, _ in rows]):.2f}% vs base {np.mean([r[k][4] for _, r, _ in rows]):.2f}% | by year ret/vol× {py}")
        if typ == 'BOJ':
            P('  BoJ decisions with a policy change (hikes / YCC changes) — BTC return from 02:00 UTC of the decision day (and minus baseline):')
            hk = []
            for y, r, e in rows:
                if e.get('change'):
                    c72 = sum(r[q][0] for q in range(1, 4)); b72 = sum(r[q][1] for q in range(1, 4))
                    c120 = sum(r[q][0] for q in range(1, 5)); b120 = sum(r[q][1] for q in range(1, 5))
                    if e.get('hike'): hk.append((c72 - b72, c120 - b120))
                    P(f"    {e['date_local']} {e['change'][:60]:60} 0…4h {r[1][0]:+5.2f}% | 0…24h {r[1][0] + r[2][0]:+5.2f}% | "
                      f"0…72h {c72:+6.2f}% (ex {c72 - b72:+6.2f}) | 0…120h {c120:+6.2f}% (ex {c120 - b120:+6.2f})")
            if hk:
                h = np.array(hk)
                P(f"    hikes in the sample n={len(h)}: 0…72h ex mean {h[:, 0].mean():+.2f}% (t {tstat(h[:, 0]):+.1f}), negative {(h[:, 0] < 0).sum()} of {len(h)}; "
                  f"0…120h ex mean {h[:, 1].mean():+.2f}% (t {tstat(h[:, 1]):+.1f})")
        if typ == 'CRYPTO_POLICY':
            P('  each event (approximate clock time): BTC −24…0h | 0…+24h | +24…+72h, %')
            for y, r, e in rows:
                P(f"    {e['date_local']} {e.get('kind', ''):11} {e['notes'][:70]:70} {r[0][0]:+6.2f} | {r[3][0] + r[1][0] + r[2][0]:+6.2f} | {r[4][0]:+6.2f}")
    # 2021–2022 check for the main types
    P('\n--- 2021–2022 check (same method): first window after the event — ret ex (t) and vol× ---')
    for typ in ('BOJ', 'ECB', 'PPI', 'OPEX', 'OPEX_Q', 'JACKSON_HOLE'):
        rows, wins = ev_table(typ, years=(2021, 2022))
        if len(rows) < 3: continue
        k = 1
        ex = np.array([r[k][0] - r[k][1] for _, r, _ in rows]); lv = np.log(np.array([r[k][2] / r[k][3] for _, r, _ in rows]))
        P(f"  {typ:12} n={len(rows):2d} {wins[k][0]:+d}…{wins[k][1]:+d}h ret ex {ex.mean():+.2f}% (t {tstat(ex):+.1f}) vol× {np.exp(np.nanmean(lv)):.2f} (t {tstat(lv):+.1f})")
    # placebo calendars: each event moved to one of its own baseline times → how many |t| ≥ 2 does the family show by chance?
    P('\n--- placebo: 100 fake calendars (each event → a random baseline time), same tables; count of |t| ≥ 2 ---')
    fam = [k for k in summary if k[0] not in ('CRYPTO_POLICY', 'JACKSON_HOLE')]          # n < 8: anecdotes, not tests
    real_r = sum(1 for k in fam if abs(summary[k][1]) >= 2); real_v = sum(1 for k in fam if abs(summary[k][3]) >= 2)
    cache = {}
    for typ in set(k[0] for k in fam):
        es = ev_list(typ); same_t = np.array(sorted(e['t'] for e in es), np.int64)
        cache[typ] = {e['t']: baselines(e, same_t) for e in es}
    cr, cv, mxr, mxv = [], [], [], []
    for _ in range(100):
        nr = nv = 0; br = bv = 0
        for typ in set(k[0] for k in fam):
            tmap = {t: int(rng.choice(b)) for t, b in cache[typ].items() if b}
            rows, wins = ev_table(typ, tmap=tmap)
            for k in range(len(wins)):
                ex = np.array([r[k][0] - r[k][1] for _, r, _ in rows]); lv = np.log(np.array([r[k][2] / r[k][3] for _, r, _ in rows]))
                tr, tv = tstat(ex), tstat(lv)
                nr += abs(tr) >= 2; nv += abs(tv) >= 2; br = max(br, abs(tr)); bv = max(bv, abs(tv))
        cr.append(nr); cv.append(nv); mxr.append(br); mxv.append(bv)
    cr, cv, mxr, mxv = map(np.array, (cr, cv, mxr, mxv))
    P(f"returns: real {real_r} of {len(fam)} tests |t| ≥ 2; placebo median {np.median(cr):.0f} (p90 {np.percentile(cr, 90):.0f}); "
      f"placebo max-|t| p95 {np.percentile(mxr, 95):.1f}")
    P(f"volatility: real {real_v} of {len(fam)} tests |t| ≥ 2; placebo median {np.median(cv):.0f} (p90 {np.percentile(cv, 90):.0f}); "
      f"placebo max-|t| p95 {np.percentile(mxv, 95):.1f}")
    for k in sorted(fam, key=lambda k: -max(abs(summary[k][1]), abs(summary[k][3]))):
        mr, tr, vx, tv = summary[k]
        if abs(tr) >= 2 or abs(tv) >= 2:
            P(f"   {k[0]:12} {k[1]:+4d}…{k[2]:+4d}h ret {mr:+.2f}% (t {tr:+.1f}, family p {(mxr >= abs(tr)).mean():.2f}) | vol× {vx:.2f} "
              f"(t {tv:+.1f}, family p {(mxv >= abs(tv)).mean():.2f})")
    return summary


# ------------------------------------------------------------------------------------------------ 2f. Nikkei reversal
COST = 2 * (0.0005 + 0.0002)                                     # 0.14 % round trip, as autopilot_v2
N_VARIANTS = [(th, w, sides) for th in (1.5, 2.0) for w in ((0.25, 24), (6, 12)) for sides in ('both', 'long')]


def nikkei_cands(th, w, sides, years, f_override=None):
    """BTC against the Nikkei's close-to-close move: Nikkei ≤ −th % → long, ≥ +th % → short (sides='both');
    entry at the Tokyo close + w[0] h, exit at close + w[1] h, net of 0.14 %. Returns autopilot-style candidates."""
    d = daily('NIKKEI'); f = d['chg'] * 100 if f_override is None else f_override
    out = []
    for i in np.where(np.isin(d['year'], years) & np.isfinite(f))[0]:
        sg = 1 if f[i] <= -th else (-1 if f[i] >= th and sides == 'both' else 0)
        if not sg: continue
        t = int(d['close'][i]); t -= t % STEP
        i0, i1 = int(ix(t + int(w[0] * 3_600_000))), int(ix(t + int(w[1] * 3_600_000)))
        if i0 < 0 or i1 < 0 or not np.isfinite(O[BTC, i0]) or not np.isfinite(O[BTC, i1]): continue
        out.append(dict(rule='n', k=BTC, i=i0, x=i1, ret=sg * (O[BTC, i1] / O[BTC, i0] - 1) - COST, sf=0.05, side=sg,
                        year=int(d['year'][i])))
    return out


def nikkei():
    P('\n=== 2f. NIKKEI REVERSAL: BTC against the Nikkei day (found in 2b), net of 0.14 % per trade ===')
    P('long BTC after a Nikkei fall ≥ th %, short after a rise ≥ th % (or long only); entry = Tokyo close + a h, exit = close + b h')
    d = daily('NIKKEI'); f = d['chg'] * 100; rng = np.random.default_rng(61)
    P(f"{'variant':30} {'n':>4} {'avg':>7} {'t':>5} {'win':>4} | per year 2023/24/25/26 (n) | 2021-22 n avg (t) | shift-null p(avg)")
    res = {}
    for th, w, sides in N_VARIANTS:
        c = nikkei_cands(th, w, sides, YEARS); r = np.array([x['ret'] for x in c]) * 100; y = np.array([x['year'] for x in c])
        o = np.array([x['ret'] for x in nikkei_cands(th, w, sides, (2021, 2022))]) * 100
        nl = []
        for _ in range(300):
            ff = np.roll(f, int(rng.integers(20, len(f) - 20)))
            rr = [x['ret'] for x in nikkei_cands(th, w, sides, YEARS, ff)]
            if rr: nl.append(np.mean(rr) * 100)
        nl = np.array(nl)
        lab = f"th {th}% {w[0]:g}…{w[1]:g}h {sides}"
        res[(th, w, sides)] = (r.mean(), [r[y == q].mean() if (y == q).any() else np.nan for q in YEARS], nl)
        P(f"{lab:30} {len(r):4d} {r.mean():+6.2f}% {tstat(r):+5.1f} {(r > 0).mean() * 100:3.0f}% | "
          + ' '.join(f"{r[y == q].mean():+5.2f}({(y == q).sum()})" for q in YEARS)
          + f" | {len(o):3d} {o.mean() if len(o) else np.nan:+.2f}% ({tstat(o):+.1f}) | {(nl >= r.mean()).mean():.3f}")
    # selection-aware: the best of the 8 variants on shifted data
    best_real = max(v[0] for v in res.values()); best_null = np.max(np.array([v[2][:250] for v in res.values()]), 0)
    P(f"best of {len(res)} variants: real {best_real:+.2f}% per trade; shifted-data best: median {np.median(best_null):+.2f}%, "
      f"p95 {np.percentile(best_null, 95):+.2f}% → real beats {(best_real > best_null).mean() * 100:.0f}%")
    # longs and shorts separately for the main variants
    for th in (1.5, 2.0):
        c = nikkei_cands(th, (0.25, 24), 'both', YEARS)
        for sg, lab in ((1, 'long after a fall'), (-1, 'short after a rise')):
            r = np.array([x['ret'] for x in c if x['side'] == sg]) * 100
            P(f"   th {th}% 24h {lab:20} n={len(r):3d} avg {r.mean():+.2f}% (t {tstat(r):+.1f})")
    P('\n--- timing / threshold robustness (th 1.5 %, both sides unless stated) ---')
    for w in ((0.25, 24), (1, 24), (2, 25), (3, 27), (6, 30), (0.25, 12), (0.25, 36), (0.25, 48), (6, 12), (12, 24)):
        c = nikkei_cands(1.5, w, 'both', YEARS); r = np.array([x['ret'] for x in c]) * 100; y = np.array([x['year'] for x in c])
        o = np.array([x['ret'] for x in nikkei_cands(1.5, w, 'both', (2021, 2022))]) * 100
        P(f"   entry +{w[0]:>4g} h, exit +{w[1]:>3g} h: avg {r.mean():+.2f}% (t {tstat(r):+.1f}) | " + ' '.join(f"{r[y == q].mean():+.2f}" for q in YEARS)
          + f" | 2021-22 {o.mean():+.2f}% (t {tstat(o):+.1f})")
    for th in (1.0, 1.25, 2.5, 3.0):
        c = nikkei_cands(th, (0.25, 24), 'both', YEARS); r = np.array([x['ret'] for x in c]) * 100; y = np.array([x['year'] for x in c])
        P(f"   threshold {th}%: n={len(r)} avg {r.mean():+.2f}% (t {tstat(r):+.1f}) | " + ' '.join(f"{r[y == q].mean():+.2f}" if (y == q).any() else '  -  ' for q in YEARS))
    c = nikkei_cands(1.5, (0.25, 24), 'both', YEARS); g = collections.defaultdict(list)
    for x in c:
        q = dt.datetime.fromtimestamp(times[x['i']] / 1000, UTC); g[f"{q.year}H{1 + (q.month > 6)}"].append(x['ret'] * 100)
    P('   by half-year: ' + ' '.join(f"{k} {np.mean(v):+.2f}({len(v)})" for k, v in sorted(g.items())))
    # after the crypto data end (2026-09-09): Yahoo BTC-USD hourly closes (bar open + 1 h)
    bt, bv, _ = series('yahoo:BTC-USD:1h')
    if bt is not None:
        bt = bt * 1000 + 3_600_000; rr = []
        for i in np.where(d['close'] > TEND)[0]:
            if not np.isfinite(f[i]) or abs(f[i]) < 1.5: continue
            sg = 1 if f[i] < 0 else -1; t0 = d['close'][i] + 900_000
            a, b_ = np.searchsorted(bt, t0), np.searchsorted(bt, t0 + 86_400_000)
            if b_ >= len(bt) or a >= len(bt) or abs(bt[a] - t0) > 3_600_000 or abs(bt[b_] - (t0 + 86_400_000)) > 3_600_000: continue
            rr.append((str(d['date'][i]), f[i], sg * (bv[b_] / bv[a] - 1) * 100 - COST * 100))
        if rr:
            x = np.array([q[2] for q in rr])
            P(f"   after the data end (Yahoo BTC-USD 1h, {rr[0][0]} … {rr[-1][0]}): n={len(x)} avg {x.mean():+.2f}% net, positive {(x > 0).sum()}/{len(x)}: "
              + ', '.join(f"{q[0]} nk {q[1]:+.1f}% → {q[2]:+.2f}%" for q in rr))
    return res


# ------------------------------------------------------------------------------------------------ 4. autopilot
NORMA = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40, d_share=0.15, d_open=3)


def autopilot():
    import autopilot_v2 as AP
    P('\n=== 4. AUTOPILOT «норма»+D with pre-registered macro filters (autopilot_v2.simulate) ===')
    pk = os.path.join(SRC, 'macro_base_cands.pkl')
    if os.path.exists(pk) and os.path.getmtime(pk) > os.path.getmtime(os.path.join(HERE, 'autopilot_v2.py')):
        B = pickle.load(open(pk, 'rb'))
    else:
        B = AP.cand_B() + AP.cand_E(0.4) + AP.cand_F() + AP.cand_D(); pickle.dump(B, open(pk, 'wb'))

    def run(c):
        eq, tr, cu = AP.simulate(c, rules=('B', 'E', 'F', 'D'), **NORMA); r, _ = AP.report(eq, tr, cu); return r
    base = run(B)
    yl = lambda r: ' '.join(f"{r['years'].get(str(y), 0):>7,}" for y in YEARS)
    better = lambda r: sum(r['years'].get(str(y), 0) > base['years'].get(str(y), 0) for y in YEARS)
    P(f"{'база «норма»+D':44} ${base['final']:>8,} DD {base['maxdd'] * 100:5.1f}% n={base['n']:5d} | {yl(base)}")
    # shock days (2023–2026): union of the risk-off conditions; times = factor close (ms)
    def shock_times(labels):
        out = set()
        for lab, name, cond, sgn in conditions():
            if lab not in labels or daily(name) is None: continue
            d, samp, hit, Y = ev_outcomes(name, cond, YEARS + (2022,), gap_days=0)
            out |= set(int(x) for x in d['close'][hit])
        return np.array(sorted(out), np.int64)
    RISKOFF = ('Nasdaq ≤ −3%', 'VIX ≥ +30%', 'USDJPY ≤ −2%')
    RISKOFF2 = ('Nasdaq ≤ −2%', 'VIX ≥ +20%', 'USDJPY ≤ −1.5%', 'US10Y ≥ +15 bp', 'DXY ≥ +1%')
    st1, st2 = shock_times(RISKOFF), shock_times(RISKOFF2)
    P(f"shock closes 2023–26: strict {len(st1)} ({', '.join(RISKOFF)}); wide {len(st2)} ({', '.join(RISKOFF2)})")
    ti = times

    def by_entry(c, st, h):
        tt = ti[c['i']]; j = np.searchsorted(st, tt, side='right') - 1
        return j >= 0 and tt - st[j] < h * 3_600_000
    # how do the candidate trades entered after shocks do? (before portfolio caps)
    P('\n--- candidate trades by entry within H hours after a shock close (all signals, before caps) ---')
    for nm, st in (('strict', st1), ('wide', st2)):
        for h in (24, 72):
            g = collections.defaultdict(list)
            for c in B:
                if c['i'] < AP.START: continue
                g[(c['rule'], by_entry(c, st, h))].append(c['ret'] * 100)
            P(f"  {nm:6} {h:2d}h: " + ' | '.join(f"{r} {'after' if a else 'other'} n={len(v)} avg {np.mean(v):+.2f}%"
                                                 for (r, a), v in sorted(g.items()) if v))

    def filt(st, h, rules=None, flatten=False):
        out = []
        for c in B:
            if (rules is None or c['rule'] in rules) and by_entry(c, st, h): continue
            out.append(c)
        return out
    variants = [('strict: нет входов 24 ч после шока', st1, 24, None), ('strict: нет входов 72 ч после', st1, 72, None),
                ('strict: E не входит 24 ч после', st1, 24, ('E',)), ('strict: B не входит 24 ч после', st1, 24, ('B',)),
                ('wide: нет входов 24 ч после', st2, 24, None), ('wide: E не входит 24 ч после', st2, 24, ('E',)),
                ('wide: нет входов 72 ч после', st2, 72, None), ('wide: F/D не входят 24 ч после', st2, 24, ('F', 'D'))]
    real = []
    P(f"\n{'variant':44} {'final':>9} {'maxDD':>8} {'trades':>7} | {'2023':>7} {'2024':>7} {'2025':>7} {'2026':>7} | better years")
    for lab, st, h, rl in variants:
        r = run(filt(st, h, rl)); real.append((lab, r))
        P(f"{lab:44} ${r['final']:>8,} DD {r['maxdd'] * 100:5.1f}% n={r['n']:5d} | {yl(r)} | {better(r)}/4")
    # null: random shock days (same count per year, NY 16:00 close of random US trading days) — selection-aware (best of the 8)
    spx = daily('SPX'); pool = {y: spx['close'][(spx['year'] == y)] for y in YEARS + (2022,)}
    rng = np.random.default_rng(51); nb, ng = [], []
    cnt1 = collections.Counter(year_of(st1)); cnt2 = collections.Counter(year_of(st2))
    for rep in range(30):
        f1 = np.array(sorted(np.concatenate([rng.choice(pool[y], cnt1[y], replace=False) for y in cnt1 if y in pool])), np.int64)
        f2 = np.array(sorted(np.concatenate([rng.choice(pool[y], cnt2[y], replace=False) for y in cnt2 if y in pool])), np.int64)
        ps = []
        for lab, st, h, rl in variants:
            r = run(filt(f1 if st is st1 else f2, h, rl))
            if better(r) == 4: ps.append(r['final'] - base['final'])
            ng.append(r['final'] - base['final'])
        nb.append(len(ps))
    rp = [r['final'] - base['final'] for _, r in real if better(r) == 4]
    ng = np.array(ng)
    P(f"\nnull (30 random shock calendars × 8 variants): variants better in all 4 years — median {np.median(nb):.0f} of 8; "
      f"gain vs base: median ${np.median(ng):+,.0f}, p90 ${np.percentile(ng, 90):+,.0f}; real: {len(rp)} pass 4/4, gains {[f'{x:+,}' for x in rp]}")
    for lab, r in real:
        P(f"   {lab:44} gain ${r['final'] - base['final']:+8,} → beats {(r['final'] - base['final'] > ng).mean() * 100:3.0f}% of random-date variants")
    # rule F (OI flush) after shocks: by day (F enters in batches across coins), then the F-only filter family
    P('\n--- rule F entries within H h after a shock close, by day (F enters several coins at once) ---')
    for nm, st in (('strict', st1), ('wide', st2)):
        for h in (12, 24, 48):
            g = collections.defaultdict(list)
            for c in B:
                if c['rule'] == 'F' and c['i'] >= AP.START and by_entry(c, st, h): g[AP.DAY[c['i']]].append(c['ret'] * 100)
            dd = np.array([np.mean(v) for v in g.values()])
            P(f"  {nm:6} ≤{h:2d}h: trades {sum(len(v) for v in g.values()):3d}, days {len(dd):2d}, day-avg {dd.mean():+.2f}% (t {tstat(dd):+.1f}), "
              f"negative {(dd < 0).sum()}/{len(dd)}" + (' | ' + ', '.join(f"{k} {np.mean(v):+.1f}%({len(v)})" for k, v in sorted(g.items())) if h == 24 else ''))
    fvar = [(f'{nm} {"/".join(rl)} {h}h', st, h, rl) for nm, st in (('strict', st1), ('wide', st2)) for rl in (('F',), ('F', 'D')) for h in (12, 24, 48)]
    freal = {}
    for lab, st, h, rl in fvar:
        r = run(filt(st, h, rl)); freal[lab] = r
        P(f"  {lab:22} ${r['final']:>8,} DD {r['maxdd'] * 100:5.1f}% | {yl(r)} | {better(r)}/4 | gain {r['final'] - base['final']:+,}")
    bests, npass = [], []
    for rep in range(40):
        f1 = np.array(sorted(np.concatenate([rng.choice(pool[y], cnt1[y], replace=False) for y in cnt1 if y in pool])), np.int64)
        f2 = np.array(sorted(np.concatenate([rng.choice(pool[y], cnt2[y], replace=False) for y in cnt2 if y in pool])), np.int64)
        gs, k = [], 0
        for lab, st, h, rl in fvar:
            r = run(filt(f1 if st is st1 else f2, h, rl)); gs.append(r['final'] - base['final']); k += better(r) == 4
        bests.append(max(gs)); npass.append(k)
    bests = np.array(bests); rb = max(r['final'] - base['final'] for r in freal.values())
    P(f"  selection-aware null (40 random calendars × {len(fvar)}): best gain median ${np.median(bests):+,.0f}, p95 ${np.percentile(bests, 95):+,.0f}; "
      f"real best ${rb:+,} → beats {(rb > bests).mean() * 100:.0f}%; variants 4/4 — real {sum(better(r) == 4 for r in freal.values())}, "
      f"null median {np.median(npass):.0f} (p90 {np.percentile(npass, 90):.0f})")
    # Nikkei reversal as an extra lab rule 'n' (BTC, share of equity, 1 position at a time)
    P('\n--- «норма»+D + Nikkei reversal (rule n: BTC, share of equity per trade, at most 1 open) ---')
    P(f"{'variant':44} {'final':>9} {'maxDD':>8} {'trades':>7} | {'2023':>7} {'2024':>7} {'2025':>7} {'2026':>7} | better years | n-rule $")

    def run_n(cn, share):
        eq, tr, cu = AP.simulate(B + cn, rules=('B', 'E', 'F', 'D', 'n'), extra={'n': dict(share=share, open=1)}, **NORMA)
        r, _ = AP.report(eq, tr, cu); return r
    nres = []
    for th, w, sides in N_VARIANTS:
        for share in (0.15, 0.25):
            r = run_n(nikkei_cands(th, w, sides, YEARS), share); lab = f"n: th {th}% {w[0]:g}…{w[1]:g}h {sides} share {share}"
            nres.append((lab, r))
            P(f"{lab:44} ${r['final']:>8,} DD {r['maxdd'] * 100:5.1f}% n={r['n']:5d} | {yl(r)} | {better(r)}/4 | {r['rules'].get('n', 0):+,}")
    # null: Nikkei changes circularly shifted (same distribution and clustering, wrong dates) → best of the 16 variants
    f = daily('NIKKEI')['chg'] * 100; nb2, ng2 = [], []
    for rep in range(20):
        ff = np.roll(f, int(rng.integers(20, len(f) - 20))); ps = []
        for th, w, sides in N_VARIANTS:
            for share in (0.15, 0.25):
                r = run_n(nikkei_cands(th, w, sides, YEARS, ff), share)
                ng2.append(r['final'] - base['final'])
                if better(r) == 4: ps.append(r['final'] - base['final'])
        nb2.append(len(ps))
    ng2 = np.array(ng2); rp2 = [r['final'] - base['final'] for _, r in nres if better(r) == 4]
    P(f"null (20 shifted Nikkei calendars × 16): variants better than base in all 4 years — median {np.median(nb2):.0f} of 16 (p90 "
      f"{np.percentile(nb2, 90):.0f}); gain median ${np.median(ng2):+,.0f}, p90 ${np.percentile(ng2, 90):+,.0f}; real: {len(rp2)} pass 4/4")
    for lab, r in nres:
        P(f"   {lab:44} gain ${r['final'] - base['final']:+8,} → beats {(r['final'] - base['final'] > ng2).mean() * 100:3.0f}% of shifted-calendar variants")
    return base, real


if __name__ == '__main__':
    what = [a for a in sys.argv[1:]] or ['explain', 'hourly', 'predict', 'extreme', 'diverge', 'liquidity', 'nikkei', 'events', 'autopilot']
    if not RAW and set(what) - {'events', 'autopilot'}:
        print(f'factor data not found: {FJSON} (run research-macro.yml and scripts/macro_factors_fetch.py --decode)'); sys.exit(1)
    P(f"crypto data {dt.datetime.fromtimestamp(T0 / 1000, UTC):%Y-%m-%d} → {dt.datetime.fromtimestamp(TEND / 1000, UTC):%Y-%m-%d %H:%M}; "
      f"factor series {len(RAW)}")
    for w in what:
        globals()[w]()
    os.makedirs(os.path.join(SCRATCH, 'macro'), exist_ok=True)
    open(os.path.join(SCRATCH, 'macro', f"macro_factors_out_{'_'.join(what)}.txt"), 'w').write('\n'.join(OUT))
