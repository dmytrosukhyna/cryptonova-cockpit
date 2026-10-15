"""Autopilot — whole-portfolio backtest of the one strategy the desk runs (all rules together, one account).
10 coins, 15m bars 2020-09 → 2026-09 (data/binance_research.sqlite), costs 0.05 %/side fee + 0.02 % slip.
Rules (same as cnq/agents/paper_desk.py):
  R  retest of a broken 4h level (30-bar breakout on 1.3× volume, retest within 24 bars, stop level ∓ 1 ATR4h, target 2R, ≤ 48 bars)
     — no R-longs and no R-shorts while breadth ≥ 70 % (broad sell-off)
  B  liquidation-cascade long: 15m close-to-close ≤ −3.5σ (30-day σ) with lower wick ≥ 50 % → long next open, stop low − 0.5 ATR15, target 1R, ≤ 8 h
  E  climax long in a broad sell-off: breadth ≥ 70 %, 15m candle ≤ −1.5 % on ≥ 2× 24h-average volume → basket (share of equity split equally), stop −12 %, exit 24 h; one basket per 20 h
Portfolio: risk % of current equity per R/B trade, max N simultaneous R/B positions, one position per coin, equity compounds on exits.
Usage: python scripts/autopilot_backtest.py            → grid of variants
       python scripts/autopilot_backtest.py --best     → details of the chosen setting (years, months, rule split) → JSON for the panel"""
import sqlite3, sys, json, collections, datetime as dt
import numpy as np

FEE, SLIP = 0.0005, 0.0002
COST = 2 * (FEE + SLIP)
conn = sqlite3.connect('data/binance_research.sqlite')
syms = [r[0] for r in conn.execute("select distinct symbol from candles where interval='15m'")]
D = {s: np.array(conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall(), dtype=float) for s in syms}
times = np.array(sorted(set.intersection(*[set(D[s][:, 0].astype(int)) for s in syms]))); T = len(times)
NS = len(syms)
O, H, L, C, V = (np.full((NS, T), np.nan) for _ in range(5))
for k, s in enumerate(syms):
    pos = {int(t): i for i, t in enumerate(D[s][:, 0].astype(int))}
    ii = np.array([pos[int(t)] for t in times])
    O[k], H[k], L[k], C[k], V[k] = (D[s][ii, c] for c in (1, 2, 3, 4, 5))
ret24 = np.zeros((NS, T)); ret24[:, 96:] = C[:, 96:] / C[:, :-96] - 1
BR = (ret24 < -0.03).mean(axis=0); BR[:96] = 0
DAY = np.array([dt.datetime.utcfromtimestamp(t / 1000).strftime('%Y-%m-%d') for t in times])


def atr15(k, n=14):
    tr = np.r_[H[k, 0] - L[k, 0], np.maximum(H[k, 1:] - L[k, 1:], np.maximum(abs(H[k, 1:] - C[k, :-1]), abs(L[k, 1:] - C[k, :-1])))]
    a = np.full(T, np.nan); a[n - 1:] = np.convolve(tr, np.ones(n) / n, 'valid'); return a


def walk(k, i0, side, stop, tgt, max_bars):
    """enter at O[k,i0]; returns (exit_index, gross return on notional)"""
    e = O[k, i0]; sg = 1 if side == 'LONG' else -1
    end = min(T - 1, i0 + max_bars)
    for j in range(i0, end + 1):
        if stop is not None and (L[k, j] <= stop if sg > 0 else H[k, j] >= stop):
            return j, sg * (stop / e - 1)
        if tgt is not None and (H[k, j] >= tgt if sg > 0 else L[k, j] <= tgt):
            return j, sg * (tgt / e - 1)
    return end, sg * (C[k, end] / e - 1)


