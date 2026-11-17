"""Backlog 187 (17.11.2026 09:15 Athens): beta decomposition of the LIVE «Киты» desk copies and the REAL account copies.
Pre-registered (same as 186): copy % = side*(exit/entry-1)*100 - 0.11 (funding ignored); market = side*beta*BTC return
over [opened, closed] (15m closes, Binance); B1 beta=1, BE beta = OLS 1h coin on BTC 30d before entry clipped 0..3.
alpha = copy - market. Verdict: skill if alpha>0, t>=2.4 in both; negative if <0, t<=-2.4 in both; else ~0 if |mkt|>=|alpha|.
Also by entry day (cluster) and by trader. Only copies with prices available (data ends 16.11 05:12 UTC).
Usage: whale_beta187.py lab-data-whales/whale_rules_data_20261116.json.gz OUT.json (run from the repo root). Research only."""
import gzip, json, sys, math, statistics as st, bisect, collections, time
D = json.load(gzip.open(sys.argv[1])); M = D['mkt']; END = D['made']
def ser(c):
    v = M.get(c)
    if not v or not v['k15']: return None
    return [r[0] for r in v['k15']], [r[4] for r in v['k15']]
CACHE = {}
def S(c):
    if c not in CACHE: CACHE[c] = ser(c)
    return CACHE[c]
def px(c, t):
    s = S(c)
    if not s: return None
    i = bisect.bisect_right(s[0], t - 15 * 60000) - 1  # last fully closed 15m bar
    if i < 0 or t - s[0][i] > 3 * 3600000: return None
    return s[1][i]
def beta(c, t0):
    if c == 'BTC': return 1.0
    a, b = S(c), S('BTC')
    if not a: return None
    H = 3600000; xs = []; ys = []
    am = dict(zip(a[0], a[1])); bm = dict(zip(b[0], b[1]))
    t = (t0 // H) * H - 30 * 86400000
    while t < (t0 // H) * H:
        if t in am and t + H in am and t in bm and t + H in bm:
            xs.append(bm[t + H] / bm[t] - 1); ys.append(am[t + H] / am[t] - 1)
        t += H
    if len(xs) < 200: return None
    mx, my = st.mean(xs), st.mean(ys); vx = sum((x - mx) ** 2 for x in xs)
    return min(3, max(0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx)) if vx else None
def tst(xs):
    if len(xs) < 2: return {'n': len(xs)}
    m, sd = st.mean(xs), st.stdev(xs)
    return {'n': len(xs), 'mean': round(m, 2), 't': round(m / (sd / math.sqrt(len(xs))), 2) if sd else None, 'win': round(sum(x > 0 for x in xs) / len(xs), 2)}
def rows(items):
    out = []; miss = collections.Counter()
    for c, side, e, x, t0, t1, who in items:
        if t1 > END: miss['после конца данных'] += 1; continue
        b0, b1 = px('BTC', t0), px('BTC', t1)
        be = beta(c, t0)
        if not b0 or not b1: miss['нет BTC'] += 1; continue
        if be is None: miss['нет беты'] += 1; continue
        s = 1 if side.upper() in ('LONG', 'BUY') else -1
        r = s * (x / e - 1) * 100 - 0.11; br = (b1 / b0 - 1) * 100
        out.append({'c': c, 'who': who, 'day': time.strftime('%m-%d', time.gmtime(t0 / 1000)), 's': s, 'r': r,
                    'm1': s * br, 'mE': s * be * br, 'a1': r - s * br, 'aE': r - s * be * br, 'pl': be * br, 'beta': be})
    return out, miss
def rep(name, R, miss):
    print(f'== {name}: {len(R)} copies (L {sum(r["s"]>0 for r in R)}/S {sum(r["s"]<0 for r in R)}), skipped {dict(miss)}')
    if not R: return {}
    o = {'copy': tst([r['r'] for r in R]), 'mkt_BE': tst([r['mE'] for r in R]), 'alpha_BE': tst([r['aE'] for r in R]),
         'mkt_B1': tst([r['m1'] for r in R]), 'alpha_B1': tst([r['a1'] for r in R]), 'passive_long': tst([r['pl'] for r in R])}
    byd = collections.defaultdict(list)
    for r in R: byd[r['day']].append(r['aE'])
    o['alpha_BE_by_day'] = tst([st.mean(v) for v in byd.values()])
    byw = collections.defaultdict(list)
    for r in R: byw[r['who']].append(r)
    o['by_trader'] = {w: {'n': len(v), 'copy': round(st.mean(x['r'] for x in v), 2), 'alphaE': tst([x['aE'] for x in v])} for w, v in sorted(byw.items(), key=lambda kv: -len(kv[1])) if len(v) >= 4}
    A, B = o['alpha_BE'], o['alpha_B1']
    if A.get('t') and B.get('t') and A['mean'] > 0 and B['mean'] > 0 and A['t'] >= 2.4 and B['t'] >= 2.4: v = 'умение есть'
    elif A.get('t') and B.get('t') and A['mean'] < 0 and B['mean'] < 0 and A['t'] <= -2.4 and B['t'] <= -2.4: v = 'умение отрицательное'
    elif abs(o['mkt_BE'].get('mean', 0)) >= abs(A.get('mean', 0)): v = 'умение ≈ 0 — итог объясняет рынок'
    else: v = 'не доказано'
    o['verdict'] = v
    for k, vv in o.items(): print(' ', k, vv)
    return o
desk = json.load(open('web/whales_live.json'))['desk']
items = [(c['coin'], c['side'], c['entry'], c['exit'], c['opened_t'], c['closed_t'], c['whale']) for c in desk['closed'] if c.get('exit') and c.get('entry')]
real = json.load(open('web/public_real.json'))['closed']
ritems = [(x['inst'].split('-')[0], x['side'], x['open_px'], x['close_px'], x['opened'], x['closed'], 'real') for x in real if 'SWAP' in x.get('inst', '') and x.get('open_px') and x.get('close_px')]
O = {}
for nm, it in (('DESK', items), ('REAL', ritems)):
    R, miss = rows(it); O[nm] = rep(nm, R, miss)
    if nm == 'DESK':
        a = [r for r in R if r['day'] < '11-10']; b = [r for r in R if r['day'] >= '11-10']
        O['DESK_h1'] = rep('DESK до 10.11', a, {}); O['DESK_h2'] = rep('DESK с 10.11', b, {})
print('first/last desk open', min(i[4] for i in items), max(i[4] for i in items), 'END', END)
json.dump(O, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
