"""Robustness of the Портфолио regime switch (70 % / 20 %): SMA 100–250 × lookback none / 30 / 60 / 90 / 120 days, 2023-07 → 2026-10.
Usage: python scripts/lab/regime_robust.py <alt100_all2023.npz>. Result 18.10: every variant beats fixed 40 % (+88 %); 60 days is best or
near best for every SMA — SMA200 + 60 d: +197 %, drawdown −25 %, 2025 +15 %, 2026 +9 % (SMA200 + 90 d: +183 %, −26 %, +13 %, +3 %)."""
import sys, json, numpy as np, datetime as dt
npz = sys.argv[1] if len(sys.argv) > 1 else 'alt100_all2023.npz'
sys.argv = ['x', npz]
src=open(__import__('os').path.join(__import__('os').path.dirname(__file__), 'portfolio_rules.py')).read().split('res = {"period"')[0]
ns={}; exec(src, ns)
btc, n, rb, years, monday, t = ns['btc'], ns['n'], ns['rb'], ns['years'], ns['monday'], ns['t']
RS, COST = ns['RS'], ns['COST']
S0 = 250
def flag(L, R):
    sma = np.array([btc[max(0,i-L+1):i+1].mean() if i >= L-1 else np.nan for i in range(n)])
    ok = btc > sma
    if R:
        r = np.array([btc[i]/btc[i-R]-1 if i >= R else np.nan for i in range(n)])
        ok = ok & (r > 0)
    return ok
def sim(on, off, bull):
    vs, vc = 0.0, 10000.0; vals=[]; prev=None; flips=0
    for i in range(S0, n):
        if i > S0: vs *= 1+rb[i]; vc *= 1+RS
        T = on if bull[i] else off
        tot = vs+vc
        if prev is None or T != prev or monday[i]:
            if prev is not None and T != prev: flips += 1
            want = tot*T; cost = abs(want-vs)*COST; vs, vc = want, tot-want-cost; prev = T
        vals.append(vs+vc)
    v=np.array(vals); y=years[S0:]; pk=np.maximum.accumulate(v)
    o={'total':round((v[-1]/1e4-1)*100),'dd':round(((v/pk)-1).min()*100),'flips':flips}
    for yr in (2024,2025,2026):
        ix=np.where(y==yr)[0]; o[yr]=round((v[ix[-1]]/v[ix[0]-1]-1)*100,1)
    return o
print('period from', dt.datetime.fromtimestamp(t[S0]/1000, dt.timezone.utc).date())
print('fixed 40%', sim(0.4,0.4,np.ones(n,bool)))
rows=[]
for L in (100,150,200,250):
    for R in (None,30,60,90,120):
        r=sim(0.7,0.2,flag(L,R)); rows.append((L,R,r)); print(f'SMA{L} R{R}', r)
