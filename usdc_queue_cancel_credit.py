import asyncio,json,os,time
from collections import defaultdict
import numpy as np,pandas as pd,websockets

SYMS=['SOLUSDC','XRPUSDC']; CAP=120; STEP=100; ORDER=70.; INV_CAP=70.; TAKER_BP=4.
LATS=[10,240]; CANCEL_CREDITS=[0.,.25,.5,1.]; TRADE_CREDIT=.5; FILTERS={'none':0.,'weak':.5}
OUT='usdc_queue_cancel_output'; os.makedirs(OUT,exist_ok=True)
B=defaultdict(list);T=defaultdict(list)
BU='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMS)
TR='wss://fstream.binance.com/market/stream?streams='+'/'.join(f'{s.lower()}@aggTrade' for s in SYMS)
async def cb(stop):
 async with websockets.connect(BU,ping_interval=10,ping_timeout=10,max_queue=500000) as w:
  while time.monotonic()<stop:
   try:d=json.loads(await asyncio.wait_for(w.recv(),2)).get('data',{})
   except asyncio.TimeoutError:continue
   s=d.get('s')
   if s in SYMS and all(k in d for k in ('b','B','a','A')):B[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))
async def ct(stop):
 async with websockets.connect(TR,ping_interval=10,ping_timeout=10,max_queue=500000) as w:
  while time.monotonic()<stop:
   try:d=json.loads(await asyncio.wait_for(w.recv(),2)).get('data',{})
   except asyncio.TimeoutError:continue
   s=d.get('s')
   if s in SYMS and d.get('e')=='aggTrade':T[s].append((int(d.get('a',0)),int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))
async def capture():
 stop=time.monotonic()+CAP; await asyncio.gather(cb(stop),ct(stop))
class Tape:
 def __init__(self,s):
  self.b=pd.DataFrame(B[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True);self.t=pd.DataFrame(T[s],columns=['id','ts','price','qty','bm']).sort_values(['ts','id']).drop_duplicates('id').reset_index(drop=True)
  if len(self.b):self.b['mid']=(self.b.bid+self.b.ask)/2;self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
  self.bt=self.b.ts.to_numpy(np.int64);self.bid=self.b.bid.to_numpy(float);self.ask=self.b.ask.to_numpy(float);self.bq=self.b.bidq.to_numpy(float);self.aq=self.b.askq.to_numpy(float);self.mid=self.b.mid.to_numpy(float)
  self.tt=self.t.ts.to_numpy(np.int64);self.tp=self.t.price.to_numpy(float);self.tq=self.t.qty.to_numpy(float);self.tm=self.t.bm.astype(bool).to_numpy()
 def bi(self,z):
  i=np.searchsorted(self.bt,z,'right')-1;return i if i>=0 else None
 def flow(self,z):
  lo=np.searchsorted(self.tt,z-1000,'left');hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  n=self.tp[lo:hi]*self.tq[lo:hi];return float(np.where(self.tm[lo:hi],-n,n).sum()/(n.sum()+1e-12))
 def qty(self,a,z,side,px):
  lo=np.searchsorted(self.tt,a,'right');hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  p=self.tp[lo:hi];q=self.tq[lo:hi];m=self.tm[lo:hi];mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15));return float(q[mask].sum())
 def mark(self,z,side,px):
  i=self.bi(z+1000)
  if i is None:return np.nan
  return ((self.mid[i]-px)/px*1e4) if side=='buy' else ((px-self.mid[i])/px*1e4)
