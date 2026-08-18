import asyncio, json, math, os, time
import numpy as np
import pandas as pd
import websockets

S='WOOUSDT'; CAPTURE=45; OUT='maker_woo_live_quick_output'; os.makedirs(OUT,exist_ok=True)
ORDER_USD=100.0; MAKER=2.0; TAKER=5.0
books=[]; trades=[]
BOOK='wss://fstream.binance.com/public/ws/woousdt@bookTicker'
TRADE='wss://fstream.binance.com/market/ws/woousdt@aggTrade'

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
async def maincap():
  stop=time.monotonic()+CAPTURE
  await asyncio.gather(cb(stop),ct(stop))
asyncio.run(maincap())

b=pd.DataFrame(books,columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
t=pd.DataFrame(trades,columns=['ts','price','qty','buyer_maker']).sort_values('ts').reset_index(drop=True)
if len(b):
  b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4

def at(ts):
  i=np.searchsorted(b.ts.values,ts,'right')-1
  return None if i<0 else b.iloc[i]
def fs(ts):
  a=t.ts.values; lo=np.searchsorted(a,ts-5000,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
  if len(x)==0:return 0.,0.,0.,0
  n=(x.price*x.qty).values; sg=np.where(x.buyer_maker.values,-n,n)
  return float(sg.sum()/(n.sum()+1e-12)),float(x.loc[x.buyer_maker,'qty'].sum()/5),float(x.loc[~x.buyer_maker,'qty'].sum()/5),len(x)
def fill(start,end,side,px,q,own):
  a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right'); done=0.; need=q+own
  for r in t.iloc[lo:hi].itertuples(index=False):
    ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
    if ok:
      done+=r.qty
      if done>=need:return int(r.ts)
  return None
def mark(ts,side,px,h):
  r=at(ts+h)
  if r is None:return np.nan
  return ((r.mid-px)/px*1e4) if side=='buy' else ((px-r.mid)/px*1e4)

profiles={'balanced':(5.0,.5,1.0),'strict':(2.0,.25,2.0)}
rows=[]; cycles=[]
if len(b)>1 and len(t)>1:
  start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max()))
  for lat in [10,240]:
    for name,(maxq,tox,minbuf) in profiles.items():
      free=start; c=[]
      for sig in np.arange(((start+249)//250)*250,end,250,dtype=np.int64):
        if sig<free:continue
        r=at(sig)
        if r is None or r.spread_bp-4<minbuf:continue
        fl,sell,buy,n=fs(sig)
        if n<2:continue
        qb=r.bidq/(sell+1e-12); qs=r.askq/(buy+1e-12); opts=[]
        if qb<=maxq and fl>=-tox:opts.append(('buy',qb))
        if qs<=maxq and fl<=tox:opts.append(('sell',qs))
        if not opts:continue
        side=min(opts,key=lambda z:z[1])[0]; p=at(sig+lat)
        if p is None:continue
        px=float(p.bid if side=='buy' else p.ask); q=float(p.bidq if side=='buy' else p.askq); own=ORDER_USD/px
        ft=fill(sig+lat,min(sig+lat+8000,end),side,px,q,own)
        if ft is None:continue
        er=at(ft+lat)
        if er is None:continue
        es='sell' if side=='buy' else 'buy'; epx=float(er.ask if es=='sell' else er.bid); eq=float(er.askq if es=='sell' else er.bidq); eown=ORDER_USD/epx
        eft=fill(ft+lat,min(ft+lat+15000,end),es,epx,eq,eown)
        if eft is not None:
          gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fee=4.; mode='MM'; close=eft
        else:
          close=min(ft+lat+15000,end); cr=at(close)
          if cr is None:continue
          cpx=float(cr.bid if side=='buy' else cr.ask); gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fee=7.; mode='MT'
        rec={'lat_ms':lat,'profile':name,'side':side,'signal_ts':sig,'fill_ts':ft,'close_ts':close,'spread_bp':float(r.spread_bp),'qsec':float(qb if side=='buy' else qs),'flow5s':fl,'gross_bp':gross,'fees_bp':fee,'net_bp':gross-fee,'exit_mode':mode,'mark1_bp':mark(ft,side,px,1000),'mark5_bp':mark(ft,side,px,5000)}
        c.append(rec);cycles.append(rec);free=close
      d=pd.DataFrame(c)
      if len(d):
        x=d.net_bp.to_numpy(); se=x.std(ddof=1)/math.sqrt(len(x)) if len(x)>1 else np.nan
        rows.append({'lat_ms':lat,'profile':name,'n':len(d),'mean_net_bp':x.mean(),'mean_gross_bp':d.gross_bp.mean(),'total_net_bp':x.sum(),'mm_exit_rate':(d.exit_mode=='MM').mean(),'win_net':(x>=0).mean(),'mark1_bp':d.mark1_bp.mean(),'mark5_bp':d.mark5_bp.mean(),'ci95_low':x.mean()-1.96*se if np.isfinite(se) else np.nan})
R=pd.DataFrame(rows); C=pd.DataFrame(cycles)
R.to_csv(f'{OUT}/summary.csv',index=False);C.to_csv(f'{OUT}/cycles.csv',index=False);b.to_csv(f'{OUT}/book.csv',index=False);t.to_csv(f'{OUT}/trades.csv',index=False)
lines=['# WOO current maker quick','',f'- Capture: {CAPTURE}s','- $100 orders; full displayed L1 queue + own order must trade through; no cancellation credit.','- Maker/maker fee 4bp; failed maker exit forced taker total 7bp.','- Fixed balanced/strict profiles; no sample fitting.','',f'- book events: {len(b)}; aggTrades: {len(t)}',f"- median spread: {b.spread_bp.median():.3f} bp" if len(b) else '- no book data','', '## Results','']
if len(R):lines.append(R.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('No completed cycles.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
