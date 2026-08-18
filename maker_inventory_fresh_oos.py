import asyncio,json,os,time,urllib.parse,urllib.request
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['GALAUSDT','IMXUSDT'];CAPTURE=300;STEP=250;LAT=10;ORDER_USD=100.;CAP_USD=100.;MIN_SPREAD=4.25;MAKER=.0002;TAKER=.0005
OUT='maker_inventory_fresh_oos_output';os.makedirs(OUT,exist_ok=True)
books=defaultdict(list);trades=defaultdict(list);last_ids={}
BOOK='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
REST='https://fapi.binance.com/fapi/v1/trades'

def recent(s):
 q=urllib.parse.urlencode({'symbol':s,'limit':1000});req=urllib.request.Request(REST+'?'+q,headers={'User-Agent':'locked-maker-oos/1.0'})
 with urllib.request.urlopen(req,timeout=8) as r:return json.loads(r.read().decode())
async def trade_poll(stop):
 for s in SYMBOLS:
  try:
   rr=await asyncio.to_thread(recent,s);last_ids[s]=max(int(x['id']) for x in rr) if rr else -1
  except Exception as e:print('seed',s,repr(e),flush=True);last_ids[s]=-1
 while time.monotonic()<stop:
  for s in SYMBOLS:
   try:
    rr=await asyncio.to_thread(recent,s);old=last_ids.get(s,-1);fresh=[x for x in rr if int(x['id'])>old]
    fresh.sort(key=lambda x:int(x['id']))
    for x in fresh:
     if bool(x.get('isRPITrade',False)):continue
     trades[s].append((int(x['id']),int(x['time']),float(x['price']),float(x['qty']),bool(x['isBuyerMaker'])))
    if fresh:last_ids[s]=max(old,max(int(x['id']) for x in fresh))
   except Exception as e:print('poll',s,repr(e),flush=True)
  await asyncio.sleep(1.0)
async def book_collect(stop):
 async with websockets.connect(BOOK,ping_interval=10,ping_timeout=10,max_queue=300000) as ws:
  while time.monotonic()<stop:
   try:raw=await asyncio.wait_for(ws.recv(),2)
   except asyncio.TimeoutError:continue
   o=json.loads(raw);d=o.get('data',o);s=d.get('s')
   if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
    ts=int(d.get('T',d.get('E')));books[s].append((ts,float(d['b']),float(d['B']),float(d['a']),float(d['A'])))
async def capture():
 stop=time.monotonic()+CAPTURE;await asyncio.gather(book_collect(stop),trade_poll(stop))

class Tape:
 def __init__(self,s):
  self.b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
  self.t=pd.DataFrame(trades[s],columns=['trade_id','ts','price','qty','buyer_maker']).sort_values(['ts','trade_id']).drop_duplicates('trade_id').reset_index(drop=True)
  if len(self.b):self.b['mid']=(self.b.bid+self.b.ask)/2;self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
  self.bt=self.b.ts.to_numpy(np.int64);self.bid=self.b.bid.to_numpy(float);self.ask=self.b.ask.to_numpy(float);self.bq=self.b.bidq.to_numpy(float);self.aq=self.b.askq.to_numpy(float);self.mid=self.b.mid.to_numpy(float);self.spr=self.b.spread_bp.to_numpy(float)
  self.tt=self.t.ts.to_numpy(np.int64);self.tp=self.t.price.to_numpy(float);self.tq=self.t.qty.to_numpy(float);self.tm=self.t.buyer_maker.astype(bool).to_numpy()
 def bi(self,z):
  i=np.searchsorted(self.bt,z,'right')-1;return i if i>=0 else None
 def stable(self,a,z,side,px):
  lo=np.searchsorted(self.bt,a,'right');hi=np.searchsorted(self.bt,z,'right');x=(self.bid if side=='buy' else self.ask)[lo:hi]
  return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
 def qty(self,a,z,side,px):
  lo=np.searchsorted(self.tt,a,'right');hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  p=self.tp[lo:hi];q=self.tq[lo:hi];m=self.tm[lo:hi];mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15));return float(q[mask].sum())

