"""US macro events (FOMC decision 14:00 ET, Fed chair press conference 14:30 ET, CPI 08:30 ET, NFP / Employment Situation
08:30 ET) and crypto — 18 coins, 15m, 2023-01 → 2026-09 (extra out-of-sample check on 2021–2022 events), cost 0.14 % round trip.
Event list: scripts/macro_events.json (official Fed / BLS dates, UTC with US DST).

Sections (default: all)
  study     event study: log return and realised volatility of BTC, ETH and the equal-weight 18-coin basket in windows
            −48…−24h, −24…0h, 0…+1h, +1…+4h, +4…+24h, +24…+72h; baseline = the same ET weekday and clock time 1–12 weeks
            before/after the event in weeks with no FOMC/CPI/NFP within ±72 h. Each event = one observation (t-stats).
  rules     tradable rules after costs: pre-event drift (long/short from −48/−24/−12/−6 h to the event ±2 h),
            post-event fade / follow (first 30/60/120 min move, hold 4/24 h, size thresholds); per year, ±1–2 h shifts.
  autopilot portfolio «норма»+D (autopilot_v2.simulate, ≈ $70.6k): skip new entries N h before events, also after,
            flatten before the event; per year; ±1–2 h shifted calendars; placebo calendars (random non-event weeks).
  oos       2021–22 check of the post-event idea on E/B candidates (F/D need real OI/basis metrics, imputed before 2023).
  null      selection-aware null for the 74 autopilot filters: the whole set on 20 fake calendars (slow, ~15 min).
Usage: python scripts/macro_events_test.py [study] [rules] [autopilot] [oos] [null]
docs/research/macro_events.md"""
import sys, os, json, pickle, collections, warnings, datetime as dt, zoneinfo, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as AP
from ap_data import SRC

np.seterr(all='ignore'); warnings.filterwarnings('ignore')
times, NS, T, START, COST = AP.times, AP.NS, AP.T, AP.START, AP.COST
O, H, L, C = AP.O, AP.H, AP.L, AP.C
SYMS = [str(s).replace('USDT', '') for s in AP.syms]
BTC, ETH = SYMS.index('BTC'), SYMS.index('ETH')
NY = zoneinfo.ZoneInfo('America/New_York')
HB = 4                                        # bars per hour
YEARS = (2023, 2024, 2025, 2026)
NORMA = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40, d_share=0.15, d_open=3)
WINDOWS = [(-48, -24), (-24, 0), (0, 1), (1, 4), (4, 24), (24, 72)]
TYPES = ('FOMC', 'FOMC_PRESSER', 'CPI', 'NFP')
EV_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'macro_events.json')
OUT = []


def P(s=''):
    print(s, flush=True); OUT.append(s)


# ------------------------------------------------------------------------------------------------ events
def ms(d):
    return int(d.timestamp() * 1000)


def load_events():
    raw = json.load(open(EV_JSON))['events']
    ev = []
    for e in raw:
        y, m, d = map(int, e['date_et'].split('-')); hh, mm = map(int, e['time_et'].split(':'))
        et = dt.datetime(y, m, d, hh, mm, tzinfo=NY)
        u = et.astimezone(dt.timezone.utc)
        assert u.strftime('%Y-%m-%dT%H:%MZ') == e['utc'], e          # JSON UTC = ET + DST rule
        ev.append(dict(e, et=et, t=ms(u), year=y))
    return ev


EV = load_events()
ALL_T = np.array(sorted(e['t'] for e in EV if e['type'] in ('FOMC', 'CPI', 'NFP')))
T0, T1 = int(times[0]), int(times[-1])


def bar(t):
    """index of the 15m bar that opens at time t (ms), None if outside data."""
    if t < T0 or t > T1: return None
    i = int(np.searchsorted(times, t))
    return i if i < T and times[i] == t else None


def near_event(t, before_h=72, after_h=72):
    j = np.searchsorted(ALL_T, t - before_h * 3600_000)
    return j < len(ALL_T) and ALL_T[j] <= t + after_h * 3600_000


def baseline_times(e, kmax=12):
    """same ET weekday + clock time, k = ±1..±kmax weeks, no FOMC/CPI/NFP within ±72 h, inside data."""
    out = []
    for k in list(range(-kmax, 0)) + list(range(1, kmax + 1)):
        x = e['et'] + dt.timedelta(weeks=k)                        # wall-clock arithmetic in America/New_York
        t = ms(x.astimezone(dt.timezone.utc))
        if near_event(t): continue
        if bar(t - 48 * 3600_000) is None or bar(t + 72 * 3600_000) is None: continue
        out.append(t)
    return out


def usable(e, pre=48, post=72):
    return bar(e['t'] - pre * 3600_000) is not None and bar(e['t'] + post * 3600_000) is not None


# ------------------------------------------------------------------------------------------------ series
with np.errstate(all='ignore'):
    LR = np.full_like(C, np.nan); LR[:, 1:] = np.log(C[:, 1:] / C[:, :-1])   # 15m log returns (bar j: close j-1 → close j)
    LO = np.log(O)
