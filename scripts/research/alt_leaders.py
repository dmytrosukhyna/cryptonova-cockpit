# extra look (2 variants, labelled as such): in BTC-below-SMA200 months, at each month start, liquid alts (vol30>=$20M)
# split into 'leaders' (365d return > BTC 365d return) vs 'laggards' (365d high drawdown >=70%); hold 180d, equal weight, cost 0.3%
import numpy as np,sys
exec(open('scripts/research/alt_bottom.py').read().split("res={}")[0])
L=[];G=[];B=[];mo=[]
for t in range(400,T-181,30):
    b=C[bi,t-200:t+1]
    if not (C[bi,t]<np.nanmean(b)): continue
    br=C[bi,t]/C[bi,t-365]-1; lead=[];lag=[]
    for i,s in enumerate(syms):
        if s in EXC or not np.isfinite(C[i,t]) or not np.isfinite(C[i,t-365]) or vol30(i,t)<20e6: continue
        q=fwd(i,t,180)
        if not q: continue
        if C[i,t]/C[i,t-365]-1>br: lead.append(q[1])
        if C[i,t]<=np.nanmax(C[i,t-365:t+1])*0.3: lag.append(q[1])
    if lead and lag:
        L.append(np.mean(lead)-COST);G.append(np.mean(lag)-COST);B.append(fwd(bi,t,180)[0]-COST);mo.append((D0[t].isoformat(),len(lead),len(lag)))
L,G,B=map(np.array,(L,G,B))
print('месяцев',len(L)); print(f'лидеры ср {L.mean()*100:+.0f}% мед {np.median(L)*100:+.0f}% | на дне ср {G.mean()*100:+.0f}% мед {np.median(G)*100:+.0f}% | BTC ср {B.mean()*100:+.0f}%')
d=L-G; print(f'лидеры минус дно: ср {d.mean()*100:+.0f}% t {d.mean()/(d.std(ddof=1)/np.sqrt(len(d))):+.1f} (месяцы перекрываются по 180 д — t завышен) лучше в {(d>0).mean()*100:.0f}%')
for m,l,g,b,x in zip(mo,L,G,B,d): print(m, f'{l*100:+.0f} {g*100:+.0f} btc{b*100:+.0f}')
