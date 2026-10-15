"""Breakout → retest (role reversal). 4h bars from 15m, 10 coins 2020-09→2026-09.
Breakout UP: 4h close > max high of previous N bars + 0.3 ATR, volume ≥ 1.3× avg(20). Level = that prior max high.
Retest: within next 24 bars (4 days) a bar's low comes within 0.5 ATR of the level and the bar closes above the level.
Entry at next open, stop = level − 1 ATR, target T·R, hold ≤ 48 bars (8 days). Mirror for DOWN breakouts (shorts).
Costs 0.07 %+0.02 % per side; stop first inside a bar."""
import sqlite3, sys, numpy as np
conn = sqlite3.connect('data/binance_research.sqlite')
syms = [r[0] for r in conn.execute("select distinct symbol from candles where interval='15m'")]
FEE = 0.0009
def agg(rows, n=16):
    k = len(rows) // n; r = np.array(rows[:k*n], dtype=float).reshape(k, n, 6)
    return np.stack([r[:,0,0], r[:,0,1], r[:,:,2].max(1), r[:,:,3].min(1), r[:,-1,4], r[:,:,5].sum(1)], 1)
def atr(b, n=14):
    tr = np.maximum(b[1:,2]-b[1:,3], np.maximum(abs(b[1:,2]-b[:-1,4]), abs(b[1:,3]-b[:-1,4])))
    a = np.full(len(b), np.nan); a[n:] = np.convolve(tr, np.ones(n)/n, 'valid')[:len(b)-n]; return a
def run(N=30, volx=1.3, tgtR=2.0, hold=48, stopATR=1.0, need_vol=True, wait=24):
    res = {'LONG': [], 'SHORT': []}
    for s in syms:
        rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
        b = agg(rows); A = atr(b); V = np.convolve(b[:,5], np.ones(20)/20, 'valid'); V = np.concatenate([np.full(19, np.nan), V])
        T = len(b); i = N + 20; last_lvl = None
        while i < T - hold - wait - 2:
            a = A[i]
            if not np.isfinite(a) or a <= 0: i += 1; continue
            hiN = b[i-N:i, 2].max(); loN = b[i-N:i, 3].min(); volok = (not need_vol) or b[i,5] >= volx * V[i]
            side = None
            if b[i,4] > hiN + 0.3*a and volok: side, lvl = 'LONG', hiN
            elif b[i,4] < loN - 0.3*a and volok: side, lvl = 'SHORT', loN
            if side is None: i += 1; continue
            # look for retest
            j = i + 1; done = False
            while j < min(T - hold - 2, i + 1 + wait):
                if side == 'LONG' and b[j,3] <= lvl + 0.5*a and b[j,4] > lvl:
                    e = b[j+1,1]; stop = lvl - stopATR*a; r = e - stop
                    if r > 0:
                        tgt = e + tgtR*r; out = None
                        for k in range(j+1, j+1+hold):
                            if b[k,3] <= stop: out = -(e-stop)/r - FEE*e/r; break
                            if b[k,2] >= tgt: out = (tgt-e)/r - FEE*e/r; break
                        if out is None: out = (b[j+hold,4]-e)/r - FEE*e/r
                        res[side].append((b[j,0], out)); done = True
                    break
                if side == 'LONG' and b[j,4] < lvl - 0.5*a: break   # failed breakout
                if side == 'SHORT' and b[j,2] >= lvl - 0.5*a and b[j,4] < lvl:
                    e = b[j+1,1]; stop = lvl + stopATR*a; r = stop - e
                    if r > 0:
                        tgt = e - tgtR*r; out = None
                        for k in range(j+1, j+1+hold):
                            if b[k,2] >= stop: out = -(stop-e)/r - FEE*e/r; break
                            if b[k,3] <= tgt: out = (e-tgt)/r - FEE*e/r; break
                        if out is None: out = (e-b[j+hold,4])/r - FEE*e/r
                        res[side].append((b[j,0], out)); done = True
                    break
                if side == 'SHORT' and b[j,4] > lvl + 0.5*a: break
                j += 1
            i = (j + 1) if done else i + 1
    return res
cut = 1734000000000  # ~Dec 2024 → last third OOS
def show(name, res):
    for side in ('LONG', 'SHORT'):
        a = np.array([x for _, x in res[side]]); o = np.array([x for t, x in res[side] if t >= cut])
        if len(a): print(f"  {name:36} {side:5} n={len(a):4d} avgR={a.mean():+.3f} WR={int((a>0).mean()*100)}% | OOS n={len(o)} {o.mean() if len(o) else float('nan'):+.3f}")
