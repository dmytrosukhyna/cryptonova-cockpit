"""Funding carry (delta-neutral): long spot + short perp on coins with persistently positive funding; earn funding every 8 h.
Selection uses only funding already settled (trailing average); P&L = next settled funding − switching costs.
Capital = notional × 1.2 (spot leg + perp margin at 5×). Basis moves are ignored (spot data not in the set) — see notes.
Usage: python scripts/funding_carry_test.py"""
import sys, collections, datetime as dt, numpy as np
sys.path.insert(0, 'scripts')
from ap_data import load
syms, times, A = load(); FR = A['funding_rate_pct'] / 100       # fraction per 8 h
START = int(np.searchsorted(times, 1672531200000))
hrs = np.array([(t // 60000) % (24 * 60) for t in times])
B = [i for i in range(START, len(times)) if hrs[i] % 480 == 0]   # 8-hour boundaries (00/08/16 UTC)
R = np.array([[FR[k, b - 1] if b > 0 else np.nan for b in B] for k in range(len(syms))])   # rate settled at boundary b (window before it)
DAYK = [dt.datetime.utcfromtimestamp(times[b] / 1000) for b in B]
def run(thr, look, topn, cost, exit_thr=0.0):
    held = set(); eq = 1.0; mon = collections.defaultdict(float); curve = [1.0]
    for j in range(look, len(B) - 1):
        avg = np.nanmean(R[:, j - look + 1:j + 1], 1)
        keep = {k for k in held if np.isfinite(avg[k]) and avg[k] > exit_thr}          # hysteresis: hold while funding stays positive
        for k in np.argsort(-np.nan_to_num(avg, nan=-9)):
            if len(keep) >= topn: break
            if np.isfinite(avg[k]) and avg[k] > thr: keep.add(int(k))
        new = keep; sw = len(new ^ held); held = new
        w = 1.0 / topn / 1.2                        # capital share per coin
        nxt = np.nan_to_num(R[list(held), j + 1]) if held else np.array([0.0])
        r = w * nxt.sum() - w * sw * cost / 2 * 1.0   # half the round trip on each entry or exit
        eq *= 1 + r; curve.append(eq); mon[DAYK[j + 1].strftime('%Y-%m')] += r
    c = np.array(curve); dd = ((c - np.maximum.accumulate(c)) / np.maximum.accumulate(c)).min()
    m = np.array(list(mon.values())); yrs = len(c) / 3 / 365.25
    y = collections.defaultdict(float)
    for k, v in mon.items(): y[k[:4]] += v
    return eq ** (1 / yrs) - 1, dd, (m > 0).mean(), m.min(), {k: round(v * 100, 1) for k, v in sorted(y.items())}
print("thr/8h  look  top  cost  →  APR    maxDD   months+  worst month  years%")
for cost in (0.0, 0.003, 0.0015):
    for thr in (0.0001, 0.0002, 0.0003):
        for look in (9, 21):
            for topn in (3, 6, 10):
                a, dd, wm, wmin, ys = run(thr, look, topn, cost)
                print(f"{thr*100:.3f}% {look:4d} {topn:4d} {cost*100:.2f}% → {a*100:5.1f}% {dd*100:6.2f}% {wm*100:5.0f}%   {wmin*100:+.2f}%  {ys}", flush=True)
