"""Prop exam on history — would our autopilot pass a prop firm's evaluation, at which size, and how fast?

Uses the autopilot's whole-portfolio backtest (scripts/autopilot_v2.py: rules B, E, F, D on 18 coins, 2023-01 → 2026-09,
Binance 15m data with costs) and replays it under each firm's rules from config/prop_firms.json, with the account marked
to market every 15 minutes (open positions at the bar close, the worst point at the bar low) — so daily-loss and drawdown
limits see floating losses, not only closed trades.

Prop accounts are fixed-size: every trade keeps the share of the account it had in the backtest (no compounding inside a
phase), times a size multiplier k (0.25 … 1.5). Starts: every week of history (at a moment with no open position).
Phase passed → the next phase starts at 100 with the same strategy. Funded stage: 180 days under the funded limits.

Usage: python scripts/prop_backtest.py [--firms config/prop_firms.json] [--out web/prop.json]
"""
import sys, json, time, collections, statistics as st
import numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as AP

DAYMS = 86_400_000
BAR = 15 * 60_000
KS = (0.5, 0.75, 1.0)
FUNDED_DAYS = 180
MAX_PHASE_DAYS = 365
VARIANTS = {   # live profiles (cnq/agents/paper_desk.PROFILES) without the FX-gap rule A
    "cons": dict(name_ru="Консервативная", rules=('B', 'E', 'F', 'D'), risk=0.005, max_open=2, e_share=0.10, f_share=0.15, f_open=2, d_share=0.10, d_open=2),
    "bal": dict(name_ru="Сбалансированная", rules=('B', 'E', 'F', 'D'), risk=0.01, max_open=4, e_share=0.20, f_share=0.25, f_open=3, d_share=0.15, d_open=3),
    # prop mode: the same rules, fewer positions at once (crash-buys are correlated: they lose on the same day)
    "prop": dict(name_ru="Проп-режим (меньше позиций сразу)", rules=('B', 'E', 'F', 'D'), risk=0.0075, max_open=2, e_share=0.12, f_share=0.15, f_open=1, d_share=0.10, d_open=1),
}
GUARDS = (None, 0.6)          # daily stop of our own: close everything and pause till tomorrow at 60 % of the firm's daily limit


def trades_of(params):
    p = {k: v for k, v in params.items() if k != 'name_ru'}
    eq, trades, curve = AP.simulate(ALL, **p)
    trades = sorted(trades, key=lambda o: o['x'])
    # equity at each entry = start + P&L of trades closed before the entry bar (simulate books closes with x < i first)
    xs = np.array([o['x'] for o in trades]); cum = np.cumsum([o['pnl'] for o in trades])
    out = []
    for o in trades:
        j = np.searchsorted(xs, o['i'], side='left')       # trades with x < i
        e0 = 10_000.0 + (cum[j - 1] if j > 0 else 0.0)
        out.append(dict(o, frac=o['usd'] / e0))
    return out, AP.report(eq, trades, curve)[0]


def paths(trades):
    """Global per-bar arrays in % of a fixed account: realized (closed by the end of the bar), open at the close, open at the worst point."""
    T0 = AP.START
    n = AP.T - T0
    realized = np.zeros(n); oc = np.zeros(n); ow = np.zeros(n); nopen = np.zeros(n, dtype=np.int32); opened = np.zeros(n, dtype=bool)
    O, L, C = AP.O, AP.L, AP.C
    for o in trades:
        k, i, x, f = o['k'], o['i'], o['x'], o['frac'] * 100
        if i < T0 or x >= AP.T:
            continue
        e = O[k, i]
        if not np.isfinite(e) or e <= 0:
            continue
        a, b = i - T0, x - T0
        realized[b] += f * o['ret']
        if b > a:
            seg = slice(i, x)
            cc = np.nan_to_num(C[k, seg] / e - 1, nan=0.0); ll = np.nan_to_num(L[k, seg] / e - 1, nan=0.0)
            oc[a:b] += f * cc; ow[a:b] += f * np.minimum(ll, cc)
        # exit bar: a losing exit is the stop (or a time exit) — the backtest fills it at the stop, so the worst point is the exit
        # itself (a flash crash can fill a stop far lower — not modelled); a winning exit bar may first dip below the result
        lx = L[k, x] / e - 1 if np.isfinite(L[k, x]) else o['ret']
        if o['ret'] > 0:
            ow[b] += f * (min(lx, o['ret']) - o['ret'])
        nopen[a:b + 1] += 1
        opened[a] = True
    realized = np.cumsum(realized)
    return dict(realized=realized, oc=oc, ow=ow, nopen=nopen, opened=opened, t=AP.times[T0:])


