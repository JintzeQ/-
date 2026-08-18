import asyncio, json, os, time
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['SOLUSDC','XRPUSDC','DOGEUSDC','LTCUSDC']
CAPTURE=120; STEP=500; TTL=3000; ORDER_USD=70.0
OUT='usdc_maker_quick_output'; os.makedirs(OUT,exist_ok=True)
books=defaultdict(list); trades=defaultdict(list)
STREAM='wss://fstream.binance.com/stream?streams='+'/'.join([z for s in SYMBOLS for z in (f'{s.lower()}@bookTicker',f'{s.lower()}@aggTrade')])

async def cap():
    stop=time.monotonic()+CAPTURE
    async with websockets.connect(STREAM,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: d=json.loads(await asyncio.wait_for(ws.recv(),2)).get('data',{})
            except asyncio.TimeoutError: continue
            s=d.get('s')
            if s not in SYMBOLS: continue
            if d.get('e')=='aggTrade': trades[s].append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))
            elif all(k in d for k in ('b','B','a','A')): books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

asyncio.run(cap())

def frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']); t=pd.DataFrame(trades[s],columns=['ts','price','qty','bm'])
    if len(b):
        b=b.sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True); b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
    if len(t): t=t.sort_values('ts').reset_index(drop=True)
    return b,t

def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]
def flow(t,ts):
    a=t.ts.values; lo=np.searchsorted(a,ts-1000,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.
    n=(x.price*x.qty).values; return float(np.where(x.bm.values,-n,n).sum()/(n.sum()+1e-12))
def fill(t,start,end,side,px,need):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right'); done=0.
    for r in t.iloc[lo:hi].itertuples(index=False):
        if (side=='buy' and r.bm and r.price<=px+1e-15) or (side=='sell' and (not r.bm) and r.price>=px-1e-15):
            done+=r.qty
            if done>=need:return int(r.ts)
    return None
def mark(b,ts,side,px,h):
    r=at(b,ts+h)
    if r is None:return np.nan
    return ((r.mid-px)/px*1e4) if side=='buy' else ((px-r.mid)/px*1e4)

rows=[]; di=[]
for s in SYMBOLS:
    b,t=frames(s); b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
    if len(b): di.append({'symbol':s,'book_events':len(b),'trades':len(t),'median_spread_bp':b.spread_bp.median(),'p75_spread_bp':b.spread_bp.quantile(.75)})
    if len(b)<2 or len(t)<2: continue
    start=max(int(b.ts.min()),int(t.ts.min())+1000); end=min(int(b.ts.max()),int(t.ts.max()))
    for lat in [10,240]:
      for profile in ['all','protected']:
       for qmult in [0.5,1.0]:
        vals=[]; spreads=[]; attempts=0
        for sig in np.arange(((start+STEP-1)//STEP)*STEP,end,STEP,dtype=np.int64):
            r=at(b,sig); fl=flow(t,sig)
            if r is None or r.spread_bp<0.5: continue
            for side in ['buy','sell']:
                if profile=='protected' and ((side=='buy' and fl<-0.15) or (side=='sell' and fl>0.15)): continue
                p=at(b,sig+lat)
                if p is None:continue
                px=float(p.bid if side=='buy' else p.ask); q=float(p.bidq if side=='buy' else p.askq); own=ORDER_USD/px; attempts+=1
                ft=fill(t,sig+lat,min(sig+lat+TTL,end),side,px,q*qmult+own)
                if ft is None:continue
                vals.append((mark(b,ft,side,px,1000),mark(b,ft,side,px,5000))); spreads.append(float(p.spread_bp))
        if vals:
            a=np.array(vals,float)
            rows.append({'symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'attempts':attempts,'fills':len(vals),'fill_rate':len(vals)/max(attempts,1),'mean_spread_bp':np.mean(spreads),'mark1_bp':np.nanmean(a[:,0]),'mark5_bp':np.nanmean(a[:,1]),'positive5_rate':np.nanmean(a[:,1]>=0)})
        else: rows.append({'symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'attempts':attempts,'fills':0,'fill_rate':0,'mean_spread_bp':np.nan,'mark1_bp':np.nan,'mark5_bp':np.nan,'positive5_rate':np.nan})
R=pd.DataFrame(rows); D=pd.DataFrame(di); R.to_csv(f'{OUT}/results.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False)
valid=R[R.fills>=3].sort_values(['mark5_bp','fills'],ascending=[False,False]); pos=valid[valid.mark5_bp>=0]
lines=['# Quick USDC zero-maker fill probe','',f'- {CAPTURE}s prospective capture; $70 quote; maker fee 0bp.','- Full/half displayed L1 queue + own size must trade through within 3s. No touch=fill.','- 5s markout is side-adjusted mid-price value after a modeled maker fill; with maker fee 0 it is also the pre-funding passive-fill EV proxy.','','## Diagnostics','',D.to_markdown(index=False,floatfmt='.3f') if len(D) else 'None.','','## Positive 5s fill-value configs (>=3 fills)','',pos.to_markdown(index=False,floatfmt='.3f') if len(pos) else 'None.','','## Best regardless of sign','',valid.head(20).to_markdown(index=False,floatfmt='.3f') if len(valid) else 'No config reached 3 fills.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
