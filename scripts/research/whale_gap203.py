"""п. 203 (18.11): разрыв стол «Киты» ↔ реальный счёт. Вход: DIR/real_closed.json — объединение public_real.json closed
из снимков git (07.11/02.11/05.11/09.11/13.11 + текущий; 117 из 117 закрытых). Запуск: DIR=... python3 scripts/research/whale_gap203.py"""
import os; DIR=os.environ.get("DIR","/tmp/claude-0/r")
import json,datetime as dt,statistics as st,math
from zoneinfo import ZoneInfo
A=ZoneInfo('Europe/Athens'); F=lambda t: dt.datetime.fromtimestamp(t/1000,A).strftime('%d.%m %H:%M')
R=json.load(open(DIR+'/real_closed.json'))
L=json.load(open('web/whales_live.json'))['desk']
D=L['closed']; O=L['open']
print('desk closed',len(D),F(min(x['opened_t'] for x in D)),'start',L['start'],'equity',L['equity'],'started',F(L['started']))
print('desk sum pnl',round(sum(x['pnl'] for x in D),1))
for x in R: x['coin']=x['inst'].split('-')[0]; x['side']=x['side'].upper()
R.sort(key=lambda x:x['opened'])
# match
used=set()
for r in R:
    best=None
    for i,d in enumerate(D+O):
        if i in used or d['coin']!=r['coin'] or d['side']!=r['side']: continue
        dtm=(r['opened']-d['opened_t'])/60000
        if -5<=dtm<=24*60 and (best is None or abs(dtm)<abs(best[1])): best=(i,dtm)
    r['m']=best
    if best: used.add(best[0])
def per(lbl,rs):
    print(lbl,len(rs),round(sum(x['pnl_usd'] for x in rs),2))
cut=1794430000000  # 06.11 ~ 20:00 Athens
print('cut',F(cut))
per('real before cut',[r for r in R if r['opened']<cut])
per('real before cut matched desk',[r for r in R if r['opened']<cut and r['m']])
per('real after cut',[r for r in R if r['opened']>=cut])
per('real after cut matched',[r for r in R if r['opened']>=cut and r['m']])
per('real after cut unmatched',[r for r in R if r['opened']>=cut and not r['m']])
for r in R:
    if r['opened']>=cut and not r['m']: print('  U',r['coin'],r['side'],F(r['opened']),F(r['closed']),r['pnl_usd'])
json.dump(R,open(DIR+'/R.json','w'))
import json,datetime as dt,statistics as st,math
from zoneinfo import ZoneInfo
A=ZoneInfo('Europe/Athens'); F=lambda t: dt.datetime.fromtimestamp(t/1000,A).strftime('%d.%m %H:%M')
R=json.load(open(DIR+'/R.json'))
L=json.load(open('web/whales_live.json'))['desk']; D=L['closed']; O=L['open']; DO=D+O
cut=int(dt.datetime(2026,11,6,20,30,tzinfo=A).timestamp()*1000)
Ra=[r for r in R if r['opened']>=cut]; Rb=[r for r in R if r['opened']<cut]
print('real before 06.11 20:30',len(Rb),round(sum(r['pnl_usd'] for r in Rb),2),' after',len(Ra),round(sum(r['pnl_usd'] for r in Ra),2))
# by month-ish windows of real
for a,b in [('22.10','06.11'),]: pass
Da=[d for d in D if d['opened_t']>=cut]; Db=[d for d in D if d['opened_t']<cut]
print('desk closed before',len(Db),round(sum(d['pnl'] for d in Db),1),' after',len(Da),round(sum(d['pnl'] for d in Da),1))
mi={}
for r in Ra:
    if r['m']: mi[r['m'][0]]=r
ratios=[];rows=[]
for i,d in enumerate(DO):
    if d['opened_t']<cut: continue
    r=mi.get(i)
    closed='closed_t' in d
    if r:
        mv=r['move_pct']; rn=abs(r['pnl_usd']/(mv/100)) if abs(mv)>0.3 else None
        if rn: ratios.append(rn/d['notional'])
    rows.append((d,r,closed))
k=st.median(ratios); print('size ratio real/desk median',round(k,3),'IQR',[round(x,3) for x in st.quantiles(ratios,n=4)],len(ratios))
# decomposition on closed desk trades after cut
tot_desk=0;miss=0;matched_desk=0;matched_real=0;mis=[];ivan=[0,0,0]
entry_gap=[];exit_gap=[]
for d,r,closed in rows:
    if not closed: continue
    dp=d['pnl']*k; tot_desk+=dp
    if r and r['closed']:
        matched_desk+=dp; matched_real+=r['pnl_usd']
        s=1 if d['side']=='LONG' else -1
        entry_gap.append(s*(r['open_px']/d['entry']-1)*100)  # + = worse for us (paid more on long)
        exit_gap.append(-s*(r['close_px']/d['exit']-1)*100)  # + = worse
    else:
        miss+=dp; mis.append((d,dp))
print(f'desk closed after cut scaled ${tot_desk:.1f}; matched desk ${matched_desk:.1f} vs real ${matched_real:.1f}; missed desk ${miss:.1f} ({len(mis)})')
print('entry gap % median',round(st.median(entry_gap),3),'mean',round(st.mean(entry_gap),3),'exit gap mean',round(st.mean(exit_gap),3),'n',len(entry_gap))
for d,dp in sorted(mis,key=lambda x:x[1]):
    print('  M',d['coin'],d['side'],F(d['opened_t']),d['whale'][:14],'join' if d.get('join') else '',round(dp,1),d.get('why'))
# real trades with no desk match (after cut)
for r in Ra:
    if not r['m']: print(' unmatched real',r['coin'],r['side'],F(r['opened']),r['pnl_usd'])
# what explains matched gap: per trade diff
diffs=[]
for d,r,closed in rows:
    if closed and r and r['closed']:
        diffs.append((r['pnl_usd']-d['pnl']*k,d,r))
diffs.sort(key=lambda x:x[0])
for df,d,r in diffs[:8]+diffs[-4:]:
    print('  D',round(df,1),d['coin'],d['side'],F(d['opened_t']),'desk',round(d['pnl']*k,1),'real',r['pnl_usd'],'exit',d['exit'],r['close_px'],'closeT',F(d['closed_t']),F(r['closed']),d.get('why'))
print('--- residual')
DOn=len(D)
g={}
for r in Ra:
    if not r['m']: key='no desk'
    else:
        i=r['m'][0]; d=DO[i]
        key='desk still open' if i>=DOn else ('desk opened before cut' if d['opened_t']<cut else 'pair')
    g.setdefault(key,[]).append(r)
for k_,v in g.items(): print(k_,len(v),round(sum(x['pnl_usd'] for x in v),2))
for r in g.get('desk still open',[])+g.get('desk opened before cut',[]):
    i=r['m'][0]; d=DO[i]; print('  ',r['coin'],r['side'],F(r['opened']),F(r['closed']),r['pnl_usd'],'desk opened',F(d['opened_t']),'desk closed',F(d['closed_t']) if 'closed_t' in d else 'OPEN', d.get('why'))
# before cut: by source? real before cut rows matched to desk?
mb=[r for r in Rb if r['m']]; print('before cut real matched to whale desk',len(mb),round(sum(r['pnl_usd'] for r in mb),2),'unmatched',len(Rb)-len(mb),round(sum(r['pnl_usd'] for r in Rb if not r['m']),2))
