import asyncio,json,os,time
from collections import defaultdict
import numpy as np,pandas as pd,websockets

SYMS=['BTCUSDC','ETHUSDC','SOLUSDC','XRPUSDC']; CAP=120; STEP=100; ORDER=70.; CAPINV=70.; TAKER=4.
FILTERS={'none':0.,'very_weak':.25,'weak':.5,'medium':.75}; QF=[1.,.5,.25]; LATS=[10,240]
OUT='usdc_turnover_v2_output'; os.makedirs(OUT,exist_ok=True)
books=defaultdict(list); trades=defaultdict(list)
streams=[]
for s in SYMS:
 x=s.lower(); streams += [f'{x}@bookTicker',f'{x}@aggTrade']
URL='wss://fstream.binance.com/stream?streams='+'/'.join(streams)

async def cap():
 stop=time.monotonic()+CAP
 async with websockets.connect(URL,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
  while time.monotonic()<stop:
   try:d=json.loads(await asyncio.wait_for(ws.recv(),2)).get('data',{})
   except asyncio.TimeoutError:continue
   s=d.get('s')
   if s not in SYMS:continue
   if d.get('e')=='aggTrade':trades[s].append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))
   elif all(k in d for k in ('b','B','a','A')):books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

class T:
 def __init__(self,s):
  self.b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
  self.t=pd.DataFrame(trades[s],columns=['ts','price','qty','bm']).sort_values('ts').reset_index(drop=True)
  if len(self.b): self.b['mid']=(self.b.bid+self.b.ask)/2; self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
  self.bt=self.b.ts.to_numpy(np.int64) if len(self.b) else np.array([],dtype=np.int64); self.tt=self.t.ts.to_numpy(np.int64) if len(self.t) else np.array([],dtype=np.int64)
  self.bid=self.b.bid.to_numpy(float) if len(self.b) else np.array([]); self.ask=self.b.ask.to_numpy(float) if len(self.b) else np.array([]); self.bq=self.b.bidq.to_numpy(float) if len(self.b) else np.array([]); self.aq=self.b.askq.to_numpy(float) if len(self.b) else np.array([]); self.mid=self.b.mid.to_numpy(float) if len(self.b) else np.array([])
  self.tp=self.t.price.to_numpy(float) if len(self.t) else np.array([]); self.tq=self.t.qty.to_numpy(float) if len(self.t) else np.array([]); self.tm=self.t.bm.astype(bool).to_numpy() if len(self.t) else np.array([],dtype=bool)
 def bi(self,z):
  i=np.searchsorted(self.bt,z,'right')-1; return i if i>=0 else None
 def flow(self,z):
  lo=np.searchsorted(self.tt,z-1000,'left'); hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  n=self.tp[lo:hi]*self.tq[lo:hi]; return float(np.where(self.tm[lo:hi],-n,n).sum()/(n.sum()+1e-12))
 def stable(self,a,z,side,px):
  lo=np.searchsorted(self.bt,a,'right'); hi=np.searchsorted(self.bt,z,'right'); x=(self.bid if side=='buy' else self.ask)[lo:hi]
  return True if len(x)==0 else bool(np.all(np.abs(x-px)<1e-15))
 def qty(self,a,z,side,px):
  lo=np.searchsorted(self.tt,a,'right'); hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  p=self.tp[lo:hi];q=self.tq[lo:hi];m=self.tm[lo:hi]; mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15)); return float(q[mask].sum())
 def mark(self,ts,side,px,h=1000):
  i=self.bi(ts+h)
  if i is None:return np.nan
  return ((self.mid[i]-px)/px*1e4) if side=='buy' else ((px-self.mid[i])/px*1e4)