BASK_R = np.nanmean(LR, 0)                                                     # equal-weight basket 15m log return
ASSETS = ('BTC', 'ETH', 'basket')


def wret(asset, i, j):
    """log return from the open of bar i to the open of bar j."""
    if asset == 'basket':
        r = LO[:, j] - LO[:, i]; r = r[np.isfinite(r)]
        return r.mean() if len(r) >= 5 else np.nan
    k = BTC if asset == 'BTC' else ETH
    return LO[k, j] - LO[k, i]


def wvol(asset, i, j):
    """realised volatility from bar i to bar j (sum of squared 15m log returns, as % per sqrt-hour)."""
    r = BASK_R[i:j] if asset == 'basket' else LR[BTC if asset == 'BTC' else ETH, i:j]
    r = r[np.isfinite(r)]
    return np.sqrt((r * r).sum() / max(len(r), 1) * HB) if len(r) else np.nan


def tstat(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) == 0: return np.nan
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


# ------------------------------------------------------------------------------------------------ 1. event study
def event_rows(typ, sample='main', shift_h=0.0):
    rows = []
    for e in EV:
        if e['type'] != typ or e['sample'] != sample or e.get('status') != 'released' or not usable(e): continue
        t = e['t'] + int(shift_h * 3600_000)
        bl = baseline_times(e)
        if len(bl) < 3: continue
        rec = dict(e=e, year=e['year'], wd=e['et'].strftime('%a'))
        for a in ASSETS:
            for (w0, w1) in WINDOWS:
                i, j = bar(t + w0 * 3600_000), bar(t + w1 * 3600_000)
                r_ev, v_ev = wret(a, i, j), wvol(a, i, j)
                rb = []; vb = []
                for tb in bl:
                    ib, jb = bar(tb + w0 * 3600_000), bar(tb + w1 * 3600_000)
                    rb.append(wret(a, ib, jb)); vb.append(wvol(a, ib, jb))
                rb = np.array(rb, float); rec[(a, w0, w1)] = (r_ev, np.nanmean(rb), v_ev, np.nanmean(vb), np.nanmean(np.abs(rb)), np.mean(rb[np.isfinite(rb)] < 0))
        rows.append(rec)
    return rows


def study():
    P('=== 1. EVENT STUDY (log returns in %, each event = one observation; baseline = same ET weekday+time, ±1..12 weeks, '
      'no FOMC/CPI/NFP within ±72 h) ===')
    P(f'data {AP.DAY[0]} → {AP.DAY[-1]}; events in the main sample need −48 h…+72 h of data')
    cnt = collections.Counter((e['type'], e['year']) for e in EV if e['sample'] == 'main' and e.get('status') == 'released' and usable(e))
    P('usable events: ' + ', '.join(f"{t} " + '/'.join(str(cnt[(t, y)]) for y in YEARS) for t in TYPES) + '  (2023/24/25/26)')
    res = {}
    for typ in TYPES:
        rows = event_rows(typ); res[typ] = rows
        P(f'\n--- {typ}: n={len(rows)} ---')
        P(f"{'asset':7} {'window':>10} {'event':>7} {'base':>7} {'excess':>7} {'t':>5} {'%neg':>5} {'base%neg':>8} "
          f"{'|r| ev':>7} {'|r| bs':>7} {'vol×':>5} {'t(lnvol)':>8}   excess by year 2023/24/25/26 (t)")
        for a in ASSETS:
            for (w0, w1) in WINDOWS:
                x = np.array([r[(a, w0, w1)] for r in rows], float) * np.array([100, 100, 1, 1, 100, 100])
                ex = x[:, 0] - x[:, 1]; lv = np.log(x[:, 2] / x[:, 3])
                yy = np.array([r['year'] for r in rows])
                py = ' '.join(f"{ex[yy == y].mean():+5.2f}({tstat(ex[yy == y]):+4.1f})" if (yy == y).sum() else '   -    ' for y in YEARS)
                P(f"{a:7} {w0:+4d}…{w1:+3d}h {np.nanmean(x[:, 0]):+7.2f} {np.nanmean(x[:, 1]):+7.2f} {np.nanmean(ex):+7.2f} "
                  f"{tstat(ex):+5.1f} {(x[:, 0] < 0).mean() * 100:5.0f} {np.nanmean(x[:, 5]):8.0f} {np.nanmean(np.abs(x[:, 0])):7.2f} "
                  f"{np.nanmean(x[:, 4]):7.2f} {np.exp(np.nanmean(lv)):5.2f} {tstat(lv):+8.1f}   {py}")
        yy = np.array([r['year'] for r in rows])
        for a in ('BTC', 'basket'):
            s = []
            for (w0, w1) in ((-24, 0), (0, 1), (1, 4)):
                x = np.array([r[(a, w0, w1)] for r in rows], float); lv = np.log(x[:, 2] / x[:, 3])
                s.append(f"{w0:+d}…{w1:+d}h " + '/'.join(f"{np.exp(np.nanmean(lv[yy == y])):.2f}" for y in YEARS))
            P(f"   vol× by year 2023/24/25/26, {a:6}: " + ' | '.join(s))
    # extra sample: 2021–2022 events (BTC/ETH/basket of the coins listed then)
    P('\n--- out-of-sample check: 2021–2022 events (same method) — excess return % (t) and vol× for the key windows ---')
    for typ in TYPES:
        rows = event_rows(typ, sample='extra_2021_2022')
        s = []
        for a in ASSETS:
            for (w0, w1) in ((-24, 0), (0, 1), (1, 4), (4, 24)):
                x = np.array([r[(a, w0, w1)] for r in rows], float)
                ex = (x[:, 0] - x[:, 1]) * 100; lv = np.log(x[:, 2] / x[:, 3])
                s.append(f"{a} {w0:+d}…{w1:+d}h {np.nanmean(ex):+.2f}({tstat(ex):+.1f}) vol×{np.exp(np.nanmean(lv)):.2f}")
        P(f'{typ} n={len(rows)}: ' + ' | '.join(s))
    # placement check: is the 0…+1h vol jump anchored at the release minute? shift the event clock ±1/±2 h
    P('\n--- anchor check: BTC vol× in 0…+1h when the event clock is shifted (main sample) ---')
    for typ in ('FOMC', 'CPI', 'NFP'):
        s = []
        for sh in (-2, -1, 0, 1, 2):
            rows = event_rows(typ, shift_h=sh)
            x = np.array([r[('BTC', 0, 1)] for r in rows], float)
            s.append(f"{sh:+d}h: vol×{np.exp(np.nanmean(np.log(x[:, 2] / x[:, 3]))):.2f} ret {np.nanmean((x[:, 0] - x[:, 1]) * 100):+.2f}%")
        P(f'{typ:5} ' + ' | '.join(s))
    # tail risk for the information card: BTC |move| in the first hour vs baseline
    P('\n--- BTC first-hour move size (information card) ---')
    for typ in ('FOMC', 'CPI', 'NFP'):
        rows = res[typ]
        mv = np.abs(np.array([r[('BTC', 0, 1)][0] for r in rows])) * 100
        bm = []
        for r in rows:
            for tb in baseline_times(r['e']):
                bm.append(abs(wret('BTC', bar(tb), bar(tb + 3600_000))) * 100)
        bm = np.array(bm)
        P(f"{typ:5} event: median {np.median(mv):.2f}%  p90 {np.percentile(mv, 90):.2f}%  >1%: {(mv > 1).mean() * 100:.0f}%  max {mv.max():.1f}% | "
          f"baseline: median {np.median(bm):.2f}%  p90 {np.percentile(bm, 90):.2f}%  >1%: {(bm > 1).mean() * 100:.0f}%")
    # multiple testing reminder
    nt = len(TYPES) * len(ASSETS) * len(WINDOWS)
    P(f'\n{nt} return tests above (+ {nt} vol tests); with no effect at all ≈ {nt * 0.05:.0f} would show |t| ≥ 2 by chance.')
    return res


