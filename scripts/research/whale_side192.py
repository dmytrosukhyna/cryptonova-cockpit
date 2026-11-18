"""Backlog 192 (18.11.2026 03:15 Athens): does the followed list's CHOICE OF SIDE beat simple side rules on the same segments?
Pre-registered before running:
- Segments: each closed copy (coin, entry, exit, open/close time) keeps its own timing; only the side is swapped.
  Sets: DESK = web/whales_live.json desk.closed (18.10 →), REAL = web/public_real.json closed SWAP copies,
  POOL = all OKX lead traders in lab-data-whales/whale_rules_data_20261116.json.gz (their own closed positions),
  split POOL-H (opened 08.08 → 04.11 17:13 UTC) and POOL-F (04.11 17:13 → 16.11).
- Copy % = side*(exit/entry-1)*100 - 0.11 (no leverage, funding ignored — the same for both sides of a comparison).
- Exactly 3 simple side rules, decided at entry from BTC daily closes (Binance 15m → UTC day close, last FULL day):
  R200: long if BTC close > SMA200 else short; R90: same with SMA90; M7: long if BTC 7-day return > 0 else short.
  (Rule 4 «pool long share» from item 188 is not built yet — not tested here.)
- Metric per copy: diff = copy(trader side) - copy(rule side) = 0 when sides agree, else 2*trader_side*move.
  Verdict per set and rule: «список лучше» if mean diff > 0 with t >= 2.4 per copy AND per entry day (3 rules x sets);
  «правило лучше» mirror; else «не различить». Also the share of copies where sides agree and «always long».
Usage: python3 scripts/research/whale_side192.py lab-data-whales/whale_rules_data_20261116.json.gz OUT.json (repo root).
Research only, nothing trades."""
import gzip, json, sys, math, statistics as st, collections, time
D = json.load(gzip.open(sys.argv[1]))
k = D['mkt']['BTC']['k15']
DAY = 86400000
closes = {}
for r in k: closes[r[0] // DAY] = r[4]   # close of the last 15m bar that starts inside each UTC day
days = sorted(closes); last_full = days[-2]               # the final day in the data is partial
def rule_side(t):
    d = t // DAY - 1                                       # yesterday's close only
    if d > last_full or d not in closes: return None
    hist = [closes[x] for x in days if x <= d]
    if len(hist) < 200: return None
    c = hist[-1]
    s200 = 1 if c > sum(hist[-200:]) / 200 else -1
    s90 = 1 if c > sum(hist[-90:]) / 90 else -1
    m7 = 1 if c > hist[-8] else -1
    return {'R200': s200, 'R90': s90, 'M7': m7}
def tst(xs):
    if len(xs) < 2: return {'n': len(xs)}
    m, sd = st.mean(xs), st.stdev(xs)
    return {'n': len(xs), 'mean': round(m, 2), 't': round(m / (sd / math.sqrt(len(xs))), 2) if sd else None}
def run(name, items):
    rows = []; miss = 0
    for c, side, e, x, t0 in items:
        rs = rule_side(t0)
        if rs is None or not e or not x: miss += 1; continue
        s = 1 if side.upper() in ('LONG', 'BUY') else -1
        mv = (x / e - 1) * 100
        rows.append({'day': time.strftime('%m-%d', time.gmtime(t0 / 1000)), 's': s, 'mv': mv, 'rs': rs})
    out = {'n': len(rows), 'skipped': miss, 'longs': sum(r['s'] > 0 for r in rows)}
    if not rows: return out
    out['trader'] = tst([r['s'] * r['mv'] - 0.11 for r in rows])
    out['always_long'] = tst([r['mv'] - 0.11 for r in rows])
    for R in ('R200', 'R90', 'M7'):
        diff = [(r['s'] - r['rs'][R]) * r['mv'] for r in rows]
        byd = collections.defaultdict(list)
        for r, dd in zip(rows, diff): byd[r['day']].append(dd)
        a, b = tst(diff), tst([st.mean(v) for v in byd.values()])
        if a.get('t') and b.get('t') and a['mean'] > 0 and a['t'] >= 2.4 and b['t'] >= 2.4: v = 'список лучше'
        elif a.get('t') and b.get('t') and a['mean'] < 0 and a['t'] <= -2.4 and b['t'] <= -2.4: v = 'правило лучше'
        else: v = 'не различить'
        out[R] = {'agree': round(sum(r['s'] == r['rs'][R] for r in rows) / len(rows), 2),
                  'rule': tst([r['rs'][R] * r['mv'] - 0.11 for r in rows]), 'diff': a, 'diff_by_day': b, 'days': len(byd), 'verdict': v}
    print('==', name, json.dumps(out, ensure_ascii=False))
    return out
desk = json.load(open('web/whales_live.json'))['desk']['closed']
real = json.load(open('web/public_real.json'))['closed']
import calendar
F0 = calendar.timegm(time.strptime('2026-11-04 17:13', '%Y-%m-%d %H:%M')) * 1000  # 04.11 17:13 UTC (snapshot of items 170/186)
pool = [(p[1].split('-')[0], p[2], p[5], p[6], p[3]) for tr in D['okx'] for p in tr['closed'] if p[1].endswith('-SWAP') and p[2] in ('long', 'short')]
O = {
    'DESK': run('DESK', [(c['coin'], c['side'], c['entry'], c['exit'], c['opened_t']) for c in desk if c.get('exit')]),
    'REAL': run('REAL', [(x['inst'].split('-')[0], x['side'], x['open_px'], x['close_px'], x['opened']) for x in real if 'SWAP' in x.get('inst', '')]),
    'POOL-H': run('POOL-H', [p for p in pool if p[4] < F0]),
    'POOL-F': run('POOL-F', [p for p in pool if p[4] >= F0]),
}
json.dump(O, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
