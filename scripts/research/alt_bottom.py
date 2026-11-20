# Pre-registered 21.11.2026 00:20 Athens, before any run:
# Q (owner 21.11): buy liquid alts "at the bottom", no stop, hold — they "will regrow"?
# signal: liquid alt (30d avg quote vol >= $20M, >= 365d history) closes >= D below its 365d high; episode = first such day
#   after >= 60 days without; D in {0.70,0.80,0.90}; entry next close; hold H in {180,365} days, no stop
# entry modes: 'once' (all at entry) / 'ladder' (1/3 at entry, 1/3 at -15%, 1/3 at -30% within 60d; unfilled = cash 0%)
# delisted before horizon: two cases, last close (optimistic) and -100% (pessimistic)
# benchmarks: BTC same day same horizon; null = random liquid alt same day (all alts passing liquidity), same horizon
# costs 0.3% per round; halves: signal < 2023-07-01 vs >=; t clustered by calendar month
import numpy as np, sys, datetime as dt
z=np.load(sys.argv[1]); syms=[str(s) for s in z['syms']]; days=z['days']; C=z['close'].astype(float); V=z['qvol'].astype(float)
if days[0]>1e11: days=days//1000
D0=[dt.datetime.utcfromtimestamp(int(d)).date() if d>1e6 else dt.date(1970,1,1)+dt.timedelta(int(d)) for d in days]
print('days',D0[0],D0[-1],len(D0))
EXC={'BTC','ETH','SOL','USDC','FDUSD','TUSD','BUSD','USDP','DAI','EUR','WBTC','WBETH','PAXG','USD1','AEUR','EURI','XUSD','BFUSD','USDE','RLUSD','UST','SUSD','GBP','AUD','PAX','USDS','USDSB','BKRW','IDRT','BIDR'}
# data hygiene (added before first trusted run, after a glitch run showed null +2625%): a one-day jump > x3 is a
# redenomination/data break -> the series is cut there (NaN after)
R=C[:,1:]/C[:,:-1]
for i in range(len(syms)):
    br=np.where(np.isfinite(R[i])&(R[i]>3))[0]
    if len(br): C[i,br[0]+1:]=np.nan
# relisted symbols (LUNA 2.0 after LUNA): the series ends at its first gap after valid data
for i in range(len(syms)):
    ok=np.where(np.isfinite(C[i]))[0]
    if len(ok):
        gap=np.where(~np.isfinite(C[i,ok[0]:]))[0]
        if len(gap): C[i,ok[0]+gap[0]:]=np.nan
bi=syms.index('BTC'); T=len(D0); half=next(i for i,d in enumerate(D0) if d>=dt.date(2023,7,1))
COST=0.003
def vol30(i,t): v=V[i,max(0,t-30):t]; return np.nanmean(v) if np.isfinite(v).any() else 0
def fwd(i,t,H):
    e=C[i,t]; end=min(t+H,T-1)
    if t+H>T-1: return None
    s=C[i,t:t+H+1]; ok=np.isfinite(s)
    if ok[-1]: return s[-1]/e-1, s[-1]/e-1, False
    last=s[ok][-1]; return last/e-1, -1.0, True
res={}
for Dd in (0.70,0.80,0.90):
  ev=[]
  for i,s in enumerate(syms):
    if s in EXC or s.endswith('UP') or s.endswith('DOWN'): continue
    last=-999
    for t in range(365,T-1):
      c=C[i,t]
      if not np.isfinite(c): continue
      h=C[i,t-365:t+1]
      if np.isfinite(h).sum()<300: continue
      hh=np.nanmax(h)
      if c<=hh*(1-Dd):
        if t-last>=60 and vol30(i,t)>=20e6: ev.append((i,t+1))
        last=t
  res[Dd]=ev
def lad(i,t,H):
    e=C[i,t]; r=fwd(i,t,H)
    if r is None: return None
    a,b,dl=r; seg=C[i,t:min(t+61,T)]; parts=[(a,b)]
    for k in (0.85,0.70):
        hit=np.where(np.isfinite(seg)&(seg<=e*k))[0]
        if len(hit): 
            px=e*k; tt=t+hit[0]; s=C[i,tt:t+H+1]; ok=np.isfinite(s)
            if ok[-1]: parts.append((s[-1]/px-1,s[-1]/px-1))
            else: parts.append((s[ok][-1]/px-1,-1.0))
        else: parts.append((0.0,0.0))
    return np.mean([p[0] for p in parts]), np.mean([p[1] for p in parts]), dl
def stats(x,months):
    x=np.array(x); m=np.array(months)
    um=np.unique(m); mm=np.array([x[m==u].mean() for u in um])
    t=mm.mean()/(mm.std(ddof=1)/np.sqrt(len(mm))) if len(mm)>2 else np.nan
    return len(x),x.mean()*100,np.median(x)*100,(x>0).mean()*100,t,len(um)
for Dd,ev in res.items():
  for H in (180,365):
    for mode in ('once','ladder'):
      rows=[]
      for i,t in ev:
        r=fwd(i,t,H) if mode=='once' else lad(i,t,H)
        if r is None: continue
        b=fwd(bi,t,H)
        # null: all liquid alts that day
        nl=[]
        for j,s in enumerate(syms):
          if s in EXC or j==i or not np.isfinite(C[j,t]) or vol30(j,t)<20e6: continue
          q=fwd(j,t,H)
          if q: nl.append(q[1])
        rows.append((r[0]-COST,r[1]-COST,b[0]-COST,np.mean(nl)-COST if nl else np.nan,r[2],t,syms[i]))
      if not rows: continue
      mo=[D0[r[5]].strftime('%Y-%m') for r in rows]
      for lab,k in (('опт',0),('пес',1)):
        n,mean,med,win,tt,nm=stats([r[k] for r in rows],mo)
        ex=[r[k]-r[2] for r in rows]; _,exm,exmed,exw,ext,_=stats(ex,mo)
        h1=[r[k] for r in rows if r[5]<half]; h2=[r[k] for r in rows if r[5]>=half]
        print(f"D{int(Dd*100)} H{H} {mode:6s} {lab}: n{n} мес{nm} ср{mean:+.0f}% мед{med:+.0f}% плюс{win:.0f}% t{tt:+.1f} | минус BTC ср{exm:+.0f}% мед{exmed:+.0f}% лучше{exw:.0f}% t{ext:+.1f} | BTC ср{np.mean([r[2] for r in rows])*100:+.0f}% null{np.nanmean([r[3] for r in rows])*100:+.0f}% | пол1 {np.mean(h1)*100 if h1 else 0:+.0f}%({len(h1)}) пол2 {np.mean(h2)*100 if h2 else 0:+.0f}%({len(h2)}) delist{sum(r[4] for r in rows)}")
      if Dd==0.80 and H==365 and mode=='once':
        print('  примеры:',sorted([(r[6],D0[r[5]].isoformat(),round(r[0]*100)) for r in rows],key=lambda x:x[1])[-25:])