# ------------------------------------------------------------------------------------------------ 2. tradable rules
def px(asset, i):
    if asset == 'basket': return O[:, i]
    return O[BTC if asset == 'BTC' else ETH, i]


def trade(asset, i, j, side):
    """net simple return (after COST) of a position from the open of bar i to the open of bar j; basket = equal weight."""
    a, b = px(asset, i), px(asset, j)
    r = side * (b / a - 1) - COST
    if asset == 'basket':
        r = r[np.isfinite(r)]; return r.mean() if len(r) >= 5 else np.nan
    return r


def events_of(typ, sample='main'):
    types = ('FOMC', 'CPI', 'NFP') if typ == 'ALL' else (typ,)
    return [e for e in EV if e['type'] in types and e['sample'] == sample and e.get('status') == 'released' and usable(e)]


def summ(rs, ys):
    rs = np.asarray(rs, float); ys = np.asarray(ys); ok = np.isfinite(rs); rs, ys = rs[ok], ys[ok]
    per = {y: (rs[ys == y].mean() * 100 if (ys == y).any() else np.nan) for y in YEARS}
    return dict(n=len(rs), avg=rs.mean() * 100, t=tstat(rs), win=(rs > 0).mean() * 100, tot=rs.sum() * 100, per=per,
                allpos=all(np.isfinite(v) and v > 0 for v in per.values()))


def fmt(s):
    return (f"n={s['n']:3d} avg {s['avg']:+6.2f}% t {s['t']:+4.1f} win {s['win']:3.0f}% sum {s['tot']:+7.1f}% | "
            + ' '.join(f"{s['per'][y]:+6.2f}" for y in YEARS) + ('  ALL YEARS +' if s['allpos'] else ''))


def ev_times(typ, sample='main', tmap=None):
    """(time, year) of the events; tmap (placebo) maps an event's real time to a fake one."""
    out = []
    for e in events_of(typ, sample):
        t = tmap.get(e['t'], None) if tmap is not None else e['t']
        if t is None: continue
        out.append((t, int(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year)))
    return out


def pre_drift(typ, asset, eh, xh, side, sample='main', tmap=None):
    rs, ys = [], []
    for t, y in ev_times(typ, sample, tmap):
        i, j = bar(t + int(eh * 3600_000)), bar(t + int(xh * 3600_000))
        if i is None or j is None: continue
        rs.append(trade(asset, i, j, side)); ys.append(y)
    return summ(rs, ys)


