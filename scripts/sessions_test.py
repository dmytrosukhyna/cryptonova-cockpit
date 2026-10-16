"""Do trading-session opens/closes matter? 18 coins, 15m bars, 2023-01 → 2026-09 (dataset of autopilot_v2), cost 0.14 % round trip.
Session times are DST-aware (zoneinfo): Asia 00:00 UTC, London 08:00–16:30 London time, New York 09:30–16:00 NY time,
CME Friday close 16:00 / Sunday reopen 17:00 Chicago time.
  H1 opening-range breakout (London / NY open; range 15/30/60 min; hold 4 h or to session end; stop at other side of range)
  H2 session fades (Asia move faded at London open, London move at NY open, day move at NY close) and the mirror (follow)
  H3 time-of-day / weekday drift as fixed-time holds
  H4 CME weekend gap for BTC/ETH: fill probability and P&L
  H5 autopilot rules B/E/F by session of entry + portfolio session filters with ±1 h boundary shifts
Every variant is compared to a random-entry baseline: same coin, same direction, same UTC time of day, same stop/target
distance and holding time, but on a random day of the same year (5 draws).
Usage: python scripts/sessions_test.py [h1 h2 h3 h4 h5]   (default: all)"""
import sys, collections, datetime as dt, numpy as np
from zoneinfo import ZoneInfo
sys.path.insert(0, 'scripts')
import autopilot_v2 as AP

