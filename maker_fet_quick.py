import os, math
import numpy as np
import pandas as pd
import maker_historical_v5 as m

OUT='maker_fet_quick_output'; os.makedirs(OUT,exist_ok=True)
S='FETUSDT'; D='2024-03-29'
print('loading FET book/trades',flush=True)
b,t=m.read_book(S,D),m.read_trades(S,D)
start=max(int(b.ts.min()),int(t.ts.min())); end=min(int(b.ts.max()),int(t.ts.max())); split=start+(end-start)//2
b1=b[b.ts<=split].reset_index(drop=True); t1=t[t.ts<=split].reset_index(drop=True)
b2=b[b.ts>split].reset_index(drop=True); t2=t[t.ts>split].reset_index(drop=True)
train=[]
for i,c in enumerate(m.CONFIGS):
    d=m.simulate(b1,t1,c,front=False); st=m.stat(d); train.append((i,c,st))
rank=sorted([x for x in train if x[2]['n']>=5],key=lambda x:(x[2]['net'],x[2]['n']),reverse=True)[:8]
rows=[]
for i,c,tr in rank:
    d=m.simulate(b2,t2,c,front=False); so=m.stat(d)
    rows.append({'rid':i,**c,'train_n':tr['n'],'train_net_bp':tr['net'],'train_mm':tr['mm'],'oos_n':so['n'],'oos_net_bp':so['net'],'oos_gross_bp':so['gross'],'oos_mm':so['mm'],'oos_markout_bp':so['mark'],'oos_ci_low':so['ci'],'qmodel':'displayed'})
for i,c,tr in rank[:3]:
    d=m.simulate(b2,t2,c,front=True); so=m.stat(d)
    rows.append({'rid':i,**c,'train_n':tr['n'],'train_net_bp':tr['net'],'train_mm':tr['mm'],'oos_n':so['n'],'oos_net_bp':so['net'],'oos_gross_bp':so['gross'],'oos_mm':so['mm'],'oos_markout_bp':so['mark'],'oos_ci_low':so['ci'],'qmodel':'front'})
R=pd.DataFrame(rows); R.to_csv(f'{OUT}/results.csv',index=False)
mid=(b.best_bid_price+b.best_ask_price)/2; spr=(b.best_ask_price-b.best_bid_price)/mid*1e4
cons=R[R.qmodel=='displayed'].sort_values(['oos_net_bp','oos_n'],ascending=[False,False]); pos=cons[(cons.oos_n>=5)&(cons.oos_net_bp>=0)]
front=R[R.qmodel=='front'].sort_values('oos_net_bp',ascending=False)
lines=['# FETUSDT maker quick OOS','', '- Data: 2024-03-29 bookTicker + aggTrades.','- First half-day train, second half-day OOS.','- $100 size; 2s decision; 20s entry TTL; 60s maker-exit TTL.','- Displayed queue model: full L1 queue × qmult + own order must trade through; no cancellation credit.','- Current fee assumption: maker/maker 4bp; forced maker/taker 7bp.','',f'- Median historical spread: {spr.median():.3f} bp; p75: {spr.quantile(.75):.3f} bp.','',f'## Conservative PASS count: {len(pos)}','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
lines+=['','## Best conservative','']
if len(cons):lines.append(cons.head(8).to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
lines+=['','## Front-of-queue upper bound','']
if len(front):lines.append(front.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines))
