# П. 213 — половинный риск после 2 минусов пары кит+монета (19.11.2026, глубокое исследование 21:00 Афин)

**Вопрос.** Вместо паузы (п. 211 не прошёл) — копировать пару «кит + монета» с риском 1,5% вместо 3% в течение 24 ч после того, как 2 её сделки закрылись в минусе в пределах 24 ч.

**Порог (заранее, из backlog):** дневная просадка лучше, доход хуже не больше чем на 10%, в обеих половинах; только демо.

## Данные и метод
- data/whale_trades/*.jsonl 04–19.11: 27 010 закрытых сделок 240 трейдеров (bn 3 988, okx 22 078), дубли по (трейдер, id, монета) убраны.
- Доход копии = сторона × (выход/вход − 1), снизу −16% (защитный стоп, по цене закрытия), минус 0,10% издержек. Задержка +2 мин не моделируется (нет внутридневных цен) — оговорка, как в п. 211.
- Пометка без заглядывания вперёд: сделка открыта в течение 24 ч после закрытия 2-го минуса пары (оба минуса закрыты в пределах 24 ч). Помечено 6 974 сделки.
- Равный вес: дневной итог = сумма (вес × доход) / среднее число сделок в день; вес 1 или 0,5. Половины — по медианной дате закрытия.
- Вариантов: 1 правило × 3 выборки (все / Binance / OKX). Скрипт — в конце файла.

## Результат
| выборка | помечено | средняя помеченных / всех | доход: 3% → половинный | просадка (макс.) | худший день |
|---|---|---|---|---|---|
| все, весь период | 6 974 | +1,01% / +1,35% | +167,6 → +151,4 (**−9,6%**) | −2,9 → −3,0 | −2,88 → −2,97 |
| все, 1-я половина | | | +74,5 → +65,5 (**−12,1%**) | −1,0 → −0,5 | −1,00 → −0,47 |
| все, 2-я половина | | | +84,9 → +76,9 (−9,4%) | −1,6 → −1,7 | −1,61 → −1,66 |
| Binance | 194 | +0,68% / +1,44% | −1,1% дохода | −1,0 → −1,1 | без улучшения |
| OKX | 6 588 | +1,04% / +1,35% | −11,5% (−12,2% / −11,4% по половинам) | −4,4 → −4,5 | без улучшения |

Журнал стола «Киты» (163 закрытые копии с 19.10): правило задело бы 13 копий в 3 парах (ARB/FIL одного OKX-кита, ETH leaf-ye), итог −$15.38; половинный размер сберёг бы +$7.69 — почти всё из одного хвоста ARB −$128.4, остальные 12 копий в сумме +$113. Серий 3 — мало.

## Вывод
**Не прошло.** Просадка лучше только в 1-й половине; на всём периоде и во 2-й половине — чуть хуже (минусы приходят не после «пилы»). Доход теряется на 9–12% — у границы 10% и выше её в 1-й половине и на OKX. Сделки после двух минусов у этих трейдеров в плюсе (+1,0%), поэтому урезать их размер невыгодно. Правило не вводим ни в демо, ни в реальный счёт; хвостовой риск уже закрыт стопом −16% и правилом «5 копий подряд в минусе → в B».

Побочная идея: п. 216 — урезать размер не по счёту минусов, а по глубине: после копии пары с убытком ≤ −8% (хвост, как ARB −$128).

## Скрипт
```python
import json,glob,collections,math,datetime as dt,statistics as st
T=[];seen=set()
for f in sorted(glob.glob('data/whale_trades/*.jsonl')):
  for l in open(f):
    try:r=json.loads(l)
    except: continue
    if r.get('kind')!='closed' or not r.get('entry') or not r.get('exit'): continue
    k=(r['trader'],r['id'],r['coin'])
    if k in seen: continue
    seen.add(k)
    s=1 if r['side']=='LONG' else -1
    ret=max(s*(r['exit']/r['entry']-1),-0.16)-0.001
    T.append(dict(tr=r['trader'],src=r['src'],c=r['coin'],o=r['open_t'],cl=r['close_t'],ret=ret))
T.sort(key=lambda x:x['o'])
# flag: trades opened within 24h after close of 2nd loss of pair where both losses closed within 24h (only info closed before open)
by=collections.defaultdict(list)
for t in T: by[(t['tr'],t['c'])].append(t)
H=24*3600e3
for k,ts in by.items():
  closes=sorted([(t['cl'],t['ret']) for t in ts])
  losses=[c for c,r in closes if r<0]
  starts=[losses[i] for i in range(1,len(losses)) if losses[i]-losses[i-1]<=H]
  for t in ts:
    t['flag']=any(s<=t['o']<s+H for s in starts)
def run(sub,name):
  if not sub: return
  days=sorted(set(dt.datetime.utcfromtimestamp(t['cl']/1000).date() for t in sub))
  mid=days[len(days)//2]
  out=[]
  for half,cond in (('всё',lambda d:True),('1-я',lambda d:d<mid),('2-я',lambda d:d>=mid)):
    res={}
    for wname,wf in (('3%',1.0),('1.5% после 2 минусов',0.5)):
      daily=collections.defaultdict(float);n=collections.defaultdict(int)
      for t in sub:
        d=dt.datetime.utcfromtimestamp(t['cl']/1000).date()
        if not cond(d): continue
        w=wf if t['flag'] else 1.0
        daily[d]+=w*t['ret'];n[d]+=1
      # equal weight: mean per trade per day (portfolio of equal slots) -> use daily sum / avg trades per day
      dd=sorted(daily); avgn=st.mean(n[d] for d in dd)
      eq=0;pk=0;mdd=0;worst=0
      for d in dd:
        r=daily[d]/avgn; eq+=r; pk=max(pk,eq); mdd=min(mdd,eq-pk); worst=min(worst,r)
      res[wname]=(eq*100,mdd*100,worst*100)
    a,b=res['3%'],res['1.5% после 2 минусов']
    out.append((half,a,b,(a[0]-b[0])/abs(a[0])*100 if a[0] else 0))
  nf=sum(t['flag'] for t in sub)
  print(f"\n## {name}: сделок {len(sub)}, помечено {nf}, средняя помеченных {100*st.mean([t['ret'] for t in sub if t['flag']] or [0]):+.2f}%, всех {100*st.mean(t['ret'] for t in sub):+.2f}%")
  for half,a,b,loss in out:
    print(f"{half}: доход {a[0]:+.1f} → {b[0]:+.1f} (−{loss:.1f}%), макс. просадка {a[1]:.1f} → {b[1]:.1f}, худший день {a[2]:.2f} → {b[2]:.2f}")
run(T,'все')
run([t for t in T if t['src']=='bn'],'Binance')
run([t for t in T if t['src']=='okx'],'OKX')
```
