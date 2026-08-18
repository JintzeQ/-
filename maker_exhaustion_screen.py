import os, math
import numpy as np
import pandas as pd

SETS={
 'maker_live_v6_output':['WOOUSDT','CHZUSDT','GALAUSDT'],
 'maker_live_v7_output':['BBUSDT','ROBOUSDT','RAREUSDT'],
 'maker_live_v9_output':['FETUSDT','OPUSDT','WIFUSDT'],
}
OUT='maker_exhaustion_output'; os.makedirs(OUT,exist_ok=True)
ORDER_USD=100.; MAKER_FEE=2.; STEP=250; TTL=3000


def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]

def flow(t,a,b):
    x=t[(t.ts>a)&(t.ts<=b)]
    if len(x)==0:return 0.,0
    n=(x.price*x.qty).to_numpy(); sg=np.where(x.buyer_maker.to_numpy(),-n,n)
    return float(sg.sum()/(n.sum()+1e-12)),len(x)

def fill(t,start,end,side,px,queue,own):
    x=t[(t.ts>=start)&(t.ts<=end)]; need=float(queue)+float(own); done=0.
    for r in x.itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None

def mark(b,ft,side,px,h):
    r=at(b,ft+h)
    if r is None:return np.nan
    return ((float(r.mid)-px)/px*1e4) if side=='buy' else ((px-float(r.mid))/px*1e4)

def screen(folder,s,lat,profile):
    b=pd.read_csv(f'{folder}/{s}_book.csv').sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
    t=pd.read_csv(f'{folder}/{s}_trades.csv').sort_values('ts').reset_index(drop=True)
    if len(b)<3 or len(t)<3:return pd.DataFrame(),0
    if 'mid' not in b:b['mid']=(b.bid+b.ask)/2
    if 'spread_bp' not in b:b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
    start=max(int(b.ts.min())+1500,int(t.ts.min())+1500); end=min(int(b.ts.max()),int(t.ts.max()))
    out=[]; attempts=0; free=start
    for sig in np.arange(((start+STEP-1)//STEP)*STEP,end,STEP,dtype=np.int64):
        if sig<free:continue
        r=at(b,sig); p=at(b,sig-250)
        if r is None or p is None or r.spread_bp<4.25:continue
        # prior 1s burst excluding the final 250ms, then current 250ms flow state
        fprev,nprev=flow(t,sig-1250,sig-250); fnow,nnow=flow(t,sig-250,sig)
        if nprev<2:continue
        side=None
        if fprev<=-0.70 and fnow>=-0.10 and r.mid>=p.mid: side='buy'
        elif fprev>=0.70 and fnow<=0.10 and r.mid<=p.mid: side='sell'
        if side is None:continue
        if profile=='refill':
            if side=='buy' and not (r.bidq>=1.2*p.bidq):continue
            if side=='sell' and not (r.askq>=1.2*p.askq):continue
        elif profile=='flip':
            if side=='buy' and fnow<0.20:continue
            if side=='sell' and fnow>-0.20:continue
        place=sig+lat; q=at(b,place)
        if q is None:continue
        px=float(q.bid if side=='buy' else q.ask); qa=float(q.bidq if side=='buy' else q.askq); own=ORDER_USD/px; attempts+=1
        ft=fill(t,place,min(place+TTL,end),side,px,qa,own)
        if ft is None:
            free=sig+1000;continue
        out.append({'symbol':s,'capture':folder,'lat_ms':lat,'profile':profile,'side':side,'signal_ts':sig,'fill_ts':ft,
                    'entry_px':px,'spread_bp':float(q.spread_bp),'flow_prev':fprev,'flow_now':fnow,
                    'value1_bp':mark(b,ft,side,px,1000)-MAKER_FEE,'value5_bp':mark(b,ft,side,px,5000)-MAKER_FEE,
                    'value15_bp':mark(b,ft,side,px,15000)-MAKER_FEE})
        free=ft+5000
    return pd.DataFrame(out),attempts

rows=[]; fills=[]
for folder,syms in SETS.items():
  for s in syms:
    for lat in [10,240]:
      for profile in ['halt','refill','flip']:
        d,att=screen(folder,s,lat,profile)
        if len(d):
            fills.append(d)
            rows.append({'symbol':s,'capture':folder,'lat_ms':lat,'profile':profile,'attempts':att,'fills':len(d),'fill_rate':len(d)/max(att,1),
                         'value1_bp':d.value1_bp.mean(),'value5_bp':d.value5_bp.mean(),'value15_bp':d.value15_bp.mean(),
                         'positive5_rate':(d.value5_bp>=0).mean()})
        else:
            rows.append({'symbol':s,'capture':folder,'lat_ms':lat,'profile':profile,'attempts':att,'fills':0,'fill_rate':0.,'value1_bp':np.nan,'value5_bp':np.nan,'value15_bp':np.nan,'positive5_rate':np.nan})
R=pd.DataFrame(rows);F=pd.concat(fills,ignore_index=True) if fills else pd.DataFrame();R.to_csv(f'{OUT}/summary.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
valid=R[R.fills>=2].sort_values(['value5_bp','fills'],ascending=[False,False]);pos=valid[valid.value5_bp>=0]
lines=['# Sweep-exhaustion maker exploratory screen','',
'- Uses three previously untouched-to-this-rule prospective captures: v6 WOO/CHZ/GALA, v7 BB/ROBO/RARE, v9 FET/OP/WIF.','- Signal after an extreme prior 1s aggressor burst (|flow|>=0.70): final 250ms flow must stop being toxic and mid must stop extending adversely.','- `refill` additionally requires >=20% same-side displayed queue replenishment; `flip` requires current 250ms flow to flip direction.','- $100 maker quote; full displayed L1 queue + own size must trade through within 3s; no cancellation credit.','- Reported value is future side-adjusted mid markout minus 2bp maker entry fee. This is exploratory rule discovery, not OOS proof.','', '## Positive 5s value configs (>=2 fills)','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
lines+=['','## Best regardless of sign','']
if len(valid):lines.append(valid.head(20).to_markdown(index=False,floatfmt='.3f'))
else:lines.append('No config had at least 2 modeled fills.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
