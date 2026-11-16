"""П. 180 (backlog): понедельничная продажа ядра при −10% от входа против удержания. Данные: release lab-data-spot1d/spot1d.npz.
Usage: python scripts/core_monday180_study.py spot1d.npz"""
import numpy as np, datetime as dt, json, sys
z=np.load(sys.argv[1]); syms=list(z['syms']); days=z['days']; C=z['close']
COST=0.002  # sell+rebuy, 0.1% each
D=[dt.datetime.fromtimestamp(x/1000,dt.UTC) for x in days]
mon=[i for i,d in enumerate(D) if d.weekday()==0]
mid=len(days)//2
out={}
for s in ('BTC','ETH','SOL'):
    p=C[syms.index(s)].astype(float)
    sma=np.array([np.nanmean(p[max(0,i-199):i+1]) if i>=199 else np.nan for i in range(len(p))])
    # A) single path simulation: hold; Monday & p<=0.9*entry -> sell, 14d lock, rebuy at day+14 close (entry reset)
    def sim(hold):
        eq=1.0; entry=p[mon[0]]; i=mon[0]; cash_until=-1; ev=[]
        units=1.0/p[i]; cash=0.0
        for j in range(i+1,len(p)):
            if cash_until>=0 and j==cash_until:
                units=cash*(1-COST/2)/p[j]; cash=0; entry=p[j]; cash_until=-1
            if hold or cash_until>=0: continue
            if D[j].weekday()==0 and p[j]<=0.9*entry and j+14<len(p):
                cash=units*p[j]*(1-COST/2); units=0; cash_until=j+14; ev.append(j)
        v=cash+units*p[-1]
        return v/(1/p[mon[0]]*p[mon[0]]), ev
    vh,_=sim(True); vs,ev=sim(False)
    # B) staggered entries: every Monday start; first trigger Monday after; forward returns
    rows=[]
    for k,s0 in enumerate(mon):
        e=p[s0]
        for t in mon[k+1:]:
            if p[t]<=0.9*e:
                if t+30<len(p): rows.append((t, p[t+14]/p[t]-1, p[t+30]/p[t]-1, (bool(p[t]<sma[t]) if not np.isnan(sma[t]) else None), min(p[t:t+31])/p[t]-1))
                break
    # unique trigger days (many starts map to same t) -> dedupe by t
    u={}
    for r in rows: u.setdefault(r[0],r)
    R=list(u.values())
    def stat(rs):
        if not rs: return None
        a14=np.array([r[1] for r in rs]); a30=np.array([r[2] for r in rs])
        # selling advantage: 14d = -r14 - cost ; 30d (rebuy at d14) = -r14 - cost as well
        adv=-a14-COST
        return dict(n=len(rs), hold14=round(a14.mean()*100,2), hold30=round(a30.mean()*100,2), sell_adv=round(adv.mean()*100,2),
                    sell_helps=round((adv>0).mean()*100,1), sell_adv30=round((-a30-COST).mean()*100,2), helps30=round(((-a30-COST)>0).mean()*100,1), worst14=round(a14.min()*100,1), best14=round(a14.max()*100,1),
                    t=round(adv.mean()/(adv.std(ddof=1)/np.sqrt(len(adv))),2) if len(adv)>2 else None)
    out[s]=dict(path_hold=round(vh,3), path_rule=round(vs,3), path_events=len(ev),
                path_dates=[D[j].strftime('%Y-%m-%d') for j in ev],
                all=stat(R), h1=stat([r for r in R if r[0]<mid]), h2=stat([r for r in R if r[0]>=mid]),
                bear=stat([r for r in R if r[3] is True]), bull=stat([r for r in R if r[3] is False]))
print('split', D[mid].date(), 'range', D[0].date(), D[-1].date())
print(json.dumps(out,indent=1,ensure_ascii=False))