def post_fade(typ, asset, mh, hh, mode, thr=0.0, delay_bars=0, sample='main', tmap=None):
    """first move = open(t) → open(t+mh); entry at t+mh (+delay); fade (mode=-1) or follow (+1) it for hh hours."""
    rs, ys = [], []
    for t, y in ev_times(typ, sample, tmap):
        e = dict(t=t, year=y)
        i0, i1 = bar(e['t']), bar(e['t'] + int(mh * 3600_000))
        if i0 is None or i1 is None: continue
        mv = wret(asset, i0, i1)
        if not np.isfinite(mv) or abs(mv) < thr or mv == 0: continue
        ie = i1 + delay_bars; ix = ie + int(hh * HB)
        if ix >= T: continue
        rs.append(trade(asset, ie, ix, mode * np.sign(mv))); ys.append(e['year'])
    return summ(rs, ys)


def rule_grid(tmap=None, quiet=True):
    grid = []
    for typ in ('FOMC', 'CPI', 'NFP'):
        for asset in ASSETS:
            for eh in (-48, -24, -12, -6):
                for side in (1, -1):
                    s = pre_drift(typ, asset, eh, 0, side, tmap=tmap); grid.append(('pre', typ, asset, eh, 0, side, s))
                    if not quiet: P(f"{typ:4} {asset:6} {'long ' if side > 0 else 'short'} {eh:+3d}h→0h  {fmt(s)}")
    if not quiet: P('\n--- 2b. post-event fade / follow: first move over M min, hold H h ---')
    for typ in ('FOMC', 'CPI', 'NFP'):
        for asset in ASSETS:
            for mh in (0.5, 1, 2):
                for hh in (4, 24):
                    for mode in (-1, 1):
                        s = post_fade(typ, asset, mh, hh, mode, tmap=tmap); grid.append(('post', typ, asset, mh, hh, mode, s))
                        if not quiet: P(f"{typ:4} {asset:6} {'fade  ' if mode < 0 else 'follow'} M={mh:3}h H={hh:2d}h  {fmt(s)}")
    return grid


_BL = {}
PLACEBO_AVG = {}


def placebo_map(rng, kmax=12):
    """each real event time → a random baseline time (same ET weekday + clock, non-event week ±1..kmax)."""
    m = {}
    for e in EV:
        if e['type'] not in ('FOMC', 'CPI', 'NFP') or e['sample'] != 'main' or e.get('status') != 'released' or not usable(e): continue
        if e['t'] not in _BL: _BL[e['t']] = baseline_times(e, kmax)
        if _BL[e['t']]: m[e['t']] = int(rng.choice(_BL[e['t']]))
    return m


def profile():
    """cumulative excess log return (event − baseline mean), % from −6 h to each offset; BTC and basket."""
    offs = (-4, -2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2, 4)
    P('\n--- 2c. path around the release: cumulative excess return from −6 h (event − same-weekday baseline), % (t) ---')
    P(f"{'':12} " + ' '.join(f"{o:+7g}h" for o in offs))
    for typ in ('FOMC', 'CPI', 'NFP'):
        for asset in ('BTC', 'basket'):
            row = []
            for o in offs:
                ex = []
                for e in events_of(typ):
                    i0, i1 = bar(e['t'] - 6 * 3600_000), bar(e['t'] + int(o * 3600_000))
                    b = [wret(asset, bar(tb - 6 * 3600_000), bar(tb + int(o * 3600_000))) for tb in baseline_times(e)]
                    ex.append((wret(asset, i0, i1) - np.nanmean(b)) * 100)
                row.append(f"{np.nanmean(ex):+5.2f}({tstat(ex):+.1f})")
            P(f"{typ:4} {asset:7} " + ' '.join(f"{r:>8}" for r in row))


