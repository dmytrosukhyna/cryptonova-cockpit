"""Autopilot v2 — whole-portfolio backtest on 18 coins with real derivatives data (2023-01 → 2026-09, Binance futures,
non-imputed OI/liquidations). Adds rule F "OI flush": open interest −8 % over 4h together with price −5 % over 4h →
long next bar (optionally delayed); chosen thresholds OI −12 % / price −5 % (robust region −10…−15 %, see docs/research/oi_flush.md), exit 24 h, catastrophe stop −12 %. Rules B and E as in autopilot_backtest.py.
Usage: python scripts/autopilot_v2.py [--save '{json params}']"""
import sys, json, collections, datetime as dt, numpy as np
sys.path.insert(0, 'scripts')
from ap_data import load
syms, times, A = load()
O, H, L, C, V, OI, IMP = A['open'], A['high'], A['low'], A['close'], A['volume_quote'], A['open_interest_usd'], A['is_imputed_metrics']
NS, T = C.shape
FEE, SLIP = 0.0005, 0.0002; COST = 2 * (FEE + SLIP)
START = int(np.searchsorted(times, 1672531200000))            # 2023-01-01
DAY = np.array([dt.datetime.utcfromtimestamp(t / 1000).strftime('%Y-%m-%d') for t in times])
r24 = np.full_like(C, np.nan); r24[:, 96:] = C[:, 96:] / C[:, :-96] - 1
avail = np.isfinite(r24).sum(0); BR = np.where(avail >= 8, (r24 < -0.03).sum(0) / np.maximum(avail, 1), 0)
OI16 = np.full_like(OI, np.nan); OI16[:, 16:] = OI[:, 16:] / OI[:, :-16] - 1
R4h = np.full_like(C, np.nan); R4h[:, 16:] = C[:, 16:] / C[:, :-16] - 1


def walk(k, i0, sg, stop, tgt, max_bars):
    e = O[k, i0]; end = min(T - 1, i0 + max_bars)
    for j in range(i0, end + 1):
        if not np.isfinite(L[k, j]): continue
        if stop is not None and (L[k, j] <= stop if sg > 0 else H[k, j] >= stop): return j, sg * (stop / e - 1)
        if tgt is not None and (H[k, j] >= tgt if sg > 0 else L[k, j] <= tgt): return j, sg * (tgt / e - 1)
    while end > i0 and not np.isfinite(C[k, end]): end -= 1
    return end, sg * (C[k, end] / e - 1)


def atr15(k, n=14):
    tr = np.r_[H[k, 0] - L[k, 0], np.maximum(H[k, 1:] - L[k, 1:], np.maximum(abs(H[k, 1:] - C[k, :-1]), abs(L[k, 1:] - C[k, :-1])))]
    a = np.full(T, np.nan); a[n - 1:] = np.convolve(np.nan_to_num(tr), np.ones(n) / n, 'valid'); return a


def cand_B():
    out = []
    for k in range(NS):
        r = np.r_[0, C[k, 1:] / C[k, :-1] - 1]; r = np.nan_to_num(r)
        sig = np.sqrt(np.convolve(r * r, np.ones(2880), 'full')[:T] / 2880); a = atr15(k); rng = H[k] - L[k]
        with np.errstate(invalid='ignore', divide='ignore'):
            wick = (np.minimum(O[k], C[k]) - L[k]) / rng
            idx = np.where((r <= -3.5 * sig) & (wick >= 0.5) & (np.arange(T) >= START))[0]
        for i in idx:
            if i + 34 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; stop = L[k, i] - 0.5 * a[i]
            if not e - stop > 0: continue
            x, g = walk(k, i + 1, 1, stop, e + (e - stop), 32)
            out.append(dict(rule='B', k=k, i=i + 1, x=x, ret=g - COST, sf=(e - stop) / e + COST))
    return out


def cand_E():
    out = []
    for k in range(NS):
        cs = np.cumsum(np.nan_to_num(V[k])); vav = np.full(T, np.nan); vav[97:] = (cs[96:-1] - cs[:-97]) / 96
        with np.errstate(invalid='ignore'):
            idx = np.where((C[k] / O[k] - 1 <= -0.015) & (V[k] >= 2 * vav) & (BR >= 0.7) & (np.arange(T) >= START))[0]
        for i in idx:
            if i + 98 >= T or not np.isfinite(O[k, i + 1]): continue
            e = O[k, i + 1]; x, g = walk(k, i + 1, 1, e * 0.88, None, 96)
            out.append(dict(rule='E', k=k, i=i + 1, x=x, ret=g - COST, sf=0.12))
    return out


def cand_F(oi=-0.12, px=-0.05, delay=1, hold=96, gap=16):
    out = []
    with np.errstate(invalid='ignore'):
        M = (OI16 <= oi) & (R4h <= px) & (IMP == 0)
    for k in range(NS):
        last = -10 ** 9
        for i in np.where(M[k])[0]:
            if i < START or i - last < gap or i + delay + hold + 2 >= T: continue
            last = i; i0 = i + delay
            if not np.isfinite(O[k, i0]): continue
            e = O[k, i0]; x, g = walk(k, i0, 1, e * 0.88, None, hold)
            out.append(dict(rule='F', k=k, i=i0, x=x, ret=g - COST, sf=0.12))
    return out