def replay(s,x,lat,qf,fn):
 if len(x.bt)<3 or len(x.tt)<2:return None,[]
 st=max(int(x.bt[0])+1500,int(x.tt[0])+1500); en=min(int(x.bt[-1]),int(x.tt[-1]));
 if en<=st:return None,[]
 alpha=FILTERS[fn]; cash=pos=mv=maxinv=0.; attempts=0; prev=st; fills=[]; orders={'buy':None,'sell':None}
 for now in np.arange(((st+STEP-1)//STEP)*STEP,en+1,STEP,dtype=np.int64):
  i=x.bi(now)
  if i is None:continue
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   a=max(prev,o['place'])
   if now<=a:continue
   if not x.stable(a,now,side,o['px']):orders[side]=None;continue
   o['need']-=x.qty(a,now,side,o['px'])
   if o['need']<=0:
    n=o['own']*o['px']; cash += n if side=='sell' else -n; pos += -o['own'] if side=='sell' else o['own']; mv+=n
    fills.append({'symbol':s,'filter':fn,'lat_ms':lat,'qfrac':qf,'ts':int(now),'side':side,'notional':n,'mark1_bp':x.mark(now,side,o['px']),'inventory_usd_after':pos*x.mid[i]}); orders[side]=None
  inv=pos*x.mid[i]; maxinv=max(maxinv,abs(inv)); fl=x.flow(now)
  for side in ['buy','sell']:
   if side=='buy' and inv>=CAPINV-1e-9:orders[side]=None;continue
   if side=='sell' and inv<=-CAPINV+1e-9:orders[side]=None;continue
   inc=(side=='buy' and inv>=0) or (side=='sell' and inv<=0); tox=max(0.,-fl) if side=='buy' else max(0.,fl)
   scale=max(.25,1-alpha*tox)*(max(.25,1-abs(inv)/CAPINV) if inc else 1.); notional=ORDER*scale
   place=int(now+lat); j=x.bi(place)
   if j is None:continue
   px=float(x.bid[j] if side=='buy' else x.ask[j]); qa=float(x.bq[j] if side=='buy' else x.aq[j]); own=notional/px; o=orders[side]
   if o is not None and abs(o['px']-px)<1e-15 and abs(o['own']-own)/max(own,1e-12)<.1:continue
   orders[side]={'px':px,'own':own,'need':qa*qf+own,'place':place}; attempts+=1
  prev=now
 i=x.bi(en); liq=0.
 if i is not None and abs(pos)>1e-15:
  if pos>0:n=pos*x.bid[i];cash+=n
  else:n=(-pos)*x.ask[i];cash-=n
  liq=abs(n)
 net=cash-liq*TAKER/1e4; tv=mv+liq; mins=(en-st)/60000.; f=pd.DataFrame(fills)
 return {'symbol':s,'filter':fn,'lat_ms':lat,'qfrac':qf,'maker_fills':len(fills),'quote_attempts':attempts,'maker_volume_usd':mv,'maker_volume_per_min':mv/max(mins,1e-9),'liquidation_volume_usd':liq,'liquidation_share':liq/tv if tv else np.nan,'net_pnl_usd':net,'net_bp_per_volume':net/tv*1e4 if tv else np.nan,'mean_mark1_bp':float(f.mark1_bp.mean()) if len(f) else np.nan,'max_inventory_usd':maxinv,'capture_minutes':mins},fills

asyncio.run(cap()); rows=[]; fs=[]; dg=[]
for s in SYMS:
 x=T(s); x.b.to_csv(f'{OUT}/{s}_book.csv',index=False); x.t.to_csv(f'{OUT}/{s}_aggtrades.csv',index=False)
 dg.append({'symbol':s,'book_events':len(x.b),'agg_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan})
 for lat in LATS:
  for q in QF:
   for fn in FILTERS:
    r,f=replay(s,x,lat,q,fn)
    if r:rows.append(r);fs.extend(f)
R=pd.DataFrame(rows);D=pd.DataFrame(dg);F=pd.DataFrame(fs);R.to_csv(f'{OUT}/results.csv',index=False);D.to_csv(f'{OUT}/diagnostics.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
el=R[(R.maker_fills>=3)&(R.net_bp_per_volume>=0)].sort_values(['maker_volume_per_min','net_bp_per_volume'],ascending=[False,False]) if len(R) else R; pri=el[el.qfrac==1.] if len(el) else el
lines=['# USDC turnover-first maker sweep v2 (WS tape)','',f'- Prospective {CAP}s; maker fee 0bp, residual taker flatten 4bp; $70 quote/$70 inventory cap.','- Objective: maximize maker volume/min subject to net bp/volume >= 0.','- Soft toxicity only changes quote size; no hard flow filter.','- qfrac=1.0 is conservative displayed-L1 primary; 0.5/0.25 sensitivity.','- aggTrade has no RPI flag. Only trades at/through visible touch deplete queue, but same-price RPI contamination cannot be ruled out.','','## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f'),'','## Conservative q=1.0 positive EV','',pri.head(20).to_markdown(index=False,floatfmt='.4f') if len(pri) else 'None.','','## All q sensitivities positive EV','',el.head(30).to_markdown(index=False,floatfmt='.4f') if len(el) else 'None.','','## Highest turnover regardless of EV','',R[R.maker_fills>=3].sort_values('maker_volume_per_min',ascending=False).head(20).to_markdown(index=False,floatfmt='.4f') if len(R) else 'No valid results.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