def sim(s,x):
 if len(x.bt)<3 or len(x.tt)<2:return {'symbol':s,'maker_fills':0,'quote_attempts':0,'maker_volume_usd':0.,'liquidation_volume_usd':0.,'total_volume_usd':0.,'liquidation_share':np.nan,'gross_cash_pnl':0.,'fees_usd':0.,'net_pnl_usd':0.,'net_bp_per_volume':np.nan,'max_inventory_usd':0.},[]
 start=max(int(x.bt[0])+1000,int(x.tt[0])+1000);end=min(int(x.bt[-1]),int(x.tt[-1]));orders={'buy':None,'sell':None};cash=fees=pos=vol=0.;prev=start;fills=[];attempts=0;maxinv=0.
 for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
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
    n=o['own']*o['px'];fee=n*MAKER
    if side=='buy':cash-=n;pos+=o['own']
    else:cash+=n;pos-=o['own']
    fees+=fee;vol+=n;fills.append({'symbol':s,'ts':now,'side':side,'px':o['px'],'notional':n,'inventory_qty_after':pos});orders[side]=None
  inv=pos*x.mid[i];maxinv=max(maxinv,abs(inv));ok=x.spr[i]>=MIN_SPREAD
  buy_ok=ok and inv<CAP_USD and inv<=0.5*CAP_USD;sell_ok=ok and inv>-CAP_USD and inv>=-0.5*CAP_USD
  for side,allow in [('buy',buy_ok),('sell',sell_ok)]:
   if not allow:orders[side]=None;continue
   place=now+LAT;j=x.bi(place)
   if j is None:continue
   px=x.bid[j] if side=='buy' else x.ask[j];qa=x.bq[j] if side=='buy' else x.aq[j];own=ORDER_USD/px;o=orders[side]
   if o is not None and abs(o['px']-px)<=1e-15:continue
   orders[side]={'px':float(px),'own':float(own),'need':float(qa+own),'place':int(place)};attempts+=1
  prev=now
 i=x.bi(end);liq=0.
 if i is not None and abs(pos)>1e-15:
  if pos>0:n=pos*x.bid[i];cash+=n
  else:n=(-pos)*x.ask[i];cash-=n
  liq=float(n);fees+=liq*TAKER;vol+=liq
 pnl=cash-fees
 return {'symbol':s,'maker_fills':len(fills),'quote_attempts':attempts,'maker_volume_usd':vol-liq,'liquidation_volume_usd':liq,'total_volume_usd':vol,'liquidation_share':liq/vol if vol else np.nan,'gross_cash_pnl':cash,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan,'max_inventory_usd':maxinv},fills

asyncio.run(capture());rows=[];ff=[];diag=[]
for s in SYMBOLS:
 x=Tape(s);r,f=sim(s,x);rows.append(r);ff.extend(f)
 x.b.to_csv(f'{OUT}/{s}_book.csv',index=False);x.t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
 diag.append({'symbol':s,'book_events':len(x.b),'non_rpi_market_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan,'p75_spread_bp':x.b.spread_bp.quantile(.75) if len(x.b) else np.nan})
R=pd.DataFrame(rows);D=pd.DataFrame(diag);F=pd.DataFrame(ff);R.to_csv(f'{OUT}/summary.csv',index=False);D.to_csv(f'{OUT}/diagnostics.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
tv=R.total_volume_usd.sum();pnl=R.net_pnl_usd.sum();agg=pnl/tv*1e4 if tv else np.nan
pass_gala=bool(len(R[R.symbol=='GALAUSDT']) and R.loc[R.symbol=='GALAUSDT','maker_fills'].iloc[0]>=5 and R.loc[R.symbol=='GALAUSDT','net_bp_per_volume'].iloc[0]>=0)
lines=['# Fresh locked inventory-maker OOS','',f'- Fresh prospective capture: {CAPTURE}s; symbols GALAUSDT + IMXUSDT.','- Configuration frozen before capture: 10ms order-to-exchange, symmetric BBO, $100/order, $100 inventory cap, min spread 4.25bp.','- Trades are REST market trades; RPI trades are excluded.','- Full displayed L1 queue + own size must trade through. Any touch change cancels quote and forfeits queue progress; no cancellation credit.','- Maker fee 2bp/fill; all residual inventory taker-flattened at opposite touch with 5bp fee.','', '## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f'),'','## Results','',R.to_markdown(index=False,floatfmt='.4f'),'',f'Aggregate net bp / genuine volume: **{agg:.4f} bp**',f'GALA minimum screen (>=5 maker fills and net>=0): **{"PASS" if pass_gala else "FAIL"}**.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