def simulate(cands, rules=('B', 'E', 'F'), risk=0.01, max_open=4, e_share=0.2, f_share=0.15, f_open=3, lev_cap=3.0, start=10_000.0):
    ev = sorted([c for c in cands if c['rule'] in rules], key=lambda c: c['i'])
    byi = collections.defaultdict(list)
    for c in ev: byi[c['i']].append(c)
    eq = start; open_ = []; last_e = -10 ** 9; trades = []; curve = [(START, eq)]
    for i in sorted(byi):
        still = []
        for o in sorted(open_, key=lambda o: o['x']):
            if o['x'] < i: eq += o['pnl']; trades.append(o); curve.append((o['x'], eq))
            else: still.append(o)
        open_ = still; held = {o['k'] for o in open_}
        gross = sum(o['usd'] for o in open_)
        es = [c for c in byi[i] if c['rule'] == 'E' and c['k'] not in held]
        if es and i - last_e >= 80:
            n = eq * e_share / len(es)
            for c in es:
                if gross + n > eq * lev_cap: break
                open_.append(dict(c, pnl=n * c['ret'], usd=n)); held.add(c['k']); gross += n
            last_e = i
        for c in byi[i]:
            if c['rule'] == 'E' or c['k'] in held: continue
            if c['rule'] == 'F':
                if sum(1 for o in open_ if o['rule'] == 'F') >= f_open: continue
                n = eq * f_share
            else:
                if sum(1 for o in open_ if o['rule'] == 'B') >= max_open: continue
                n = min(eq * risk / c['sf'], eq * 3)
            if gross + n > eq * lev_cap: continue
            open_.append(dict(c, pnl=n * c['ret'], usd=n)); held.add(c['k']); gross += n
    for o in sorted(open_, key=lambda o: o['x']): eq += o['pnl']; trades.append(o); curve.append((o['x'], eq))
    return eq, trades, curve


def report(eq, trades, curve, start=10_000.0):
    curve.sort(); v = np.array([x for _, x in curve]); dd = ((v - np.maximum.accumulate(v)) / np.maximum.accumulate(v)).min()
    yrs = (times[-1] - times[START]) / (365.25 * 86400_000)
    m = collections.defaultdict(float); y = collections.defaultdict(float); ru = collections.defaultdict(float)
    for t in trades: m[DAY[t['x']][:7]] += t['pnl']; y[DAY[t['x']][:4]] += t['pnl']; ru[t['rule']] += t['pnl']
    mv = np.array(list(m.values())) if m else np.zeros(1)
    return dict(final=round(eq), cagr=round((eq / start) ** (1 / yrs) - 1, 4), maxdd=round(dd, 4), n=len(trades), win_m=round((mv > 0).mean(), 3),
                months=len(mv), years={k: round(v) for k, v in sorted(y.items())}, rules={k: round(v) for k, v in ru.items()}), m


if __name__ == '__main__':
    B, E, F = cand_B(), cand_E(), cand_F()
    for nm, cs in (('B', B), ('E', E), ('F', F)):
        a = np.array([c['ret'] for c in cs]) * 100; print(f"{nm}: n={len(cs)} avg={a.mean():+.3f}% WR={(a > 0).mean() * 100:.0f}%", flush=True)
    ALL = B + E + F
    if '--save' in sys.argv:
        p = json.loads(sys.argv[sys.argv.index('--save') + 1]); p['rules'] = tuple(p.get('rules', ('B', 'E', 'F')))
        eq, tr, cu = simulate(ALL, **p); r, m = report(eq, tr, cu); print(r)
        cu.sort(); json.dump(dict(params={k: v for k, v in p.items()}, report=r, months={k: round(v) for k, v in sorted(m.items())},
                                  curve=[[int(times[i]), round(v, 2)] for i, v in cu[::max(1, len(cu) // 400)]], coins=syms,
                                  period=[DAY[START], DAY[-1]]), open('web/backtest.json', 'w'), ensure_ascii=False)
        print('saved web/backtest.json'); sys.exit()
    print("\nrules   risk  eShare fShare fOpen →  final     CAGR   maxDD  trades winM   years")
    for rules in (('B', 'E'), ('F',), ('E', 'F'), ('B', 'E', 'F')):
        for risk, es, fs, fo in ((0.005, 0.1, 0.10, 2), (0.01, 0.2, 0.15, 3), (0.01, 0.2, 0.25, 3), (0.015, 0.3, 0.25, 4), (0.02, 0.4, 0.35, 4)):
            eq, tr, cu = simulate(ALL, rules, risk, 4, es, fs, fo); r, _ = report(eq, tr, cu)
            print(f"{'+'.join(rules):7} {risk * 100:4.1f}% {es:5.2f} {fs:5.2f} {fo:3d}  → ${r['final']:>8,} {r['cagr'] * 100:+6.1f}% {r['maxdd'] * 100:6.1f}% {r['n']:6d} {r['win_m'] * 100:4.0f}%  {r['years']}", flush=True)