def cand_R(N=30, wait=24, hold=48, tgtR=2.0, stopATR=1.0):
    out = []
    for k in range(NS):
        n4 = T // 16
        o = O[k, :n4 * 16].reshape(n4, 16)[:, 0]; h = H[k, :n4 * 16].reshape(n4, 16).max(1); l = L[k, :n4 * 16].reshape(n4, 16).min(1)
        c = C[k, :n4 * 16].reshape(n4, 16)[:, -1]; v = V[k, :n4 * 16].reshape(n4, 16).sum(1)
        tr = np.r_[h[0] - l[0], np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
        a4 = np.full(n4, np.nan); a4[13:] = np.convolve(tr, np.ones(14) / 14, 'valid')
        i = N + 20
        while i < n4 - hold - 2:
            a = a4[i]; hi = h[i - N:i].max(); lo = l[i - N:i].min(); vav = v[i - 20:i].mean()
            side = 'LONG' if c[i] > hi + 0.3 * a else 'SHORT' if c[i] < lo - 0.3 * a else None
            if side is None or v[i] < 1.3 * vav or not np.isfinite(a):
                i += 1; continue
            lvl = hi if side == 'LONG' else lo; done = False; j = i + 1
            while j < min(n4 - hold - 2, i + 1 + wait):
                if side == 'LONG' and l[j] <= lvl + 0.5 * a and c[j] > lvl:
                    i0 = (j + 1) * 16; e = O[k, i0]; stop = lvl - stopATR * a
                    if e - stop > 0 and (e - stop) / e <= 0.08:
                        x, g = walk(k, i0, 'LONG', stop, e + tgtR * (e - stop), hold * 16)
                        out.append(dict(rule='R', k=k, i=i0, x=x, side='LONG', ret=g - COST, sf=(e - stop) / e + COST))
                    done = True; break
                if side == 'LONG' and c[j] < lvl - 0.5 * a: break
                if side == 'SHORT' and h[j] >= lvl - 0.5 * a and c[j] < lvl:
                    i0 = (j + 1) * 16; e = O[k, i0]; stop = lvl + stopATR * a
                    if stop - e > 0 and (stop - e) / e <= 0.08:
                        x, g = walk(k, i0, 'SHORT', stop, e - tgtR * (stop - e), hold * 16)
                        out.append(dict(rule='R', k=k, i=i0, x=x, side='SHORT', ret=g - COST, sf=(stop - e) / e + COST))
                    done = True; break
                if side == 'SHORT' and c[j] > lvl + 0.5 * a: break
                j += 1
            i = j + 1 if done else i + 1
    return out


def cand_B(sig_mult=3.5, tgtR=1.0, hold=32):
    out = []
    for k in range(NS):
        r = np.r_[0, C[k, 1:] / C[k, :-1] - 1]
        r2 = np.convolve(r * r, np.ones(2880), 'full')[:T] / 2880
        sig = np.sqrt(r2); a = atr15(k); rng = H[k] - L[k]
        wick = (np.minimum(O[k], C[k]) - L[k]) / np.where(rng > 0, rng, np.nan)
        idx = np.where((r <= -sig_mult * sig) & (wick >= 0.5) & (np.arange(T) > 3000))[0]
        for i in idx:
            if i + 1 >= T - hold - 1: continue
            e = O[k, i + 1]; stop = L[k, i] - 0.5 * a[i]
            if not (e - stop > 0): continue
            x, g = walk(k, i + 1, 'LONG', stop, e + tgtR * (e - stop), hold)
            out.append(dict(rule='B', k=k, i=i + 1, x=x, side='LONG', ret=g - COST, sf=(e - stop) / e + COST))
    return out


def cand_E(drop=-0.015, vol=2.0, bth=0.7, hold=96, cat=0.12):
    out = []
    for k in range(NS):
        cs = np.cumsum(np.nan_to_num(V[k])); vav = np.full(T, np.nan); vav[97:] = (cs[96:-1] - cs[:-97]) / 96
        d = C[k] / O[k] - 1
        for i in np.where((d <= drop) & (V[k] >= vol * vav) & (BR >= bth))[0]:
            if i < 200 or i + 1 >= T - hold - 1: continue
            e = O[k, i + 1]
            x, g = walk(k, i + 1, 'LONG', e * (1 - cat), None, hold)
            out.append(dict(rule='E', k=k, i=i + 1, x=x, side='LONG', ret=g - COST, sf=cat))
    return out


def simulate(cands, risk=0.01, max_open=4, e_share=0.20, rules=('R', 'B', 'E'), stress_skip_R=True, start=10_000.0):
    ev = sorted([c for c in cands if c['rule'] in rules], key=lambda c: (c['i'], c['rule']))
    eq = start; open_ = []; last_e = -10 ** 9; trades = []
    curve = [(0, eq)]
    byi = collections.defaultdict(list)
    for c in ev: byi[c['i']].append(c)
    for i in sorted(byi):
        # settle exits before i
        still = []
        for o in sorted(open_, key=lambda o: o['x']):
            if o['x'] < i:
                eq += o['pnl']; trades.append(o); curve.append((o['x'], eq))
            else:
                still.append(o)
        open_ = still
        held = {o['k'] for o in open_}
        es = [c for c in byi[i] if c['rule'] == 'E' and c['k'] not in held]
        if es and i - last_e >= 80:
            notional = eq * e_share / len(es)
            for c in es:
                open_.append(dict(c, pnl=notional * c['ret'], usd=notional)); held.add(c['k'])
            last_e = i
        for c in byi[i]:
            if c['rule'] == 'E' or c['k'] in held: continue
            if stress_skip_R and c['rule'] == 'R' and BR[c['i']] >= 0.7: continue
            if sum(1 for o in open_ if o['rule'] != 'E') >= max_open: continue
            notional = eq * risk / c['sf']
            notional = min(notional, eq * 3)            # leverage cap 3× per position
            open_.append(dict(c, pnl=notional * c['ret'], usd=notional)); held.add(c['k'])
    for o in sorted(open_, key=lambda o: o['x']):
        eq += o['pnl']; trades.append(o); curve.append((o['x'], eq))
    return eq, trades, curve


def report(eq, trades, curve, start=10_000.0):
    curve.sort(); vals = np.array([v for _, v in curve]); peak = np.maximum.accumulate(vals); dd = ((vals - peak) / peak).min()
    years = (times[-1] - times[3000]) / (365.25 * 86400_000)
    cagr = (eq / start) ** (1 / years) - 1
    m = collections.defaultdict(float)
    for t in trades: m[DAY[t['x']][:7]] += t['pnl']
    mv = np.array(list(m.values())) if m else np.array([0.0])
    oos = sum(t['pnl'] for t in trades if times[t['x']] >= 1733000000000)
    return dict(final=eq, cagr=cagr, maxdd=dd, n=len(trades), months=len(mv), win_m=(mv > 0).mean(), oos=oos)


if __name__ == '__main__':
    print(f"coins {NS}: {', '.join(syms)} · bars {T} · {DAY[0]} → {DAY[-1]}", flush=True)
    R = cand_R(); B = cand_B(); E = cand_E()
    for nm, cs in (('R', R), ('B', B), ('E', E)):
        a = np.array([c['ret'] / c['sf'] for c in cs]); print(f"{nm}: n={len(cs)} avgR={a.mean():+.3f} WR={(a > 0).mean() * 100:.0f}%", flush=True)
    ALL = R + B + E
    if '--best' not in sys.argv:
        print("\nrules        risk  max  eShare →  $10k →     CAGR   maxDD  trades  win-months  OOS(12.2024→)$")
        for rules in (('R',), ('B',), ('E',), ('R', 'E'), ('B', 'E'), ('R', 'B'), ('R', 'B', 'E')):
            for risk, mo, es in ((0.005, 2, 0.1), (0.01, 4, 0.2), (0.015, 6, 0.3), (0.02, 6, 0.4)):
                eq, tr, cu = simulate(ALL, risk, mo, es, rules)
                r = report(eq, tr, cu)
                print(f"{'+'.join(rules):10} {risk * 100:4.1f}% {mo:3d} {es:5.2f}  → ${r['final']:>9,.0f} {r['cagr'] * 100:+7.1f}% {r['maxdd'] * 100:6.1f}% {r['n']:6d}   {r['win_m'] * 100:4.0f}% of {r['months']:2d}   {r['oos']:+9,.0f}", flush=True)
    else:
        args = json.loads(sys.argv[sys.argv.index('--best') + 1]) if len(sys.argv) > sys.argv.index('--best') + 1 else {}
        eq, tr, cu = simulate(ALL, **args)
        r = report(eq, tr, cu); print(r)
        y = collections.defaultdict(float); ry = collections.defaultdict(lambda: collections.defaultdict(float)); m = collections.defaultdict(float)
        for t in tr:
            y[DAY[t['x']][:4]] += t['pnl']; ry[t['rule']][DAY[t['x']][:4]] += t['pnl']; m[DAY[t['x']][:7]] += t['pnl']
        print("by year $:", {k: round(v) for k, v in sorted(y.items())})
        for rule, d in ry.items(): print(f"  {rule}:", {k: round(v) for k, v in sorted(d.items())})
        cu.sort(); out = dict(params=args, report={k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()},
                               years={k: round(v) for k, v in sorted(y.items())}, months={k: round(v) for k, v in sorted(m.items())},
                               curve=[[int(times[i]), round(v, 2)] for i, v in cu[::max(1, len(cu) // 400)]], coins=syms)
        json.dump(out, open('web/backtest.json', 'w'), ensure_ascii=False)
        print("saved web/backtest.json")