def replay(s,x,lat,cc,fn):
 if len(x.bt)<3 or len(x.tt)<3:return None,[]
 st=max(int(x.bt[0])+1500,int(x.tt[0])+1500);en=min(int(x.bt[-1]),int(x.tt[-1]));alpha=FILTERS[fn]
 cash=pos=mv=maxinv=0.;attempts=0;prev=st;orders={'buy':None,'sell':None};fills=[]
 for now in np.arange(((st+STEP-1)//STEP)*STEP,en+1,STEP,dtype=np.int64):
  i=x.bi(now)
  if i is None:continue
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   a=max(prev,o['place'])
   if now<=a:continue
   curpx=float(x.bid[i] if side=='buy' else x.ask[i]);curq=float(x.bq[i] if side=='buy' else x.aq[i])
   if abs(curpx-o['px'])>1e-15:orders[side]=None;continue
   tq=x.qty(a,now,side,o['px'])*TRADE_CREDIT
   raw_trade=tq/max(TRADE_CREDIT,1e-12)
   cancel_est=max(0.,o['last_q']-curq-raw_trade)
   o['ahead']=max(0.,o['ahead']-cc*cancel_est)
   take=min(o['ahead'],tq);o['ahead']-=take;tq-=take
   if tq>0:o['own_left']-=tq
   o['last_q']=curq
   if o['own_left']<=0:
    n=o['own']*o['px'];cash+=n if side=='sell' else -n;pos+=-o['own'] if side=='sell' else o['own'];mv+=n;fills.append({'symbol':s,'lat':lat,'cancel_credit':cc,'filter':fn,'ts':int(now),'side':side,'notional':n,'mark1_bp':x.mark(now,side,o['px'])});orders[side]=None
  inv=pos*x.mid[i];maxinv=max(maxinv,abs(inv));fl=x.flow(now)
  for side in ['buy','sell']:
   room=(INV_CAP-inv) if side=='buy' else (INV_CAP+inv);room=max(0.,min(ORDER,room))
   if room<5:orders[side]=None;continue
   tox=max(0.,-fl) if side=='buy' else max(0.,fl);room*=max(.25,1-alpha*tox)
   place=int(now+lat);j=x.bi(place)
   if j is None:continue
   px=float(x.bid[j] if side=='buy' else x.ask[j]);q=float(x.bq[j] if side=='buy' else x.aq[j]);own=room/px;o=orders[side]
   if o is not None and abs(o['px']-px)<1e-15 and abs(o['own']-own)/max(own,1e-12)<.05:continue
   orders[side]={'px':px,'own':own,'own_left':own,'ahead':q,'last_q':q,'place':place};attempts+=1
  prev=now
 i=x.bi(en);liq=0.
 if i is not None and abs(pos)>1e-15:
  if pos>0:n=pos*x.bid[i];cash+=n
  else:n=(-pos)*x.ask[i];cash-=n
  liq=abs(n)
 total=mv+liq;net=cash-liq*TAKER_BP/1e4;mins=(en-st)/60000.;f=pd.DataFrame(fills)
 return {'symbol':s,'lat_ms':lat,'cancel_credit':cc,'filter':fn,'maker_fills':len(fills),'quote_attempts':attempts,'maker_volume_usd':mv,'maker_volume_per_min':mv/max(mins,1e-9),'liquidation_share':liq/total if total else np.nan,'net_pnl_usd':net,'net_bp_per_volume':net/total*1e4 if total else np.nan,'mean_mark1_bp':float(f.mark1_bp.mean()) if len(f) else np.nan,'max_inventory_usd':maxinv},fills
asyncio.run(capture());rows=[];fs=[];dg=[]
for s in SYMS:
 x=Tape(s);x.b.to_csv(f'{OUT}/{s}_book.csv',index=False);x.t.to_csv(f'{OUT}/{s}_trades.csv',index=False);dg.append({'symbol':s,'book_events':len(x.b),'agg_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan})
 for lat in LATS:
  for cc in CANCEL_CREDITS:
   for fn in FILTERS:
    r,f=replay(s,x,lat,cc,fn)
    if r:rows.append(r);fs.extend(f)
R=pd.DataFrame(rows);D=pd.DataFrame(dg);F=pd.DataFrame(fs);R.to_csv(f'{OUT}/results.csv',index=False);D.to_csv(f'{OUT}/diagnostics.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
el=R[(R.maker_fills>=3)&(R.net_bp_per_volume>=0)].sort_values(['maker_volume_per_min','cancel_credit'],ascending=[False,True]) if len(R) else R
lines=['# USDC queue-cancellation credit turnover test','',f'- Prospective {CAP}s SOL/XRP; $70 quote/$70 inventory cap; maker 0bp; end-window taker 4bp.','- Full displayed L1 is initial queue ahead. Trades receive 50% depletion credit.','- Cancellation-credit scenarios 0/25/50/100% estimate what fraction of same-price displayed-size shrink was ahead of us.','- Soft weak filter only sizes down toxic-side quotes; no hard filtering.','','## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f'),'','## Net>=0, >=3 fills ranked turnover','',el.to_markdown(index=False,floatfmt='.4f') if len(el) else 'None.','','## All results','',R.sort_values(['maker_volume_per_min','net_bp_per_volume'],ascending=[False,False]).to_markdown(index=False,floatfmt='.4f') if len(R) else 'None.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
