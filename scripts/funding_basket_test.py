"""Funding filter on F/E entries + E basket variants, portfolio level (autopilot_v2 «норма» settings).
Robustness rule: a variant is accepted only if it beats the baseline in total AND does not make any year worse by >5% of start equity."""
import sys, numpy as np
sys.path.insert(0, 'scripts')
import autopilot_v2 as ap
FR = ap.A['funding_rate_pct']
B, E, F = ap.cand_B(), ap.cand_E(), ap.cand_F()
NORM = dict(risk=0.01, max_open=4, e_share=0.2, f_share=0.25, f_open=3)

def fr_at(c):  # last known funding before entry bar (pct per 8h)
    k, i = c['k'], c['i'] - 1
    w = FR[k, max(0, i - 32):i + 1]; w = w[np.isfinite(w)]
    return w[-1] if len(w) else np.nan

for c in E + F: c['fr'] = fr_at(c)
def stats(cs):
    a = np.array([c['ret'] for c in cs]) * 100
    return f"n={len(a):4d} avg={a.mean():+.2f}% WR={(a>0).mean()*100:.0f}%" if len(a) else "n=0"
print("per-trade by funding bucket")
for nm, cs in (('E', E), ('F', F)):
    for lo, hi in ((-9, -0.01), (-0.01, 0.005), (0.005, 0.0101), (0.0101, 9)):
        print(f"  {nm} fr∈[{lo},{hi}) {stats([c for c in cs if lo <= c.get('fr', np.nan) < hi])}")
    print(f"  {nm} fr=nan {stats([c for c in cs if not np.isfinite(c['fr'])])}")

def run(cands, **kw):
    p = dict(NORM); p.update(kw)
    eq, tr, cu = ap.simulate(cands, **p); r, _ = ap.report(eq, tr, cu); return r
base = run(B + E + F)
def show(nm, r):
    dy = {y: r['years'].get(y, 0) - base['years'].get(y, 0) for y in base['years']}
    ok = r['final'] > base['final'] and min(dy.values()) > -500
    print(f"{nm:34} ${r['final']:>7,} CAGR {r['cagr']*100:+5.1f}% DD {r['maxdd']*100:5.1f}% n={r['n']:4d} Δyears={dy} {'ПРИНЯТЬ?' if ok else ''}")
show('BASE норма', base)
for th in (0.0, 0.005, 0.01):
    show(f'F only if fr<{th}', run(B + E + [c for c in F if not (c['fr'] >= th)]))
    show(f'E only if fr<{th}', run(B + F + [c for c in E if not (c['fr'] >= th)]))
# funding-negative boost: double F size when fr<0 → emulate by scaling ret
for mult in (1.5, 2.0):
    Fb = [dict(c, ret=c['ret'] * (mult if c['fr'] < 0 else 1)) for c in F]
    show(f'F x{mult} when fr<0', run(B + E + Fb))
# E basket variants: spacing between baskets, size by breadth
orig = ap.simulate
for gap in (40, 80, 160):
    src = open('scripts/autopilot_v2.py').read()
    def sim(c, gap=gap, **kw):
        import types
        code = src.split('def simulate')[1].split('def report')[0].replace('i - last_e >= 80', f'i - last_e >= {gap}')
        ns = dict(ap.__dict__); exec('def simulate' + code, ns); return ns['simulate'](c, **kw)
    eq, tr, cu = sim(B + E + F, **NORM); r, _ = ap.report(eq, tr, cu); show(f'E basket gap {gap} bars', r)
