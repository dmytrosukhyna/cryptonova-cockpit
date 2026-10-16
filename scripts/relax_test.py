"""More trades from the rules that already work: relaxed F (OI flush) and E (panic candle) thresholds.
Per trade, per year (costs included); then portfolio «норма». Usage: python scripts/relax_test.py"""
import sys, collections, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as ap
DAYS = (ap.T - ap.START) / 96
def yrs(cs):
    y = collections.defaultdict(list)
    for c in cs: y[ap.DAY[c['i']][:4]].append(c['ret'])
    return {k: round(np.mean(v) * 100, 2) for k, v in sorted(y.items())}
def line(nm, cs):
    a = np.array([c['ret'] for c in cs]); ys = yrs(cs)
    print(f"{nm:40} {len(a)/DAYS:5.2f}/day avg {a.mean()*100:+.2f}% WR {(a>0).mean()*100:3.0f}% {ys} {'✅' if all(v>0 for v in ys.values()) else ''}", flush=True)
for oi in (-0.12, -0.10, -0.08, -0.06):
    for px in (-0.05, -0.04, -0.03):
        line(f"F oi≤{oi} px≤{px}", ap.cand_F(oi=oi, px=px))
# E: drop the market-breadth requirement / loosen it
src = open('scripts/autopilot_v2.py').read().split('def cand_E')[1].split('def cand_F')[0]
for br in (0.7, 0.5, 0.3, 0.0):
    for drop, vol in ((-0.015, 2), (-0.02, 3), (-0.03, 3)):
        code = 'def cand_E' + src.replace('(BR >= 0.7)', f'(BR >= {br})').replace('<= -0.015', f'<= {drop}').replace('V[k] >= 2 * vav', f'V[k] >= {vol} * vav')
        ns = dict(ap.__dict__); exec(code, ns); line(f"E br≥{br} drop≤{drop} vol≥{vol}x", ns['cand_E']())
