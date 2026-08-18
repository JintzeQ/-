import asyncio,json,os,time,math
import numpy as np
import pandas as pd
import websockets

S='GALAUSDT'; CAPTURE=180; STEP=250; ORDER_USD=100.; MAKER=2.; TAKER=5.; ENTRY_TTL=3000; HOLD=5000; EXIT_WINDOW=10000
OUT='maker_gala_flip_oos_output';os.makedirs(OUT,exist_ok=True)
books=[];trades=[]
BOOK='wss://fstream.binance.com/public/ws/galausdt@bookTicker'
TRADE='wss://fstream.binance.com/market/ws/galausdt@aggTrade'
async def cb(stop):
  async with websockets.connect(BOOK,ping_interval=10,ping_timeout=10,max_queue=200000) as ws:
    while time.monotonic()<stop:
      try:d=json.loads(await asyncio.wait_for(ws.recv(),2))
      except asyncio.TimeoutError:continue
      books.append((int(d.get('T',d['E'])),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))
async def ct(stop):
  async with websockets.connect(TRADE,ping_interval=10,ping_timeout=10,max_queue=200000) as ws:
    while time.monotonic()<stop:
      try:d=json.loads(await asyncio.wait_for(ws.recv(),2))
      except asyncio.TimeoutError:continue
      trades.append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))
async def cap():
  stop=time.monotonic()+CAPTURE;await asyncio.gather(cb(stop),ct(stop))