def rules():
    P('\n=== 2. TRADABLE RULES (net of 0.14 % per trade; avg per trade %, per-year avg 2023/24/25/26) ===')
    P('\n--- 2a. pre-event drift: enter at −E h, exit at the event (0 h) ---')
    grid = rule_grid(quiet=False)
    nall = sum(1 for g in grid if g[-1]['allpos'])
    P(f'\n{len(grid)} variants tested; {nall} are positive after costs in every year 2023–2026.')
    # empirical null: the same 180 variants on fake calendars (every event moved to a random non-event week)
    rng = np.random.default_rng(11); cnt = []; keep = collections.defaultdict(list); bmin = []; bt = []
    worst = lambda s: min(v for v in s['per'].values() if np.isfinite(v))
    pmaps = [placebo_map(rng) for _ in range(40)]
    for pm in pmaps:
        g2 = rule_grid(tmap=pm); cnt.append(sum(1 for g in g2 if g[-1]['allpos']))
        for g in g2: keep[g[:6]].append(g[-1]['avg'])
        bmin.append(max(worst(g[-1]) for g in g2 if g[-1]['n'] >= 15)); bt.append(max(g[-1]['t'] for g in g2 if g[-1]['n'] >= 15))
    cnt, bmin, bt = np.array(cnt), np.array(bmin), np.array(bt)
    P(f'placebo (40 fake calendars, same weekday/clock, non-event weeks): variants positive in every year — median {np.median(cnt):.0f}, '
      f'p10 {np.percentile(cnt, 10):.0f}, p90 {np.percentile(cnt, 90):.0f}; real calendar {nall} → beats {(nall > cnt).mean() * 100:.0f}% of placebos')
    rmin = max(worst(g[-1]) for g in grid if g[-1]['n'] >= 15); rt = max(g[-1]['t'] for g in grid if g[-1]['n'] >= 15)
    P(f'best-of-180 (the selection we actually do): worst-year avg of the best variant — real {rmin:+.2f}%, placebo median {np.median(bmin):+.2f}% '
      f'p90 {np.percentile(bmin, 90):+.2f}% (real beats {(rmin > bmin).mean() * 100:.0f}%); best t — real {rt:+.1f}, placebo median '
      f'{np.median(bt):+.1f} p90 {np.percentile(bt, 90):+.1f} (real beats {(rt > bt).mean() * 100:.0f}%)')
    PLACEBO_AVG.update(keep)
    best = sorted([g for g in grid if g[-1]['n'] >= 15], key=lambda g: -min(v for v in g[-1]['per'].values() if np.isfinite(v)))[:8]
    P('\n--- best 8 by the worst year (min yearly avg) — robustness: ±1/±2 h shifts, size thresholds, 2021–22 out of sample ---')
    for g in best:
        kind, typ, asset, a, b, side, s = g
        pl = np.array(PLACEBO_AVG.get(g[:6], [np.nan]))
        ptxt = f"   placebo calendars: avg median {np.nanmedian(pl):+.2f}%, real beats {(s['avg'] > pl).mean() * 100:.0f}% of 40"
        if kind == 'pre':
            P(f"\n[{kind}] {typ} {asset} {'long' if side > 0 else 'short'} {a:+d}h→{b:+d}h  {fmt(s)}")
            P(ptxt)
            for de in (-2, -1, 1, 2):
                P(f"   entry {a + de:+d}h          {fmt(pre_drift(typ, asset, a + de, b, side))}")
            for dx in (-2, -1, -0.5, -0.25, 0.25, 1, 2):
                P(f"   exit  {b + dx:+g}h  {'':7}{fmt(pre_drift(typ, asset, a, b + dx, side))}")
            P(f"   2021–22 events      {fmt(pre_drift(typ, asset, a, b, side, sample='extra_2021_2022'))}")
        else:
            P(f"\n[{kind}] {typ} {asset} {'fade' if side < 0 else 'follow'} M={a}h H={b}h  {fmt(s)}")
            P(ptxt)
            for d in (1, 2, 4, 8):
                P(f"   entry +{d * 15:3d} min later   {fmt(post_fade(typ, asset, a, b, side, delay_bars=d))}")
            for thr in (0.003, 0.005, 0.01):
                P(f"   |move| ≥ {thr * 100:.1f}%        {fmt(post_fade(typ, asset, a, b, side, thr=thr))}")
            for hh in (b / 2, b * 2):
                P(f"   hold {hh:g} h           {fmt(post_fade(typ, asset, a, hh, side))}")
            P(f"   2021–22 events      {fmt(post_fade(typ, asset, a, b, side, sample='extra_2021_2022'))}")
    # pooled pre-announcement drift: all three event types together (one rule, not selected per type)
    P('\n--- pre-announcement drift, FOMC + CPI + NFP pooled: long from −E h to the release (or 15 min before it) ---')
    for asset in ASSETS:
        for eh in (-12, -8, -6, -4, -2):
            for xh in (-0.25, 0):
                s = pre_drift('ALL', asset, eh, xh, 1)
                pl = np.array([pre_drift('ALL', asset, eh, xh, 1, tmap=pm)['avg'] for pm in pmaps])
                o = pre_drift('ALL', asset, eh, xh, 1, sample='extra_2021_2022')
                P(f"ALL  {asset:6} long {eh:+3d}h→{xh:+g}h  {fmt(s)} | placebo median {np.median(pl):+.2f}% (real beats {(s['avg'] > pl).mean() * 100:.0f}%) "
                  f"| 2021–22 n={o['n']} {o['avg']:+.2f}% t {o['t']:+.1f}")
    for typ in ('FOMC', 'CPI', 'NFP'):
        s = pre_drift(typ, 'BTC', -6, -0.25, 1); o = pre_drift(typ, 'BTC', -6, -0.25, 1, sample='extra_2021_2022')
        P(f"{typ:4} BTC    long  -6h→-0.25h  {fmt(s)} | 2021–22 n={o['n']} {o['avg']:+.2f}% t {o['t']:+.1f}")
    # the owner's exact hypothesis: "price gets dumped before / after" = short into the event, short after the event
    P('\n--- owner\'s hypothesis as trades: short BTC / basket before and after (net) ---')
    for typ in ('FOMC', 'CPI', 'NFP', 'ALL'):
        for asset in ('BTC', 'basket'):
            P(f"{typ:4} {asset:6} short −24h→0h  {fmt(pre_drift(typ, asset, -24, 0, -1))}")
            P(f"{typ:4} {asset:6} short 0h→+24h  {fmt(pre_drift(typ, asset, 0, 24, -1))}")
            P(f"{typ:4} {asset:6} short 0h→+72h  {fmt(pre_drift(typ, asset, 0, 72, -1))}")
    profile()
    return grid


