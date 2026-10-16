"""Exit management for the Autopilot rules (F open-interest flush, E panic climax, B cascade) on 18 coins 2023-2026:
time exit vs take-profit, break-even after a move, trailing stop, tighter stops. Same entries, only exits change.
Prints trades, win rate, average % per trade and per-year average so robustness is visible."""
import sys, collections, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as A
O, H, L, C = A.O, A.H, A.L, A.C; T = A.T; COST = A.COST


def exit_path(k, i0, hold, stop_pct, tp=None, be_at=None, trail=None):
    e = O[k, i0]; stop = e * (1 - stop_pct); peak = e; be = False
    for j in range(i0, min(T - 1, i0 + hold) + 1):
        if not np.isfinite(L[k, j]): continue
        if L[k, j] <= stop: return stop / e - 1
        if tp and H[k, j] >= e * (1 + tp): return tp
        peak = max(peak, H[k, j])
        if be_at and not be and peak >= e * (1 + be_at): stop = max(stop, e * 1.001); be = True
        if trail and peak >= e * (1 + trail[0]): stop = max(stop, peak * (1 - trail[1]))
    j = min(T - 1, i0 + hold)
    while not np.isfinite(C[k, j]) and j > i0: j -= 1
    return C[k, j] / e - 1


def entries(rule):
    return {'F': A.cand_F(), 'E': A.cand_E(), 'B': A.cand_B()}[rule]


VARIANTS = [("база: 24ч, стоп −12%", dict(hold=96, stop_pct=0.12)),
            ("стоп −6%", dict(hold=96, stop_pct=0.06)),
            ("стоп −4%", dict(hold=96, stop_pct=0.04)),
            ("тейк +2%", dict(hold=96, stop_pct=0.12, tp=0.02)),
            ("тейк +3%", dict(hold=96, stop_pct=0.12, tp=0.03)),
            ("тейк +5%", dict(hold=96, stop_pct=0.12, tp=0.05)),
            ("безубыток после +1.5%", dict(hold=96, stop_pct=0.12, be_at=0.015)),
            ("безубыток после +3%", dict(hold=96, stop_pct=0.12, be_at=0.03)),
            ("трейлинг 2%/1.5%", dict(hold=96, stop_pct=0.12, trail=(0.02, 0.015))),
            ("трейлинг 3%/2.5%", dict(hold=192, stop_pct=0.12, trail=(0.03, 0.025))),
            ("тейк +3%, стоп −6%", dict(hold=96, stop_pct=0.06, tp=0.03)),
            ("тейк +1.5%, стоп −12% (высокий винрейт)", dict(hold=96, stop_pct=0.12, tp=0.015))]

for rule in ('F', 'E'):
    ev = entries(rule)
    print(f"\n=== {rule}: {len(ev)} входов ===")
    for nm, kw in VARIANTS:
        r = np.array([exit_path(c['k'], c['i'], **kw) - COST for c in ev]) * 100
        y = collections.defaultdict(list)
        for c, v in zip(ev, r): y[A.DAY[c['i']][:4]].append(v)
        ys = ' '.join(f"{k}:{np.mean(v):+.2f}" for k, v in sorted(y.items()))
        print(f"{nm:42} WR={(r > 0).mean() * 100:4.0f}%  avg={r.mean():+.3f}%  sum={r.sum():+7.0f}%  worst={r.min():+.1f}%  | {ys}", flush=True)