show("N=30 vol1.3 T2 stop1ATR", run())
show("N=30 no-vol T2", run(need_vol=False))
show("N=60 vol1.3 T2", run(N=60))
show("N=30 vol1.3 T1.5 stop0.5ATR", run(tgtR=1.5, stopATR=0.5))
show("N=30 vol1.3 T3 stop1ATR hold96", run(tgtR=3.0, hold=96))
show("N=120 vol1.3 T2 (20-day)", run(N=120))

if __name__ == '__main__' and '--years' in sys.argv:
    import datetime, random
    res = run()
    for side in ('LONG', 'SHORT'):
        y = {}
        for t, r in res[side]: y.setdefault(datetime.datetime.utcfromtimestamp(t/1000).year, []).append(r)
        print(side, {k: (len(v), round(float(np.mean(v)), 2)) for k, v in sorted(y.items())})
    # random baseline with identical geometry (stop 1 ATR, target 2R, hold 48) at random 4h bars
    random.seed(3); out = {'LONG': [], 'SHORT': []}
    for s in syms:
        rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
        b = agg(rows); A = atr(b)
        for _ in range(320):
            j = random.randrange(40, len(b)-60); a = A[j]
            if not np.isfinite(a): continue
            for side in ('LONG', 'SHORT'):
                e = b[j+1,1]; stop = e - a if side == 'LONG' else e + a; r = a; tgt = e + 2*r if side == 'LONG' else e - 2*r; o = None
                for k in range(j+1, j+49):
                    if side == 'LONG' and b[k,3] <= stop: o = -1 - FEE*e/r; break
                    if side == 'LONG' and b[k,2] >= tgt: o = 2 - FEE*e/r; break
                    if side == 'SHORT' and b[k,2] >= stop: o = -1 - FEE*e/r; break
                    if side == 'SHORT' and b[k,3] <= tgt: o = 2 - FEE*e/r; break
                if o is None: o = ((b[j+48,4]-e) if side == 'LONG' else (e-b[j+48,4]))/r - FEE*e/r
                out[side].append(o)
    for side in out: a = np.array(out[side]); print(f"random {side}: n={len(a)} avgR={a.mean():+.3f} WR={int((a>0).mean()*100)}%")

if __name__ == '__main__' and '--regime' in sys.argv:
    # regime filter: LONG retests only when the coin's close is above its 200-day SMA (1200 4h bars), SHORT only below
    import datetime
    def run_regime(N=30, volx=1.3, tgtR=2.0, hold=48, stopATR=1.0, wait=24):
        res = {'LONG': [], 'SHORT': []}
        for s in syms:
            rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
            b = agg(rows); A = atr(b); V = np.concatenate([np.full(19, np.nan), np.convolve(b[:,5], np.ones(20)/20, 'valid')])
            S = np.concatenate([np.full(1199, np.nan), np.convolve(b[:,4], np.ones(1200)/1200, 'valid')])
            T = len(b); i = 1220
            while i < T - hold - wait - 2:
                a = A[i]
                if not np.isfinite(a) or a <= 0 or not np.isfinite(S[i]): i += 1; continue
                hiN = b[i-N:i, 2].max(); loN = b[i-N:i, 3].min(); volok = b[i,5] >= volx * V[i]; bull = b[i,4] > S[i]
                side = None
                if b[i,4] > hiN + 0.3*a and volok and bull: side, lvl = 'LONG', hiN
                elif b[i,4] < loN - 0.3*a and volok and not bull: side, lvl = 'SHORT', loN
                if side is None: i += 1; continue
                j = i + 1; done = False
                while j < min(T - hold - 2, i + 1 + wait):
                    if side == 'LONG' and b[j,3] <= lvl + 0.5*a and b[j,4] > lvl:
                        e = b[j+1,1]; stop = lvl - stopATR*a; r = e - stop
                        if r > 0:
                            tgt = e + tgtR*r; out = None
                            for k in range(j+1, j+1+hold):
                                if b[k,3] <= stop: out = -(e-stop)/r - FEE*e/r; break
                                if b[k,2] >= tgt: out = (tgt-e)/r - FEE*e/r; break
                            if out is None: out = (b[j+hold,4]-e)/r - FEE*e/r
                            res[side].append((b[j,0], out)); done = True
                        break
                    if side == 'LONG' and b[j,4] < lvl - 0.5*a: break
                    if side == 'SHORT' and b[j,2] >= lvl - 0.5*a and b[j,4] < lvl:
                        e = b[j+1,1]; stop = lvl + stopATR*a; r = stop - e
                        if r > 0:
                            tgt = e - tgtR*r; out = None
                            for k in range(j+1, j+1+hold):
                                if b[k,2] >= stop: out = -(stop-e)/r - FEE*e/r; break
                                if b[k,3] <= tgt: out = (e-tgt)/r - FEE*e/r; break
                            if out is None: out = (e-b[j+hold,4])/r - FEE*e/r
                            res[side].append((b[j,0], out)); done = True
                        break
                    if side == 'SHORT' and b[j,4] > lvl + 0.5*a: break
                    j += 1
                i = (j + 1) if done else i + 1
        return res
    res = run_regime(); show("REGIME-filtered N=30 T2", res)
    for side in ('LONG', 'SHORT'):
        y = {}
        for t, r in res[side]: y.setdefault(datetime.datetime.utcfromtimestamp(t/1000).year, []).append(r)
        print(side, {k: (len(v), round(float(np.mean(v)), 2)) for k, v in sorted(y.items())})

