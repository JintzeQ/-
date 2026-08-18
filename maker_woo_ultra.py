import io, os
import numpy as np
import pandas as pd
import maker_historical_v5 as m

OUT='maker_woo_ultra_output'; os.makedirs(OUT,exist_ok=True)
S='WOOUSDT'; D='2024-03-29'

def read_trades_fixed(symbol,day):
    raw=m.get_zip('aggTrades',symbol,day)
    df=pd.read_csv(io.BytesIO(raw))
    low={c:str(c).lower().strip() for c in df.columns}
    if not any(v=='price' for v in low.values()):
        df=pd.read_csv(io.BytesIO(raw),header=None).iloc[:,:7]
        df.columns=['agg_id','price','qty','first_id','last_id','timestamp','buyer_maker']
    else:
        rename={}
        for c,lc in low.items():
            if lc=='price': rename[c]='price'
            elif lc in ('quantity','qty'): rename[c]='qty'
            elif lc in ('timestamp','time','transact_time','transaction_time') or (lc.endswith('_time') and 'buyer' not in lc): rename[c]='timestamp'
            elif 'buyer' in lc and 'maker' in lc: rename[c]='buyer_maker'
        df=df.rename(columns=rename)
        if 'buyer_maker' not in df.columns and len(df.columns)>=7: df=df.rename(columns={df.columns[6]:'buyer_maker'})
    for c in ['price','qty','timestamp']: df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['price','qty','timestamp']).copy()
    if df['buyer_maker'].dtype==bool:
        pass
    else:
        df['buyer_maker']=df['buyer_maker'].astype(str).str.lower().isin(['true','1','t'])
    df['ts']=df.timestamp.astype(np.int64)
    return df[['ts','price','qty','buyer_maker']].sort_values('ts').reset_index(drop=True)

print('loading WOO historical L1 + aggTrades',flush=True)
b=m.read_book(S,D); t=read_trades_fixed(S,D)
print('rows',len(b),len(t),flush=True)
start=max(int(b.ts.min()),int(t.ts.min())); cut1=start+2*3600_000; cut2=start+4*3600_000
bt=b[(b.ts>=start)&(b.ts<cut1)].reset_index(drop=True); tt=t[(t.ts>=start)&(t.ts<cut1)].reset_index(drop=True)
bo=b[(b.ts>=cut1)&(b.ts<cut2)].reset_index(drop=True); to=t[(t.ts>=cut1)&(t.ts<cut2)].reset_index(drop=True)
configs=[
 {'lat':10,'qmin':0.0,'flowcap':1.0,'qmult':1.0},
 {'lat':10,'qmin':0.3,'flowcap':0.5,'qmult':1.0},
 {'lat':10,'qmin':0.6,'flowcap':0.5,'qmult':1.0},
 {'lat':240,'qmin':0.0,'flowcap':1.0,'qmult':1.0},
 {'lat':240,'qmin':0.3,'flowcap':0.5,'qmult':1.0},
 {'lat':240,'qmin':0.6,'flowcap':0.5,'qmult':1.0},
]
rows=[]
for i,c in enumerate(configs):
  a=m.stat(m.simulate(bt,tt,c,front=False)); z=m.stat(m.simulate(bo,to,c,front=False)); f=m.stat(m.simulate(bo,to,c,front=True))
  rows.append({'rid':i,**c,'train_n':a['n'],'train_net':a['net'],'oos_n':z['n'],'oos_net':z['net'],'oos_gross':z['gross'],'oos_mm':z['mm'],'oos_markout':z['mark'],'front_n':f['n'],'front_net':f['net'],'front_mm':f['mm']})
R=pd.DataFrame(rows); R.to_csv(f'{OUT}/results.csv',index=False)
mid=(b.best_bid_price+b.best_ask_price)/2; spr=(b.best_ask_price-b.best_bid_price)/mid*1e4
lines=['# WOOUSDT maker mechanism check','', '- 2024-03-29: first 2h train diagnostic, next 2h OOS.','- Conservative displayed L1 queue model; $100 order; 4bp maker/maker and 7bp maker/taker fees.','- Historical test is structural only; current WOO tick/spread economics differ.','',f'Median full-day historical spread: {spr.median():.3f} bp','',R.to_markdown(index=False,floatfmt='.3f')]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