def phase(P, p0, base, k, rule, limit_days=MAX_PHASE_DAYS):
    """Replay one phase from bar p0 (account = 100). → (result, end bar, why)."""
    t = P['t']
    end = min(len(t), p0 + int((rule.get('time_limit_days') or limit_days) * DAYMS / BAR))
    if end - p0 < 96:
        return 'end', end, 'история закончилась'
    sl = slice(p0, end)
    ec = 100 + k * (P['realized'][sl] - base + P['oc'][sl])
    # worst point inside a bar: realized up to the previous bar + open positions at their worst (exit-bar dips included in ow)
    prev_real = np.r_[P['realized'][p0 - 1] if p0 > 0 else 0.0, P['realized'][sl][:-1]]
    ew = 100 + k * (prev_real - base + P['ow'][sl] + (P['realized'][sl] - prev_real))
    ew = np.minimum(ew, ec)
    days = t[sl] // DAYMS
    first = np.r_[True, days[1:] != days[:-1]]
    prev_close = np.r_[100.0, ec[:-1]]
    idx = np.where(first, np.arange(len(ec)), 0); idx = np.maximum.accumulate(idx)
    ds = prev_close[idx]
    daily = rule.get('daily_loss_pct'); mdd = rule.get('max_dd_pct')
    b_daily = ew <= ds * (1 - daily / 100) + 1e-9 if daily else np.zeros(len(ec), dtype=bool)
    if mdd:
        if (rule.get('max_dd_type') or 'static') == 'trailing':
            peak = np.maximum.accumulate(np.r_[100.0, ec[:-1]])
            floor = np.minimum(peak - mdd, 100.0)
        else:
            floor = np.full(len(ec), 100.0 - mdd)
        b_dd = ew <= floor + 1e-9
    else:
        b_dd = np.zeros(len(ec), dtype=bool)
    tgt = rule.get('target_pct')
    if tgt is not None:
        od = P['opened'][sl]
        open_days = np.unique(days[od])                                   # trading days = days with at least one opened trade
        tdays = np.searchsorted(open_days, days, side='right')
        ok = (ec >= 100 + tgt) & (tdays >= (rule.get('min_days') or 0))
    else:
        ok = np.zeros(len(ec), dtype=bool)
    i_daily = np.argmax(b_daily) if b_daily.any() else None
    i_dd = np.argmax(b_dd) if b_dd.any() else None
    i_ok = np.argmax(ok) if ok.any() else None
    cands = [(i, w) for i, w in ((i_daily, 'дневной лимит убытка'), (i_dd, 'максимальная просадка')) if i is not None]
    i_bad, w_bad = min(cands) if cands else (None, None)
    if i_ok is not None and (i_bad is None or i_ok < i_bad):
        return 'pass', p0 + i_ok, 'цель достигнута'
    if i_bad is not None:
        return 'fail', p0 + i_bad, w_bad
    if end < len(t):
        return 'timeout', end, 'время вышло' if rule.get('time_limit_days') else 'за год цель не достигнута'
    return 'end', end, 'история закончилась'