if __name__ == '__main__' and '--gerchik' in sys.argv:
    """G: after the retest bar, wait for a 4h close beyond the retest bar's extreme (second breakout) → enter next open.
       F: false breakout — breakout bar closed beyond the level, but within 6 bars a close comes back beyond the level by 0.3 ATR
          → fade: short at next open, stop above the breakout extreme, target 2R (mirror for down-breaks)."""
    import datetime
    def run_g(N=30, volx=1.3, tgtR=2.0, hold=48, stopATR=1.0, wait=24, confirm=6):
        res = {'LONG': [], 'SHORT': []}
        for s in syms:
            rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
            b = agg(rows); A = atr(b); V = np.concatenate([np.full(19, np.nan), np.convolve(b[:,5], np.ones(20)/20, 'valid')])
            T = len(b); i = N + 20
            while i < T - hold - wait - confirm - 3:
                a = A[i]
                if not np.isfinite(a) or a <= 0: i += 1; continue
                hiN = b[i-N:i,2].max(); loN = b[i-N:i,3].min(); volok = b[i,5] >= volx*V[i]; side = None
                if b[i,4] > hiN + 0.3*a and volok: side, lvl = 'LONG', hiN
                elif b[i,4] < loN - 0.3*a and volok: side, lvl = 'SHORT', loN
                if side is None: i += 1; continue
                j = i + 1; done = False
                while j < min(T - hold - confirm - 3, i + 1 + wait):
                    if side == 'LONG' and b[j,4] < lvl - 0.5*a: break
                    if side == 'SHORT' and b[j,4] > lvl + 0.5*a: break
                    isret = (b[j,3] <= lvl + 0.5*a and b[j,4] > lvl) if side == 'LONG' else (b[j,2] >= lvl - 0.5*a and b[j,4] < lvl)
                    if isret:
                        ext = b[j,2] if side == 'LONG' else b[j,3]   # retest bar extreme
                        for q in range(j+1, j+1+confirm):
                            if side == 'LONG' and b[q,4] < lvl - 0.5*a: break
                            if side == 'SHORT' and b[q,4] > lvl + 0.5*a: break
                            if (side == 'LONG' and b[q,4] > ext) or (side == 'SHORT' and b[q,4] < ext):
                                e = b[q+1,1]; stop = lvl - stopATR*a if side == 'LONG' else lvl + stopATR*a; r = abs(e - stop)
                                if r <= 0: break
                                tgt = e + tgtR*r if side == 'LONG' else e - tgtR*r; out = None
                                for k in range(q+1, q+1+hold):
                                    if side == 'LONG' and b[k,3] <= stop: out = -1 - FEE*e/r; break
                                    if side == 'LONG' and b[k,2] >= tgt: out = tgtR - FEE*e/r; break
                                    if side == 'SHORT' and b[k,2] >= stop: out = -1 - FEE*e/r; break
                                    if side == 'SHORT' and b[k,3] <= tgt: out = tgtR - FEE*e/r; break
                                if out is None: out = ((b[q+hold,4]-e) if side == 'LONG' else (e-b[q+hold,4]))/r - FEE*e/r
                                res[side].append((b[q,0], out)); done = True; j = q; break
                        break
                    j += 1
                i = (j + 1) if done else i + 1
        return res
    def run_f(N=30, volx=1.3, tgtR=2.0, hold=48, within=6):
        res = {'LONG': [], 'SHORT': []}   # side = direction of the FADE trade
        for s in syms:
            rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
            b = agg(rows); A = atr(b); V = np.concatenate([np.full(19, np.nan), np.convolve(b[:,5], np.ones(20)/20, 'valid')])
            T = len(b); i = N + 20
            while i < T - hold - within - 3:
                a = A[i]
                if not np.isfinite(a) or a <= 0: i += 1; continue
                hiN = b[i-N:i,2].max(); loN = b[i-N:i,3].min(); volok = b[i,5] >= volx*V[i]; brk = None
                if b[i,4] > hiN + 0.3*a and volok: brk, lvl = 'UP', hiN
                elif b[i,4] < loN - 0.3*a and volok: brk, lvl = 'DOWN', loN
                if brk is None: i += 1; continue
                done = False
                for q in range(i+1, i+1+within):
                    if brk == 'UP' and b[q,4] < lvl - 0.3*a:        # failed up-break → short
                        e = b[q+1,1]; stop = b[i:q+1,2].max() + 0.25*a; r = stop - e
                        if r <= 0: break
                        tgt = e - tgtR*r; out = None
                        for k in range(q+1, q+1+hold):
                            if b[k,2] >= stop: out = -1 - FEE*e/r; break
                            if b[k,3] <= tgt: out = tgtR - FEE*e/r; break
                        if out is None: out = (e-b[q+hold,4])/r - FEE*e/r
                        res['SHORT'].append((b[q,0], out)); done = True; i = q; break
                    if brk == 'DOWN' and b[q,4] > lvl + 0.3*a:      # failed down-break → long
                        e = b[q+1,1]; stop = b[i:q+1,3].min() - 0.25*a; r = e - stop
                        if r <= 0: break
                        tgt = e + tgtR*r; out = None
                        for k in range(q+1, q+1+hold):
                            if b[k,3] <= stop: out = -1 - FEE*e/r; break
                            if b[k,2] >= tgt: out = tgtR - FEE*e/r; break
                        if out is None: out = (b[q+hold,4]-e)/r - FEE*e/r
                        res['LONG'].append((b[q,0], out)); done = True; i = q; break
                i += 1
        return res
    show("G: retest + second breakout", run_g())
    show("G: ... target 1.5R", run_g(tgtR=1.5))
    show("F: false-breakout fade T2", run_f())
    show("F: false-breakout fade T1.5", run_f(tgtR=1.5))
    # how often does a breakout fail (close back beyond level within 24 bars) vs succeed?
    fails = 0; tot = 0
    for s in syms:
        rows = conn.execute("select open_time,open,high,low,close,volume from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
        b = agg(rows); A = atr(b); V = np.concatenate([np.full(19, np.nan), np.convolve(b[:,5], np.ones(20)/20, 'valid')]); T = len(b)
        for i in range(50, T - 30):
            a = A[i]
            if not np.isfinite(a) or a <= 0 or not (b[i,5] >= 1.3*V[i]): continue
            hiN = b[i-30:i,2].max(); loN = b[i-30:i,3].min()
            if b[i,4] > hiN + 0.3*a: tot += 1; fails += int((b[i+1:i+25,4] < hiN - 0.5*a).any())
            elif b[i,4] < loN - 0.3*a: tot += 1; fails += int((b[i+1:i+25,4] > loN + 0.5*a).any())
    print(f"breakouts on volume: {tot}, failed (closed back beyond level within 4 days): {fails/tot*100:.0f}%")

if __name__ == '__main__' and '--stress' in sys.argv:
    # Split retest trades by market breadth at the breakout bar: stress = ≥70 % of coins down >3 % over 24h (15m grid).
    import collections
    D = {}
    for s in syms:
        rows = conn.execute("select open_time, close from candles where symbol=? and interval='15m' order by open_time", (s,)).fetchall()
        D[s] = dict(rows)
    times = sorted(set.intersection(*[set(D[s]) for s in syms]))
    br = {}
    for n, t in enumerate(times):
        if n < 96: continue
        t0 = times[n - 96]; xs = [D[s][t] / D[s][t0] - 1 for s in syms]
        br[t] = sum(1 for x in xs if x < -0.03) / len(xs)
    def breadth_at(ms):
        # nearest 15m grid time at or before ms
        k = ms - ms % 900_000
        for _ in range(8):
            if k in br: return br[k]
            k -= 900_000
        return 0.0
    res = run()
    for side in ('LONG', 'SHORT'):
        groups = collections.defaultdict(list)
        for t, r in res[side]:
            groups['stress' if breadth_at(int(t)) >= 0.7 else 'calm'].append(r)
        for g, rs in groups.items():
            a = np.array(rs); print(f"R {side:5} {g:6} n={len(a):4d} avgR={a.mean():+.3f} WR={(a>0).mean()*100:3.0f}% sumR={a.sum():+.0f}")