# ------------------------------------------------------------------------------------------------ 3. autopilot filter
def base_cands():
    pk = os.path.join(SRC, 'macro_base_cands.pkl')
    if os.path.exists(pk): return pickle.load(open(pk, 'rb'))
    b = AP.cand_B() + AP.cand_E(0.4) + AP.cand_F() + AP.cand_D(); pickle.dump(b, open(pk, 'wb')); return b


def ev_bars(types=('FOMC', 'CPI', 'NFP'), shift_h=0.0, custom=None):
    ts = custom if custom is not None else [e['t'] for e in EV if e['type'] in types and e.get('status') == 'released']
    out = []
    for t in ts:
        b = bar(t + int(shift_h * 3600_000))
        if b is not None and b >= START: out.append(b)
    return np.array(sorted(set(out)), int)


def apply_filter(cands, eb, pre_h=0, post_h=0, flatten=False, only=None):
    """drop entries with bar i in [event − pre_h, event + post_h); flatten: positions open across an event bar are closed at
    the open of the event bar (= the last price before the release), net of the same COST. only: rule letters the filter
    applies to (None = all)."""
    if len(eb) == 0: return list(cands)
    lo, hi = eb - int(pre_h * HB), eb + int(post_h * HB)
    out = []
    for c in cands:
        if only is not None and c['rule'] not in only:
            out.append(c); continue
        i = c['i']
        j = np.searchsorted(eb, i, side='right')            # first event bar > i
        jj = np.searchsorted(lo, i, side='right') - 1        # last window starting ≤ i
        if (pre_h or post_h) and jj >= 0 and i < hi[jj]: continue
        if flatten and j < len(eb) and eb[j] <= c['x']:
            e = eb[j]; k = c['k']
            ex = O[k, e] if np.isfinite(O[k, e]) else C[k, e - 1]
            c = dict(c, x=int(e), ret=ex / O[k, i] - 1 - COST)
        out.append(c)
    return out


def run_pf(cands):
    eq, tr, cu = AP.simulate(cands, rules=('B', 'E', 'F', 'D'), **NORMA)
    r, _ = AP.report(eq, tr, cu)
    return r


def pf_line(lab, r, base):
    ys = ' '.join(f"{r['years'].get(str(y), 0):>7,}" for y in YEARS)
    better = sum(r['years'].get(str(y), 0) > base['years'].get(str(y), 0) for y in YEARS)
    return (f"{lab:44} ${r['final']:>8,} DD {r['maxdd'] * 100:5.1f}% n={r['n']:5d} | {ys} | better years {better}/4")


def pf_variants():
    variants = []
    ALL3 = ('FOMC', 'CPI', 'NFP')
    for types, tl in ((ALL3, 'все'), (('FOMC',), 'FOMC'), (('CPI',), 'CPI'), (('NFP',), 'NFP'), (('FOMC', 'CPI'), 'FOMC+CPI')):
        for pre in (2, 4, 8, 12, 24):
            variants.append((f'{tl}: нет входов за {pre} ч до', types, pre, 0, False, None))
        variants.append((f'{tl}: нет входов −4…+4 ч', types, 4, 4, False, None))
        variants.append((f'{tl}: нет входов −12…+12 ч', types, 12, 12, False, None))
        variants.append((f'{tl}: закрыть перед событием', types, 0, 0, True, None))
        variants.append((f'{tl}: нет входов 4 ч до + закрыть', types, 4, 0, True, None))
        variants.append((f'{tl}: нет входов 12 ч до + закрыть', types, 12, 0, True, None))
    for types, tl in ((ALL3, 'все'), (('FOMC',), 'FOMC'), (('CPI',), 'CPI'), (('NFP',), 'NFP')):
        for post in (4, 12, 24):                       # after the release: window [0, +post)
            variants.append((f'{tl}: нет входов 0…+{post} ч после', types, 0, post, False, None))
            variants.append((f'{tl}: нет входов E 0…+{post} ч после', types, 0, post, False, ('E',)))
    return variants