asyncio.run(cap())
b=pd.DataFrame(books,columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
t=pd.DataFrame(trades,columns=['ts','price','qty','buyer_maker']).sort_values('ts').reset_index(drop=True)
if len(b):
  b['mid']=(b.bid+b.ask)/2;b['spread_bp']=(b.ask-b.bid)/b.mid*1e4

def at(ts):
  i=np.searchsorted(b.ts.values,ts,'right')-1;return None if i<0 else b.iloc[i]
def fl(a,z):
  x=t[(t.ts>a)&(t.ts<=z)]
  if len(x)==0:return 0.,0
  n=(x.price*x.qty).to_numpy();sg=np.where(x.buyer_maker.to_numpy(),-n,n);return float(sg.sum()/(n.sum()+1e-12)),len(x)
def fill(start,end,side,px,queue,own):
  x=t[(t.ts>=start)&(t.ts<=end)];need=float(queue)+float(own);done=0.
  for r in x.itertuples(index=False):
    ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
    if ok:
      done+=r.qty
      if done>=need:return int(r.ts)
  return None
def gross(side,e,x):return ((x-e)/e*1e4) if side=='buy' else ((e-x)/e*1e4)
def mark(ft,side,e,h):
  r=at(ft+h)
  if r is None:return np.nan
  return ((r.mid-e)/e*1e4) if side=='buy' else ((e-r.mid)/e*1e4)

def sim(lat):
  if len(b)<3 or len(t)<3:return pd.DataFrame(),0,0
  start=max(int(b.ts.min())+1500,int(t.ts.min())+1500);end=min(int(b.ts.max()),int(t.ts.max()));free=start;rows=[];signals=entries=0
  for sig in np.arange(((start+STEP-1)//STEP)*STEP,end,STEP,dtype=np.int64):
    if sig<free:continue
    r=at(sig);p=at(sig-250)
    if r is None or p is None or r.spread_bp<4.25:continue
    fp,npv=fl(sig-1250,sig-250);fn,nn=fl(sig-250,sig)
    if npv<2:continue
    side=None
    if fp<=-0.70 and fn>=0.20 and r.mid>=p.mid:side='buy'
    elif fp>=0.70 and fn<=-0.20 and r.mid<=p.mid:side='sell'
    if side is None:continue
    signals+=1;place=sig+lat;pr=at(place)
    if pr is None:continue
    ep=float(pr.bid if side=='buy' else pr.ask);q=float(pr.bidq if side=='buy' else pr.askq);own=ORDER_USD/ep
    ft=fill(place,min(place+ENTRY_TTL,end),side,ep,q,own)
    if ft is None:free=sig+1000;continue
    entries+=1
    exit_begin=ft+HOLD+lat;exit_end=min(exit_begin+EXIT_WINDOW,end);close=None;xp=None;mode=None
    es='sell' if side=='buy' else 'buy'
    for qt in np.arange(((exit_begin+STEP-1)//STEP)*STEP,exit_end,STEP,dtype=np.int64):
      qr=at(qt)
      if qr is None:continue
      px=float(qr.ask if es=='sell' else qr.bid)
      if gross(side,ep,px)<4.0:continue
      qq=float(qr.askq if es=='sell' else qr.bidq);oq=ORDER_USD/px
      ef=fill(qt,min(qt+STEP,exit_end),es,px,qq,oq)
      if ef is not None:close=ef;xp=px;mode='MM';break
    if close is None:
      close=exit_end;cr=at(close)
      if cr is None:break
      xp=float(cr.bid if side=='buy' else cr.ask);mode='MT'
    g=gross(side,ep,xp);fees=4. if mode=='MM' else 7.
    rows.append({'lat_ms':lat,'side':side,'signal_ts':sig,'fill_ts':ft,'close_ts':close,'entry':ep,'exit':xp,'spread_signal_bp':float(r.spread_bp),'flow_prev':fp,'flow_now':fn,'mark1_bp':mark(ft,side,ep,1000),'mark5_bp':mark(ft,side,ep,5000),'mark15_bp':mark(ft,side,ep,15000),'gross_bp':g,'fees_bp':fees,'net_bp':g-fees,'exit_mode':mode})
    free=close
  return pd.DataFrame(rows),signals,entries

summary=[];allc=[]
for lat in [10,240]:
  d,sig,en=sim(lat)
  if len(d):
    allc.append(d);x=d.net_bp.to_numpy();se=x.std(ddof=1)/math.sqrt(len(x)) if len(x)>1 else np.nan
    summary.append({'lat_ms':lat,'signals':sig,'entry_fills':en,'cycles':len(d),'entry_fill_rate':en/max(sig,1),'mean_net_bp':x.mean(),'total_net_bp':x.sum(),'mean_gross_bp':d.gross_bp.mean(),'mm_exit_rate':(d.exit_mode=='MM').mean(),'win_net':(x>=0).mean(),'mark5_bp':d.mark5_bp.mean(),'ci95_low':x.mean()-1.96*se if np.isfinite(se) else np.nan,'roundtrip_volume_usd':2*ORDER_USD*len(d)})
  else:summary.append({'lat_ms':lat,'signals':sig,'entry_fills':en,'cycles':0,'entry_fill_rate':en/max(sig,1),'mean_net_bp':np.nan,'total_net_bp':0.,'mean_gross_bp':np.nan,'mm_exit_rate':np.nan,'win_net':np.nan,'mark5_bp':np.nan,'ci95_low':np.nan,'roundtrip_volume_usd':0.})
R=pd.DataFrame(summary);C=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame();R.to_csv(f'{OUT}/summary.csv',index=False);C.to_csv(f'{OUT}/cycles.csv',index=False);b.to_csv(f'{OUT}/book.csv',index=False);t.to_csv(f'{OUT}/trades.csv',index=False)
lines=['# GALA sweep-flip maker — prospective OOS round-trip','',f'- Fresh untouched capture: {CAPTURE}s.','- Entry rule fixed from prior exploratory screen: prior 1s |flow|>=0.70, final 250ms must flip opposite by >=0.20, and mid must stop extending adversely.','- $100 maker entry; full displayed L1 queue + own size must trade through within 3s; no cancellation credit.','- Hold 5s. Then 250ms cancel/requote maker exits are attempted for 10s only when locked gross >=4bp.','- If no maker exit fills, residual inventory is taker-flattened. Fees: MM 4bp, MT 7bp.','- 10ms and 240ms order-to-exchange tested.','', '## Results','',R.to_markdown(index=False,floatfmt='.3f'),'','## Cycles','']
if len(C):lines.append(C.to_markdown(index=False,floatfmt='.4f'))
else:lines.append('No completed cycles.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