def trade_arrays(trades):
    """Per trade: bars i..x, close-based and worst-point P&L (fractions of the fixed account, ×100 = %), final result."""
    O, L, C = AP.O, AP.L, AP.C
    out = []
    for o in trades:
        k, i, x = o['k'], o['i'], o['x']
        e = O[k, i]
        if i < AP.START or x >= AP.T or not np.isfinite(e) or e <= 0:
            continue
        f = o['frac'] * 100
        cc = np.nan_to_num(C[k, i:x] / e - 1, nan=0.0)
        ll = np.nan_to_num(L[k, i:x] / e - 1, nan=0.0)
        lx = L[k, x] / e - 1 if np.isfinite(L[k, x]) else o['ret']
        out.append(dict(i=i - AP.START, x=x - AP.START, f=f, cc=f * cc, ww=f * np.minimum(ll, cc), ret=f * o['ret'],
                        xw=f * (min(lx, o['ret']) if o['ret'] > 0 else o['ret'])))
    out.sort(key=lambda z: z['i'])
    return out


def sim_guard(TA, t, p0, k, rule, guard, limit_days=MAX_PHASE_DAYS):
    """Bar-by-bar (vectorised per day) replay from bar p0 with our own daily stop `guard` (fraction of the firm's daily limit):
    when the account falls guard × limit below the day's start, every position is closed at that bar's worst point and no
    trade opens until the next day. → (result, end bar, why, final equity)."""
    daily = rule.get('daily_loss_pct'); mdd = rule.get('max_dd_pct'); tgt = rule.get('target_pct')
    trailing = (rule.get('max_dd_type') or 'static') == 'trailing'
    gpct = guard * daily if (guard and daily) else None
    end = min(len(t), p0 + int((rule.get('time_limit_days') or limit_days) * DAYMS / BAR))
    if end - p0 < 96:
        return 'end', end, 'история закончилась', None
    lo = np.searchsorted([z['i'] for z in TA], p0)
    pending = TA[lo:]
    bal, peak, open_, nd, tdays = 100.0, 100.0, [], 0, 0
    j0 = p0
    while j0 < end:
        day = t[j0] // DAYMS
        j1 = min(end, j0 + int(np.searchsorted(t[j0:end] // DAYMS, day, side='right')))
        n = j1 - j0
        # entries of the day
        new = []
        while nd < len(pending) and pending[nd]['i'] < j1:
            new.append(pending[nd]); nd += 1
        act = open_ + new
        if new:
            tdays += 1
        real = np.zeros(n); fc = np.zeros(n); fw = np.zeros(n)
        for z in act:
            a = max(z['i'], j0); b = min(z['x'], j1 - 1)
            if b < a and z['x'] >= j0:
                continue
            seg_a, seg_b = a - z['i'], min(z['x'], j1) - z['i']
            if seg_b > seg_a:
                fc[a - j0:a - j0 + seg_b - seg_a] += k * z['cc'][seg_a:seg_b]
                fw[a - j0:a - j0 + seg_b - seg_a] += k * z['ww'][seg_a:seg_b]
            if j0 <= z['x'] < j1:
                real[z['x'] - j0] += k * z['ret']
                fw[z['x'] - j0] += k * (z['xw'] - z['ret'])
        rc = np.cumsum(real)
        ec = bal + rc + fc
        ew = np.minimum(bal + rc + fw, ec)
        start_eq = bal + sum(k * z['cc'][j0 - z['i'] - 1] for z in open_ if j0 - z['i'] - 1 >= 0 and j0 - z['i'] - 1 < len(z['cc']))
        cut = None
        if gpct is not None:
            hit = np.flatnonzero(ew <= start_eq * (1 - gpct / 100) + 1e-9)
            if len(hit):
                cut = int(hit[0])
                ec = ec.copy(); ew = ew.copy()
                ec[cut:] = ew[cut]; ew[cut:] = ew[cut]
        # firm checks inside the day
        bad_d = np.flatnonzero(ew <= start_eq * (1 - daily / 100) + 1e-9) if daily else np.array([], dtype=int)
        if mdd:
            pk = np.maximum.accumulate(np.r_[peak, ec[:-1]])
            floor = np.minimum(pk - mdd, 100.0) if trailing else np.full(n, 100.0 - mdd)
            bad_m = np.flatnonzero(ew <= floor + 1e-9)
        else:
            bad_m = np.array([], dtype=int)
        ok = np.flatnonzero(ec >= 100 + tgt) if (tgt is not None and tdays >= (rule.get('min_days') or 0)) else np.array([], dtype=int)
        cands = [(int(v[0]), w) for v, w in ((bad_d, 'дневной лимит убытка'), (bad_m, 'максимальная просадка')) if len(v)]
        ib, wb = min(cands) if cands else (None, None)
        if len(ok) and (ib is None or ok[0] < ib):
            return 'pass', j0 + int(ok[0]), 'цель достигнута', float(ec[ok[0]])
        if ib is not None:
            return 'fail', j0 + ib, wb, float(ew[ib])
        peak = max(peak, float(ec.max()))
        if cut is not None:
            bal = float(ew[cut]); open_ = []
            # trades that would have opened later today are skipped; trades already open are closed
            while nd < len(pending) and pending[nd]['i'] < j1:
                nd += 1
        else:
            bal = bal + float(rc[-1])
            open_ = [z for z in act if z['x'] >= j1]
        j0 = j1
    if end < len(t):
        return 'timeout', end, 'время вышло' if rule.get('time_limit_days') else 'за год цель не достигнута', bal
    return 'end', end, 'история закончилась', bal


def exam_g(TA, P, s, k, phases, funded, guard):
    t = P['t']; p = s
    for n, ph in enumerate(phases):
        r, e, why, _ = sim_guard(TA, t, p, k, ph, guard)
        if r != 'pass':
            return dict(result=r, phase=n + 1, why=why, days=(t[min(e, len(t) - 1)] - t[s]) / DAYMS if r != 'end' else None)
        t_pass = t[e]
        p = next_flat(P, e + 1)
        if p >= len(t) - 96:
            return dict(result='end', phase=n + 1, why='история закончилась', days=None)
    out = dict(result='pass', phase=len(phases), why='экзамен сдан', days=(t_pass - t[s]) / DAYMS)
    if funded and p < len(t) - 96:
        r, e, why, eq = sim_guard(TA, t, p, k, dict(funded, target_pct=None), guard, limit_days=FUNDED_DAYS)
        if r == 'fail':
            out['funded'] = dict(survived=False, days=(t[e] - t[p]) / DAYMS, why=why)
        elif r == 'timeout':
            out['funded'] = dict(survived=True, ret_pct=(eq or 100.0) - 100.0)
    return out


def next_flat(P, j):
    """first bar ≥ j with no open position (a new phase/account starts flat: positions are closed when a target is hit)."""
    z = np.flatnonzero(P['nopen'][j:] == 0)
    return j + int(z[0]) if len(z) else len(P['t'])


def exam(P, s, k, phases, funded):
    p, t = s, P['t']
    for n, ph in enumerate(phases):
        base = P['realized'][p - 1] if p > 0 else 0.0
        r, e, why = phase(P, p, base, k, ph)
        if r != 'pass':
            return dict(result=r, phase=n + 1, why=why, days=(t[min(e, len(t) - 1)] - t[s]) / DAYMS)
        t_pass = t[e]
        p = next_flat(P, e + 1)
        if p >= len(t) - 96:
            return dict(result='end', phase=n + 1, why='история закончилась', days=None)
    out = dict(result='pass', phase=len(phases), why='экзамен сдан', days=(t_pass - t[s]) / DAYMS)
    if funded and p < len(t) - 96:
        base = P['realized'][p - 1]
        fr = dict(funded, target_pct=None)
        r, e, why = phase(P, p, base, k, fr, limit_days=FUNDED_DAYS)
        if r == 'fail':
            out['funded'] = dict(survived=False, days=(t[e] - t[p]) / DAYMS, why=why)
        elif r == 'timeout':
            gain = k * (P['realized'][e - 1] - base)
            out['funded'] = dict(survived=True, ret_pct=gain)
    return out


def starts(P, step_days=7):
    t = P['t']; out = []; nxt = t[0]
    for j in range(len(t)):
        if t[j] >= nxt and P['nopen'][j] == 0:
            out.append(j); nxt = t[j] + step_days * DAYMS
    return out


def firm_rules(f):
    ch = f.get('challenge') or {}
    tg = ch.get('target_pct')
    tg = tg if isinstance(tg, list) else ([tg] if tg is not None else [])
    def num(v):
        if isinstance(v, (int, float)):
            return v
        import re
        m = re.search(r"\d+", str(v or ""))
        return int(m.group()) if m else 0
    base = dict(daily_loss_pct=ch.get('daily_loss_pct'), max_dd_pct=ch.get('max_dd_pct'),
                max_dd_type='trailing' if str(ch.get('max_dd_type') or '').startswith('trail') else 'static',
                min_days=num(ch.get('min_days')), time_limit_days=ch.get('time_limit_days') if isinstance(ch.get('time_limit_days'), (int, float)) else None)
    phases = [dict(base, target_pct=x) for x in tg if x is not None]
    fu = f.get('funded') or {}
    funded = dict(daily_loss_pct=fu.get('daily_loss_pct', base['daily_loss_pct']), max_dd_pct=fu.get('max_dd_pct', base['max_dd_pct']),
                  max_dd_type=fu.get('max_dd_type', base['max_dd_type']), min_days=0)
    return phases, funded


def stats(P, S, k, phases, funded, TA=None, guard=None):
    rs = [exam_g(TA, P, s, k, phases, funded, guard) if (TA is not None and guard) else exam(P, s, k, phases, funded) for s in S]
    rs = [r for r in rs if r['result'] != 'end']
    if not rs:
        return None
    ps = [r for r in rs if r['result'] == 'pass']
    fails = collections.Counter(f"{r['why']} (этап {r['phase']})" for r in rs if r['result'] != 'pass')
    fu = [r['funded'] for r in ps if r.get('funded')]
    sv = [f for f in fu if f['survived']]
    d = dict(k=k, starts=len(rs), pass_rate=round(len(ps) / len(rs), 3),
             pass_90d=round(sum(1 for r in ps if r['days'] <= 90) / len(rs), 3),
             days_median=round(st.median([r['days'] for r in ps]), 1) if ps else None,
             fails=dict(fails.most_common()))
    if fu:
        d.update(funded_n=len(fu), funded_survive_6m=round(len(sv) / len(fu), 3),
                 funded_month_pct=round(st.mean([f['ret_pct'] for f in sv]) / 6, 2) if sv else None)
    return d


def verdict(b, var_name):
    if not b:
        return None
    var_name = var_name + (f" + наш дневной стоп на {b['guard'] * 100:.0f}% лимита фирмы" if b.get('guard') else "")
    lvl = {0.25: "четверть обычного размера", 0.5: "половина обычного размера", 0.75: "три четверти обычного размера",
           1.0: "обычный размер", 1.5: "в 1.5 раза больше обычного"}.get(b['k'], f"×{b['k']}")
    s = f"«{var_name}», {lvl} позиций: экзамен сдан в {b['pass_rate'] * 100:.0f}% стартов на истории"
    s += f" (за 90 дней — в {b['pass_90d'] * 100:.0f}%)" if b.get('pass_90d') is not None else ""
    s += f", обычно за {b['days_median']:.0f} дн." if b.get('days_median') is not None else "."
    if b.get('funded_survive_6m') is not None:
        s += f" После сдачи финансируемый счёт прожил 6 месяцев в {b['funded_survive_6m'] * 100:.0f}% случаев"
        s += f", в среднем {b['funded_month_pct']:+.1f}% в месяц." if b.get('funded_month_pct') is not None else "."
    return s


if __name__ == '__main__':
    args = sys.argv
    firms_path = args[args.index('--firms') + 1] if '--firms' in args else 'config/prop_firms.json'
    out_path = args[args.index('--out') + 1] if '--out' in args else 'web/prop.json'
    t0 = time.time()
    ALL = AP.cand_B() + AP.cand_E() + AP.cand_F() + AP.cand_D()
    print(f"candidates {len(ALL)} in {time.time() - t0:.0f}s", flush=True)
    cfg = json.load(open(firms_path, encoding='utf-8'))
    V = {}
    for key, prm in VARIANTS.items():
        tr, rep = trades_of(prm)
        P = paths(tr)
        V[key] = dict(P=P, S=starts(P, 14), rep=rep, name=prm['name_ru'], TA=trade_arrays(tr))
        print(key, rep, 'starts', len(V[key]['S']), flush=True)
    res, cache = [], {}
    for f in cfg.get('firms') or []:
        phases, funded = firm_rules(f)
        if not phases or not any(p.get('max_dd_pct') for p in phases):
            res.append(dict(id=f.get('id'), skipped_ru="правила экзамена не проверены — не считаем")); continue
        ck = json.dumps([phases, funded], sort_keys=True)
        if ck in cache:
            rows = cache[ck]
        else:
            rows = []
            for key, v in V.items():
                for g in GUARDS:
                    for k in KS:
                        s_ = stats(v['P'], v['S'], k, phases, funded, TA=v['TA'], guard=g)
                        if s_:
                            rows.append(dict(s_, variant=key, variant_ru=v['name'], guard=g))
            cache[ck] = rows
        best = max(rows, key=lambda r: (r['pass_90d'] + 0.5 * r['pass_rate'] + 0.3 * (r.get('funded_survive_6m') or 0), -(r['days_median'] or 1e9))) if rows else None
        res.append(dict(id=f.get('id'), phases=phases, funded=funded, rows=rows, best=best, verdict_ru=verdict(best, best['variant_ru']) if best else None))
        print(f.get('id'), best, flush=True)
    by = {r['id']: r for r in res}
    out = dict(asof=int(time.time() * 1000), available=True, firms_asof=cfg.get('asof'),
               firms=[dict(f, exam=by.get(f.get('id'))) for f in cfg.get('firms') or []], avoid=cfg.get('avoid') or [],
               recommendation_ru=cfg.get('recommendation_ru'), notes_ru=cfg.get('notes_ru') or [],
               strategy=dict(period=[AP.DAY[AP.START], AP.DAY[-1]], variants={k: dict(name_ru=v['name'], report=v['rep']) for k, v in V.items()}),
               method_ru=["Берём наш автопилот на истории 2023–2026 (18 монет, комиссии учтены) и прогоняем по правилам каждой фирмы. Счёт пересчитываем каждые 15 минут "
                          "вместе с открытыми позициями (по худшей цене внутри свечи) — дневной лимит и просадка видят и незакрытый минус.",
                          "Старт — каждую неделю истории; размер позиций меняем множителем: 0.25 — четверть обычного, 1.0 — обычный. Счёт фирмы фиксированный, без сложного процента.",
                          "После сдачи — 6 месяцев на финансируемом счёте: как часто он выживает и сколько в среднем приносит в месяц.",
                          "Ограничения: история на данных Binance, проп-фирма торгует на своей бирже/платформе с другими комиссиями и проскальзыванием; правило D на истории короче."])
    json.dump(out, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    print('saved', out_path, f"{time.time() - t0:.0f}s")