def null_pf(nrep=20):
    """selection-aware null for the portfolio filters: all 74 variants on fake calendars (each event moved to a random
    non-event week ±1..6, same ET weekday/clock). How many pass «better than the base in all 4 years», and how big is the best?"""
    P('\n=== 3b. NULL for the 74 autopilot filters (fake calendars) ===')
    B = base_cands(); base = run_pf(B); variants = pf_variants()
    better = lambda r: sum(r['years'].get(str(y), 0) > base['years'].get(str(y), 0) for y in YEARS)
    real = [(lab, run_pf(apply_filter(B, ev_bars(types), pre, post, fl, only))) for lab, types, pre, post, fl, only in variants]
    rp = [r['final'] - base['final'] for _, r in real if better(r) == 4]
    P(f"real calendar: {len(rp)} of {len(variants)} pass 4/4 years, best gain ${max(rp, default=0):+,}")
    evs = [e for e in EV if e['type'] in ('FOMC', 'CPI', 'NFP') and e.get('status') == 'released' and bar(e['t']) is not None and bar(e['t']) >= START]
    bl = {e['t']: baseline_times(e, kmax=6) for e in evs}
    rng = np.random.default_rng(23); npass, gain = [], []
    for rep in range(nrep):
        fk = {e['t']: int(rng.choice(bl[e['t']])) for e in evs if bl[e['t']]}
        ps = []
        for lab, types, pre, post, fl, only in variants:
            fake = [fk[e['t']] for e in evs if e['type'] in types and e['t'] in fk]
            r = run_pf(apply_filter(B, ev_bars(custom=fake), pre, post, fl, only))
            if better(r) == 4: ps.append(r['final'] - base['final'])
        npass.append(len(ps)); gain.append(max(ps, default=0))
        P(f"  fake calendar {rep + 1:2d}: {len(ps):2d} variants pass 4/4, best gain ${max(ps, default=0):+,}")
    npass, gain = np.array(npass), np.array(gain)
    P(f"fake calendars: variants passing 4/4 — median {np.median(npass):.0f} (p10 {np.percentile(npass, 10):.0f}, p90 {np.percentile(npass, 90):.0f}); "
      f"best gain median ${np.median(gain):+,.0f} p90 ${np.percentile(gain, 90):+,.0f}; real: {len(rp)} pass, best ${max(rp, default=0):+,} "
      f"(beats {(max(rp, default=0) > gain).mean() * 100:.0f}% of fake calendars)")


def day_avg(v):
    """[(ret, day)] → (days, mean of per-day mean returns %, t over days): E enters in batches, a day is the real unit."""
    d = collections.defaultdict(list)
    for r, dd in v: d[dd].append(r)
    x = np.array([np.mean(a) for a in d.values()]) * 100
    return len(x), (x.mean() if len(x) else np.nan), tstat(x)


def e_after_by_day(cs, label):
    """E entries in the first 12 h after any FOMC/CPI/NFP vs all other E entries — trade-weighted and day-weighted."""
    eb = np.array(sorted({bar(e['t']) for e in EV if e['type'] in ('FOMC', 'CPI', 'NFP') and bar(e['t']) is not None}), int)
    g = collections.defaultdict(list)
    for c in cs:
        if c['rule'] != 'E': continue
        i = c['i']; j = np.searchsorted(eb, i, side='right'); since = (i - eb[j - 1]) / HB if j > 0 else 1e9
        g['after' if since < 12 else 'other'].append((c['ret'], AP.DAY[i]))
    for k in ('after', 'other'):
        n, m, t = day_avg(g[k]); r = np.array([x for x, _ in g[k]]) * 100
        P(f"{label} E {'0…12 ч после события' if k == 'after' else 'остальные':22} trades {len(r):5d} avg {r.mean():+6.2f}% | "
          f"days {n:4d} day-avg {m:+6.2f}% (t {t:+.1f})")


def oos_ap():
    """2021–2022 check for the post-event idea on the rules that can be built before 2023 (E and B; F and D need real
    OI / basis metrics, imputed before 2023): average trade by entry time relative to FOMC/CPI/NFP."""
    P('\n=== 3c. 2021–2022: E and B candidate trades by entry time relative to events ===')
    s0 = int(np.searchsorted(times, 1609459200000)); old = AP.START; AP.START = s0
    try:
        cs = [c for c in AP.cand_E(0.4) + AP.cand_B() if c['i'] < old]
    finally:
        AP.START = old
    for types, tl in ((('FOMC', 'CPI', 'NFP'), 'все'), (('NFP',), 'NFP'), (('FOMC',), 'FOMC'), (('CPI',), 'CPI')):
        eb = np.array(sorted({bar(e['t']) for e in EV if e['type'] in types and bar(e['t']) is not None}), int)
        grp = collections.defaultdict(list)
        for c in cs:
            i = c['i']; j = np.searchsorted(eb, i, side='right')
            since = (i - eb[j - 1]) / HB if j > 0 else 1e9; to_next = (eb[j] - i) / HB if j < len(eb) else 1e9
            g = '0…12 ч после' if since < 12 else ('12…0 ч до' if to_next <= 12 else 'прочее')
            grp[(c['rule'], g)].append((c['ret'], AP.DAY[i]))
        for (rl, g), v in sorted(grp.items()):
            r = np.array([x for x, _ in v]) * 100
            P(f"{tl:4} {rl} {g:14} n={len(r):5d} days={len(set(d for _, d in v)):4d} avg {r.mean():+6.2f}% win {(r > 0).mean() * 100:3.0f}%")
    e_after_by_day(cs, '2021–22')