O, H, L, C, T, NS, COST, START, times = AP.O, AP.H, AP.L, AP.C, AP.T, AP.NS, AP.COST, AP.START, AP.times
syms = [str(s) for s in AP.syms]
BAR = 900_000
T0 = int(times[0])
assert T0 % 86_400_000 == 0 and np.all(np.diff(times) == BAR)
ND = T // 96
D0 = START // 96                                     # first day 2023-01-01
DAYS = [dt.date(1970, 1, 1) + dt.timedelta(days=int(T0 // 86_400_000) + d) for d in range(ND)]
YEAR = np.array([x.year for x in DAYS]); WDAY = np.array([x.weekday() for x in DAYS])
NDAYS = ND - D0                                       # calendar days in the test period
UTC = dt.timezone.utc
RNG = np.random.default_rng(7)


def local_bar(d, tz, hh, mm=0):
    """bar index of local time hh:mm in zone tz on calendar day d (UTC date)."""
    x = dt.datetime(DAYS[d].year, DAYS[d].month, DAYS[d].day, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    return int((x.timestamp() * 1000 - T0) // BAR)


LDN_O = np.array([local_bar(d, 'Europe/London', 8) for d in range(ND)])
LDN_C = np.array([local_bar(d, 'Europe/London', 16, 30) for d in range(ND)])
NY_O = np.array([local_bar(d, 'America/New_York', 9, 30) for d in range(ND)])
NY_C = np.array([local_bar(d, 'America/New_York', 16) for d in range(ND)])
CME_C = np.array([local_bar(d, 'America/Chicago', 16) for d in range(ND)])
CME_O = np.array([local_bar(d, 'America/Chicago', 17) for d in range(ND)])
ASIA = np.arange(ND) * 96
YRS = ('2023', '2024', '2025', '2026')


def run(k, ie, xe, sg, entry, stop=None, tgt=None):
    """Vectorised trade walker. Position held from bar ie (entry price `entry`) up to bar xe-1, exit at close of xe-1
    unless stop/target is touched first (stop wins if both in one bar). Returns gross return (no cost) and exit bar."""
    k, ie, xe, sg, entry = map(np.asarray, (k, ie, xe, sg, entry))
    n = len(ie)
    stop = np.full(n, np.nan) if stop is None else np.asarray(stop, float)
    tgt = np.full(n, np.nan) if tgt is None else np.asarray(tgt, float)
    ln = np.maximum(xe - ie, 0); M = max(1, int(ln.max()) if n else 1)
    J = np.minimum(ie[:, None] + np.arange(M), T - 1); valid = np.arange(M) < ln[:, None]
    hh, ll = H[k[:, None], J], L[k[:, None], J]
    up = sg[:, None] > 0
    with np.errstate(invalid='ignore'):
        hs = valid & np.where(up, ll <= stop[:, None], hh >= stop[:, None])
        ht = valid & np.where(up, hh >= tgt[:, None], ll <= tgt[:, None])
    fs = np.where(hs.any(1), hs.argmax(1), M + 1); ft = np.where(ht.any(1), ht.argmax(1), M + 1)
    px = C[k, np.minimum(np.maximum(xe - 1, 0), T - 1)].astype(float)
    xb = xe - 1
    s_first = (fs <= ft) & (fs <= M); t_first = (ft < fs) & (ft <= M)
    px = np.where(s_first, stop, np.where(t_first, tgt, px))
    xb = np.where(s_first, ie + fs, np.where(t_first, ie + ft, xb))
    return sg * (px / entry - 1), xb


def baseline(tr, draws=5):
    """Random-entry baseline: same coin, direction, UTC time of day, holding time, stop/target distance; random day of the same year."""
    out = []
    for _ in range(draws):
        d = tr['ie'] // 96; y = YEAR[d]
        nd = np.empty_like(d)
        for yy in np.unique(y):
            m = y == yy; pool = np.where((YEAR == yy) & (np.arange(ND) >= D0) & (np.arange(ND) < ND - 3))[0]
            nd[m] = RNG.choice(pool, m.sum())
        sh = (nd - d) * 96
        ie, xe = tr['ie'] + sh, tr['xe'] + sh
        ok = (xe < T) & (ie >= 0)
        e = O[tr['k'], np.clip(ie, 0, T - 1)]
        st = None if tr.get('stopd') is None else e * (1 - tr['sg'] * tr['stopd'])
        tg = None if tr.get('tgtd') is None else e * (1 + tr['sg'] * tr['tgtd'])
        g, _ = run(tr['k'], np.clip(ie, 0, T - 1), np.clip(xe, 0, T - 1), tr['sg'], e, st, tg)
        r = g - COST; r[~ok | ~np.isfinite(e)] = np.nan
        out.append(r)
    return np.nanmean(np.vstack(out), 0)


def summ(name, tr, base=True, quiet=False):
    """tr: dict of arrays k, ie, xe, sg, ret (net). Prints one line; returns stats."""
    r = tr['ret'] * 100; y = YEAR[tr['ie'] // 96].astype(str)
    if len(r) < 20:
        print(f"  {name:44} n={len(r)} мало"); return None
    b = baseline(tr) * 100 if base else None
    ys = {yy: r[y == yy].mean() if (y == yy).sum() >= 5 else np.nan for yy in YRS}
    bs = {yy: np.nanmean(b[y == yy]) if (y == yy).sum() >= 5 else np.nan for yy in YRS} if base else {}
    allpos = all(np.isfinite(v) and v > 0 for v in ys.values())
    s = dict(name=name, n=len(r), tpd=len(r) / NDAYS, avg=r.mean(), wr=(r > 0).mean(), years=ys, base=np.nanmean(b) if base else np.nan,
             byears=bs, allpos=allpos)
    if not quiet:
        print(f"  {name:44} n={len(r):6d} {s['tpd']:5.2f}/д avg={r.mean():+.3f}% WR={s['wr'] * 100:3.0f}% | "
              + ' '.join(f"{k[2:]}:{v:+.2f}" for k, v in ys.items())
              + (f" || база {s['base']:+.3f}% " + ' '.join(f"{v:+.2f}" for v in bs.values()) if base else '')
              + ('  <== все годы +' if allpos else ''), flush=True)
    return s


def mk(k, ie, xe, sg, entry, stop=None, tgt=None, stopd=None, tgtd=None):
    g, xb = run(k, ie, xe, sg, entry, stop, tgt)
    ok = np.isfinite(g)
    tr = dict(k=np.asarray(k)[ok], ie=np.asarray(ie)[ok], xe=np.asarray(xe)[ok], sg=np.asarray(sg)[ok], ret=g[ok] - COST)
    if stopd is not None: tr['stopd'] = np.asarray(stopd)[ok]
    if tgtd is not None: tr['tgtd'] = np.asarray(tgtd)[ok]
    return tr


def sub(tr, m):
    return {a: v[m] for a, v in tr.items()}


DD = np.arange(D0, ND - 2)                            # usable days


# ---------------------------------------------------------------- H1 opening-range breakout
def orb(open_b, end_b, w, hold, shift=0):
    """open_b/end_b: per-day bar arrays; w: range bars; hold: 'end' or bars from open."""
    out = collections.defaultdict(list)
    o = open_b[DD] + shift * 4
    xe = (end_b[DD] + shift * 4) if hold == 'end' else o + hold
    M = int((xe - o - w).max())
    for k in range(NS):
        R = o[:, None] + np.arange(w)
        hi, lo = H[k, R].max(1), L[k, R].min(1)
        J = o[:, None] + w + np.arange(M); valid = np.arange(M) < (xe - o - w)[:, None]; J = np.minimum(J, T - 1)
        with np.errstate(invalid='ignore'):
            ux = valid & (H[k, J] > hi[:, None]); dx = valid & (L[k, J] < lo[:, None])
        anyx = ux | dx; has = anyx.any(1) & np.isfinite(hi) & np.isfinite(lo) & (hi > lo)
        f = anyx.argmax(1); je = o + w + f
        oo, cc = O[k, np.minimum(je, T - 1)], C[k, np.minimum(je, T - 1)]
        u, dn = ux[np.arange(len(o)), f], dx[np.arange(len(o)), f]
        for i in np.where(has)[0]:
            j = je[i]
            if u[i] and dn[i]:                    # bar crossed both sides
                if oo[i] > hi[i]: sg, e, stopped = 1, oo[i], True
                elif oo[i] < lo[i]: sg, e, stopped = -1, oo[i], True
                else: sg = -1 if cc[i] > oo[i] else 1; e = lo[i] if sg < 0 else hi[i]; stopped = True
            elif u[i]: sg, e, stopped = 1, max(hi[i], oo[i]), False
            else: sg, e, stopped = -1, min(lo[i], oo[i]), False
            st = lo[i] if sg > 0 else hi[i]
            out['k'].append(k); out['je'].append(j); out['xe'].append(xe[i]); out['sg'].append(sg); out['e'].append(e)
            out['st'].append(st); out['stopped'].append(stopped)
    a = {kk: np.array(v) for kk, v in out.items()}
    g, _ = run(a['k'], a['je'] + 1, np.maximum(a['xe'], a['je'] + 1), a['sg'], a['e'], a['st'])
    g = np.where(a['stopped'], a['sg'] * (a['st'] / a['e'] - 1), g)
    ok = np.isfinite(g)
    return dict(k=a['k'][ok], ie=a['je'][ok], xe=np.maximum(a['xe'], a['je'] + 1)[ok], sg=a['sg'][ok], ret=g[ok] - COST,
                stopd=np.abs(a['st'] / a['e'] - 1)[ok])


def h1():
    print("\n==== H1 пробой диапазона открытия (ORB) ====")
    res = []
    for sname, ob, eb in (('Лондон', LDN_O, LDN_C), ('Нью-Йорк', NY_O, NY_C)):
        for w in (1, 2, 4):
            for hold in (16, 'end'):
                tr = orb(ob, eb, w, hold)
                hn = '4ч' if hold == 16 else 'до конца сессии'
                s = summ(f"{sname} {w * 15}м, {hn}, оба", tr); res.append(s)
                summ(f"{sname} {w * 15}м, {hn}, лонг", sub(tr, tr['sg'] > 0))
                summ(f"{sname} {w * 15}м, {hn}, шорт", sub(tr, tr['sg'] < 0))
    print("  -- сдвиг «открытия» на ±1 ч (NY 30м, 4ч) --")
    for sh in (-1, 1):
        summ(f"Нью-Йорк 30м 4ч, сдвиг {sh:+d}ч", orb(NY_O, NY_C, 2, 16, sh))
        summ(f"Лондон 30м 4ч, сдвиг {sh:+d}ч", orb(LDN_O, LDN_C, 2, 16, sh))
    return res


# ---------------------------------------------------------------- H2 session fade / follow
def fade(move_s, move_e, ent, xe_b, thr, follow=False):
    rows = []
    for k in range(NS):
        s, e = move_s[DD], move_e[DD]; ie = ent[DD]; xe = xe_b[DD]
        mv = C[k, e - 1] / O[k, s] - 1
        with np.errstate(invalid='ignore'):
            m = np.isfinite(mv) & (np.abs(mv) >= thr) & (xe < T) & np.isfinite(O[k, np.minimum(ie, T - 1)])
        sg = np.sign(mv[m]) * (1 if follow else -1)
        rows.append((np.full(m.sum(), k), ie[m], xe[m], sg))
    k, ie, xe, sg = (np.concatenate(z) for z in zip(*rows))
    keep = sg != 0
    k, ie, xe, sg = k[keep], ie[keep], xe[keep], sg[keep].astype(int)
    return mk(k, ie, xe, sg, O[k, ie])


def h2():
    print("\n==== H2 разворот на смене сессий (фейд) и продолжение ====")
    nxt = lambda a: np.r_[a[1:], a[-1] + 96]
    cases = [
        ('Азия→фейд на открытии Лондона', ASIA, LDN_O, LDN_O, [('1ч', LDN_O + 4), ('4ч', LDN_O + 16), ('до откр. NY', NY_O)]),
        ('Лондон→фейд на открытии NY', LDN_O, NY_O, NY_O, [('1ч', NY_O + 4), ('4ч', NY_O + 16), ('до закр. NY', NY_C)]),
        ('день→фейд на закрытии NY', ASIA, NY_C, NY_C, [('1ч', NY_C + 4), ('4ч', NY_C + 16), ('до 00 UTC', nxt(ASIA)), ('до откр. Лондона', nxt(LDN_O))]),
    ]
    res = []
    for nm, s, e, ent, holds in cases:
        for hn, xe in holds:
            for thr in (0.0, 0.01, 0.02, 0.03):
                for fol in (False, True):
                    tr = fade(s, e, ent, xe, thr, fol)
                    st = summ(f"{nm} [{'продолж.' if fol else 'фейд'}] {hn}, |ход|≥{thr * 100:.0f}%", tr); res.append(st)
    print("  -- лучший кандидат (фейд хода Лондона на открытии NY, 1ч): сдвиг точки входа ±1ч, пороги 2.5/4% --")
    for sh in (-4, -2, 0, 2, 4):
        for thr in (0.02, 0.025, 0.03, 0.04):
            summ(f"фейд Лондон→NY 1ч, вход {sh * 15:+d}мин, ≥{thr * 100:.1f}%", fade(LDN_O, NY_O + sh, NY_O + sh, NY_O + sh + 4, thr))
    tr = fade(LDN_O, NY_O, NY_O, NY_O + 4, 0.03)
    d = tr['ie'] // 96
    print(f"  ≥3%: разных дней {len(np.unique(d))} на {len(d)} сделок (в среднем {len(d) / len(np.unique(d)):.1f} монет в один день)")
    return res


# ---------------------------------------------------------------- H3 time-of-day / weekday drift
def fixed(start_bars, hold, sg, days=DD):
    k = np.repeat(np.arange(NS), len(days)); ie = np.tile(start_bars[days], NS); xe = ie + hold
    ok = xe < T
    k, ie, xe = k[ok], ie[ok], xe[ok]
    e = O[k, ie]; m = np.isfinite(e) & np.isfinite(C[k, xe - 1])
    k, ie, xe = k[m], ie[m], xe[m]
    return mk(k, ie, xe, np.full(len(k), sg), O[k, ie])


def h3():
    print("\n==== H3 дрейф по часу суток (UTC) и дню недели ====")
    # gross mean 1h return by UTC hour, per year, plus volatility ratio
    r15 = C[:, 1:] / C[:, :-1] - 1; r15 = np.c_[np.full(NS, np.nan), r15]
    bars = np.arange(T); hr = (bars % 96) // 4; yy = YEAR[np.minimum(bars // 96, ND - 1)]
    sel = bars >= START
    print("  час UTC | ср. доход часа (до комиссий), % по годам | волатильность 15м отн. средней")
    vol_all = np.nanmean(np.abs(r15[:, sel]))
    hourly = {}
    for h in range(24):
        m = sel & (hr == h)
        ys = [np.nanmean(r15[:, m & (yy == int(y))]) * 4 * 100 for y in YRS]
        v = np.nanmean(np.abs(r15[:, m])) / vol_all
        hourly[h] = ys
        print(f"   {h:02d}    " + ' '.join(f"{x:+.3f}" for x in ys) + f"  | x{v:.2f}")
    print("  -- фиксированное удержание с часа h (после комиссий 0.14%) --")
    res = []
    for hold in (4, 8, 16, 32):
        for sg in (1, -1):
            for h in range(24):
                tr = fixed(ASIA + 4 * h, hold, sg)
                s = summ(f"{'лонг' if sg > 0 else 'шорт'} {h:02d}:00 UTC, {hold // 4}ч", tr, base=False, quiet=True)
                res.append(s)
                if s['allpos']: summ(s['name'], tr, base=False)
    best = sorted(res, key=lambda s: -min(s['years'].values()))[:6]
    print("  лучшие по худшему году:")
    for s in best: print(f"   {s['name']:30} avg={s['avg']:+.3f}% худший год {min(s['years'].values()):+.3f}% " + ' '.join(f"{v:+.2f}" for v in s['years'].values()))
    print("  -- по местному времени Нью-Йорка (час открытия NY = 09:30), 1ч удержание, лонг --")
    for off in (-4, -2, -1, 0, 1, 2, 4, 6):
        summ(f"лонг NY-откр {off:+d}ч, 1ч", fixed(NY_O + 4 * off, 4, 1), base=False)
    print("  -- день недели: сутки 00→24 UTC --")
    for wd, nm in enumerate(('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')):
        days = DD[WDAY[DD] == wd]
        summ(f"лонг {nm} 00→24", fixed(ASIA, 96, 1, days), base=False)
        summ(f"шорт {nm} 00→24", fixed(ASIA, 96, -1, days), base=False)
    return res


# ---------------------------------------------------------------- H4 CME weekend gap (BTC/ETH)
def h4():
    print("\n==== H4 гэп CME выходных (пт 16:00 → вс 17:00 Чикаго) ====")
    fr = [d for d in DD if WDAY[d] == 4 and d + 2 < ND]
    res = {}
    for coins, cn in (((syms.index('BTCUSDT'), syms.index('ETHUSDT')), 'BTC+ETH'), (tuple(range(NS)), 'все 18')):
        rows = []
        for k in coins:
            for d in fr:
                fc, so = CME_C[d], CME_O[d + 2]
                pf, p0 = C[k, fc - 1], O[k, so]
                if not (np.isfinite(pf) and np.isfinite(p0)) or so + 200 >= T: continue
                rows.append((k, so, pf, p0))
        k, so, pf, p0 = (np.array(z) for z in zip(*rows))
        gap = p0 / pf - 1
        print(f"  {cn}: недель×монет {len(k)}, ср. |гэп| {np.abs(gap).mean() * 100:.2f}%")
        for thr in (0.0, 0.005, 0.01, 0.02):
            for W, wn in ((96, '24ч'), (192, '48ч')):
                m = (np.abs(gap) >= thr) & (gap != 0)
                if m.sum() < 20: continue
                kk, ie, g, P0, PF = k[m], so[m], gap[m], p0[m], pf[m]
                sg = -np.sign(g).astype(int)
                mirror = P0 * (1 + (P0 - PF) / P0)       # level at the same distance on the other side
                # touch probabilities (no stop)
                _, xb_f = run(kk, ie, ie + W, sg, P0, None, PF)
                fill = run(kk, ie, ie + W, sg, P0, None, PF)[0]
                pf_hit = np.isclose(fill, sg * (PF / P0 - 1))
                mh = run(kk, ie, ie + W, -sg, P0, None, mirror)[0]
                pm_hit = np.isclose(mh, -sg * (mirror / P0 - 1))
                d_ = np.abs(PF / P0 - 1)
                tr = mk(kk, ie, ie + W, sg, P0, None, PF, None, d_)
                tr_s = mk(kk, ie, ie + W, sg, P0, mirror, PF, d_, d_)
                print(f"   |гэп|≥{thr * 100:.1f}% окно {wn}: n={m.sum()} закрытие гэпа {pf_hit.mean() * 100:.0f}% vs уровень на том же расстоянии в другую сторону {pm_hit.mean() * 100:.0f}%")
                s1 = summ(f"   {cn} фейд гэпа, цель=пт, без стопа, {wn}, ≥{thr * 100:.1f}%", tr)
                s2 = summ(f"   {cn} фейд гэпа, цель=пт, стоп зеркально, {wn}, ≥{thr * 100:.1f}%", tr_s)
                res[(cn, thr, W)] = (pf_hit.mean(), pm_hit.mean(), s1, s2)
    return res


# ---------------------------------------------------------------- H5 autopilot rules by session + portfolio filters
NORMA = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3, e_gap=40)


def sess_of(i, sh_o=0, sh_c=0):
    """0 Asia (00 UTC → London open), 1 Europe (London open → NY open), 2 US (NY open → NY close), 3 late (NY close → 24 UTC)."""
    d = i // 96
    if i < NY_O[d] + 4 * sh_o and i >= LDN_O[d]: return 1
    if NY_O[d] + 4 * sh_o <= i < NY_C[d] + 4 * sh_c: return 2
    if i >= NY_C[d] + 4 * sh_c: return 3
    return 0


def yearly_pct(trades, start=10_000.0):
    eq = start; out = {}; by = collections.defaultdict(float)
    for t in trades: by[AP.DAY[t['x']][:4]] += t['pnl']
    for y in YRS: out[y] = by[y] / eq * 100; eq += by[y]
    return out


def h5():
    print("\n==== H5 правила автопилота B/E/F по сессии входа ====")
    B, E, F = AP.cand_B(), AP.cand_E(), AP.cand_F()
    names = ('Азия 00→Лондон', 'Европа Лондон→NY', 'США NY откр→закр', 'вечер NY закр→24')
    for rn, cs in (('B', B), ('E', E), ('F', F)):
        print(f"  -- правило {rn} --")
        for si in range(4):
            sel = [c for c in cs if sess_of(c['i']) == si]
            if len(sel) < 20: print(f"  {names[si]:44} n={len(sel)} мало"); continue
            tr = dict(k=np.array([c['k'] for c in sel]), ie=np.array([c['i'] for c in sel]), xe=np.array([c['x'] + 1 for c in sel]),
                      sg=np.ones(len(sel), int), ret=np.array([c['ret'] for c in sel]))
            summ(f"{rn} {names[si]}", tr, base=False)
    ALL = B + E + F
    eq, tr, cu = AP.simulate(ALL, **NORMA); base, _ = AP.report(eq, tr, cu); by = yearly_pct(tr)
    print(f"\n  портфель «норма»: ${base['final']:,} CAGR {base['cagr'] * 100:+.1f}% DD {base['maxdd'] * 100:.1f}% сделок {base['n']} | "
          + ' '.join(f"{y[2:]}:{v:+.1f}%" for y, v in by.items()))
    print("  исполненные сделки портфеля по сессии входа (средний % сделки по годам; сумма $):")
    for rn in ('B', 'E', 'F'):
        for si in range(4):
            sel = [t for t in tr if t['rule'] == rn and sess_of(t['i']) == si]
            if not sel: continue
            yy = collections.defaultdict(list)
            for t in sel: yy[AP.DAY[t['i']][:4]].append(t['ret'] * 100)
            print(f"   {rn} {names[si]:18} n={len(sel):4d} avg={np.mean([t['ret'] for t in sel]) * 100:+.2f}% ${sum(t['pnl'] for t in sel):>+8,.0f} | "
                  + ' '.join(f"{y[2:]}:{np.mean(yy[y]):+.2f}({len(yy[y])})" if yy[y] else f"{y[2:]}: —" for y in YRS))
    out = [('база', base, by)]
    filters = [('E без США', {'E'}, {2}), ('F без США', {'F'}, {2}), ('E,F без США', {'E', 'F'}, {2}), ('B,E,F без США', {'B', 'E', 'F'}, {2}),
               ('B без США', {'B'}, {2}), ('E,F без Европы', {'E', 'F'}, {1}), ('E,F только Азия', {'E', 'F'}, {1, 2, 3}),
               ('E,F без вечера', {'E', 'F'}, {3})]
    for fn, rules, bad in filters:
        for sh_o, sh_c in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
            cs = [c for c in ALL if not (c['rule'] in rules and sess_of(c['i'], sh_o, sh_c) in bad)]
            eq, tr, cu = AP.simulate(cs, **NORMA); r, _ = AP.report(eq, tr, cu); yp = yearly_pct(tr)
            better = sum(yp[y] > by[y] for y in YRS)
            print(f"  {fn:18} сдвиг откр{sh_o:+d}ч закр{sh_c:+d}ч: ${r['final']:>7,} CAGR {r['cagr'] * 100:+5.1f}% DD {r['maxdd'] * 100:5.1f}% сделок {r['n']:4d} | "
                  + ' '.join(f"{y[2:]}:{v:+.1f}%" for y, v in yp.items()) + f" | лучше базы в {better}/4 годах", flush=True)
            out.append((f"{fn} {sh_o:+d}/{sh_c:+d}", r, yp))
    # fixed-UTC variant from filters_session_depth.md
    cs = [c for c in ALL if not (c['rule'] in ('E', 'F') and 14 <= (c['i'] % 96) // 4 < 21)]
    eq, tr, cu = AP.simulate(cs, **NORMA); r, _ = AP.report(eq, tr, cu); yp = yearly_pct(tr)
    print(f"  E,F без 14–21 UTC (старый вариант): ${r['final']:,} CAGR {r['cagr'] * 100:+.1f}% DD {r['maxdd'] * 100:.1f}% | " + ' '.join(f"{y[2:]}:{v:+.1f}%" for y, v in yp.items()))
    return out


if __name__ == '__main__':
    want = [a for a in sys.argv[1:] if a.startswith('h')] or ['h1', 'h2', 'h3', 'h4', 'h5']
    print(f"период {DAYS[D0]} → {DAYS[ND - 1]}, дней {NDAYS}, монет {NS}, комиссия+проскальзывание {COST * 100:.2f}% за сделку")
    for h in want: globals()[h]()
