"""Filters on the Autopilot rules F (OI flush) and E (panic climax), 18 coins 2023-2026: hour of day (UTC), weekend,
funding at entry, depth of the 24h drop, BTC vs alts. Only keeps a filter if it helps in every year (no curve-fitting)."""
import sys, collections, datetime as dt, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as A
FR = A.A['funding_rate_pct']; R24 = np.full_like(A.C, np.nan); R24[:, 96:] = A.C[:, 96:] / A.C[:, :-96] - 1


def show(name, cs):
    if len(cs) < 30: print(f"  {name:38} n={len(cs)} мало"); return
    r = np.array([c['ret'] for c in cs]) * 100; y = collections.defaultdict(list)
    for c in cs: y[A.DAY[c['i']][:4]].append(c['ret'] * 100)
    print(f"  {name:38} n={len(r):5d} avg={r.mean():+.2f}% WR={(r > 0).mean() * 100:3.0f}% | " + ' '.join(f"{k}:{np.mean(v):+.2f}" for k, v in sorted(y.items())), flush=True)


for rule, cs in (('F сброс позиций', A.cand_F()), ('E откуп паники', A.cand_E())):
    print(f"\n== {rule} ==")
    show("все", cs)
    hr = lambda c: dt.datetime.utcfromtimestamp(A.times[c['i']] / 1000)
    for a_, b_, nm in ((0, 8, 'Азия 00–08 UTC'), (8, 14, 'Европа 08–14'), (14, 21, 'США 14–21'), (21, 24, 'ночь 21–24')):
        show(nm, [c for c in cs if a_ <= hr(c).hour < b_])
    show("выходные", [c for c in cs if hr(c).weekday() >= 5]); show("будни", [c for c in cs if hr(c).weekday() < 5])
    fr = lambda c: FR[c['k'], c['i'] - 1]
    show("фандинг < 0 (шорты платят)", [c for c in cs if np.isfinite(fr(c)) and fr(c) < 0])
    show("фандинг ≥ 0", [c for c in cs if np.isfinite(fr(c)) and fr(c) >= 0])
    d = lambda c: R24[c['k'], c['i'] - 1]
    show("падение за 24ч ≤ −10%", [c for c in cs if np.isfinite(d(c)) and d(c) <= -0.10])
    show("падение за 24ч −10…−5%", [c for c in cs if np.isfinite(d(c)) and -0.10 < d(c) <= -0.05])
    show("падение за 24ч > −5%", [c for c in cs if np.isfinite(d(c)) and d(c) > -0.05])
    bt = A.syms.index('BTCUSDT'); et = A.syms.index('ETHUSDT')
    show("BTC и ETH", [c for c in cs if c['k'] in (bt, et)]); show("альткоины", [c for c in cs if c['k'] not in (bt, et)])