def autopilot():
    P('\n=== 3. AUTOPILOT «норма»+D with event filters ===')
    B = base_cands()
    base = run_pf(B)
    P(f"{'variant':44} {'final':>9} {'maxDD':>8} {'trades':>7} | {'2023':>7} {'2024':>7} {'2025':>7} {'2026':>7} |")
    P(pf_line('база «норма»+D', base, base))
    # how the candidate trades sit around events — by ENTRY time only (classifying by exit would be biased: a trade that
    # "exits before the event" is mostly a stopped-out one)
    eb = ev_bars()
    P('\n--- candidate trades (all signals, before portfolio caps) by entry time relative to the nearest FOMC/CPI/NFP ---')
    P(f"{'rule':4} {'entry':16} {'n':>5} {'days':>5} {'avg':>7} {'win':>4} | per-year avg 2023/24/25/26")
    yr = np.array([int(d[:4]) for d in AP.DAY])
    grp = collections.defaultdict(list)
    for c in B:
        i = c['i']
        j = np.searchsorted(eb, i, side='right')              # eb[j] = next event bar > i; eb[j-1] = last event bar ≤ i
        to_next = (eb[j] - i) / HB if j < len(eb) else 1e9; since = (i - eb[j - 1]) / HB if j > 0 else 1e9
        if since < 4: g = '2) 0…4 ч после'
        elif since < 24: g = '3) 4…24 ч после'
        elif to_next <= 4: g = '1b) 4…0 ч до'
        elif to_next <= 24: g = '1a) 24…4 ч до'
        else: g = '4) вдали'
        grp[(c['rule'], g)].append((c['ret'], i))
    for (rl, g), v in sorted(grp.items()):
        r = np.array([x for x, _ in v]) * 100; ii = np.array([x for _, x in v]); y = yr[ii]
        days = len(set(AP.DAY[ii]))
        py = ' '.join(f"{r[y == q].mean():+6.2f}" if (y == q).any() else '     -' for q in YEARS)
        P(f"{rl:4} {g:16} {len(r):5d} {days:5d} {r.mean():+6.2f}% {(r > 0).mean() * 100:3.0f}% | {py}")
    e_after_by_day(B, '2023–26')
    res = {}
    P('\n--- filters (FOMC + CPI + NFP unless stated) ---')
    variants = pf_variants()
    ALL3 = ('FOMC', 'CPI', 'NFP')
    for lab, types, pre, post, fl, only in variants:
        r = run_pf(apply_filter(B, ev_bars(types), pre, post, fl, only)); res[lab] = r
        P(pf_line(lab, r, base))
    # robustness of every variant that beats the base in all 4 years: shift the event clock ±1/±2 h
    good = [v for v in variants if sum(res[v[0]]['years'].get(str(y), 0) > base['years'].get(str(y), 0) for y in YEARS) == 4]
    P(f'\n--- {len(good)} of {len(variants)} variants better than the base in all 4 years; shift check (event clock ±1/±2 h) ---')
    for lab, types, pre, post, fl, only in good:
        for sh in (-2, -1, 1, 2):
            P(pf_line(f'  {lab} shift {sh:+d}h', run_pf(apply_filter(B, ev_bars(types, sh), pre, post, fl, only)), base))
    # placebo: the same filters on fake calendars (each event moved to a random non-event week, same ET weekday/time)
    rng = np.random.default_rng(7)
    evs = [e for e in EV if e['type'] in ALL3 and e.get('status') == 'released' and bar(e['t']) is not None and bar(e['t']) >= START]
    bl = {id(e): baseline_times(e, kmax=6) for e in evs}
    P('\n--- placebo: 60 fake calendars (each event moved to a random non-event week ±1..6, same ET weekday/time) ---')
    key = [v for v in variants if v[0] in ('все: нет входов за 4 ч до', 'все: нет входов за 12 ч до', 'все: закрыть перед событием',
                                            'FOMC: нет входов за 24 ч до', 'FOMC: закрыть перед событием', 'все: нет входов E 0…+24 ч после')]
    for lab, types, pre, post, fl, only in key + [v for v in good if v not in key]:
        fin = []
        for _ in range(60):
            fake = [int(rng.choice(bl[id(e)])) for e in evs if e['type'] in types and bl[id(e)]]
            fin.append(run_pf(apply_filter(B, ev_bars(custom=fake), pre, post, fl, only))['final'])
        fin = np.array(fin); real = res[lab]
        P(f"{lab:44} real ${real['final']:>8,} | placebo median ${np.median(fin):>8,.0f} p10 ${np.percentile(fin, 10):>8,.0f} "
          f"p90 ${np.percentile(fin, 90):>8,.0f} | real beats {(real['final'] > fin).mean() * 100:3.0f}% of placebos")
    return base, res


if __name__ == '__main__':
    what = set(sys.argv[1:]) or {'study', 'rules', 'autopilot', 'oos', 'null'}
    if 'study' in what: study()
    if 'rules' in what: rules()
    if 'autopilot' in what: autopilot()
    if 'null' in what: null_pf()
    if 'oos' in what: oos_ap()
    if {'study', 'rules', 'autopilot'} <= what:
        open(os.path.join(SRC, 'macro_events_out.txt'), 'w').write('\n'.join(OUT))
