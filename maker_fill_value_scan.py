import asyncio, json, math, os, time
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['FETUSDT','OPUSDT','WIFUSDT','IMXUSDT','ARKMUSDT','JUPUSDT','WOOUSDT','CHZUSDT','GALAUSDT','BBUSDT','ROBOUSDT','RAREUSDT','APEUSDT','BLURUSDT','GMTUSDT','SANDUSDT','MASKUSDT','API3USDT','C98USDT','ONEUSDT']
CAPTURE=120; STEP=250; TTL=3000; ORDER_USD=100.0; MAKER_FEE=2.0
OUT='maker_fill_value_scan_output'; os.makedirs(OUT,exist_ok=True)
books=defaultdict(list); trades=defaultdict(list)
BOOK='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
TRADE='wss://fstream.binance.com/market/stream?streams='+'/'.join(f'{s.lower()}@aggTrade' for s in SYMBOLS)

async def collect_book(stop):
    async with websockets.connect(BOOK,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),2)
            except asyncio.TimeoutError: continue
            o=json.loads(raw); d=o.get('data',o); s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))
async def collect_trade(stop):
    async with websockets.connect(TRADE,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),2)
            except asyncio.TimeoutError: continue
            o=json.loads(raw); d=o.get('data',o); s=d.get('s')
            if s in SYMBOLS and d.get('e')=='aggTrade':
                trades[s].append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))
async def cap():
    stop=time.monotonic()+CAPTURE
    await asyncio.gather(collect_book(stop),collect_trade(stop))
asyncio.run(cap())

def frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq'])
    t=pd.DataFrame(trades[s],columns=['ts','price','qty','buyer_maker'])
    if len(b):
        b=b.sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True); b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
    if len(t):t=t.sort_values('ts').reset_index(drop=True)
    return b,t

def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]
def flow5(t,ts):
    a=t.ts.values; lo=np.searchsorted(a,ts-5000,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.0
    n=(x.price*x.qty).values; sg=np.where(x.buyer_maker.values,-n,n)
    return float(sg.sum()/(n.sum()+1e-12))
def fill(t,start,end,side,px,queue,own):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right'); need=float(queue)+float(own); done=0.
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None
def mark(b,ft,side,px,h):
    r=at(b,ft+h)
    if r is None:return np.nan
    return ((float(r.mid)-px)/px*1e4) if side=='buy' else ((px-float(r.mid))/px*1e4)

def scan(s,b,t,lat,profile):
    if len(b)<2 or len(t)<2:return pd.DataFrame(),0
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max())); attempts=0; out=[]
    next_free={'buy':start,'sell':start}
    for sig in np.arange(((start+STEP-1)//STEP)*STEP,end,STEP,dtype=np.int64):
        fl=flow5(t,sig)
        for side in ['buy','sell']:
            if sig<next_free[side]:continue
            if profile=='protected' and ((side=='buy' and fl<0) or (side=='sell' and fl>0)):continue
            if profile=='neutral' and abs(fl)>0.20:continue
            p=at(b,sig+lat)
            if p is None or p.spread_bp<4.25:continue
            px=float(p.bid if side=='buy' else p.ask); q=float(p.bidq if side=='buy' else p.askq); own=ORDER_USD/px; attempts+=1
            ft=fill(t,sig+lat,min(sig+lat+TTL,end),side,px,q,own)
            if ft is None:
                next_free[side]=sig+TTL;continue
            out.append({'symbol':s,'lat_ms':lat,'profile':profile,'side':side,'signal_ts':sig,'fill_ts':ft,'entry_px':px,'spread_bp':float(p.spread_bp),'flow5':fl,'mark1_bp':mark(b,ft,side,px,1000),'mark5_bp':mark(b,ft,side,px,5000),'mark15_bp':mark(b,ft,side,px,15000)})
            next_free[side]=ft+5000
    return pd.DataFrame(out),attempts

rows=[]; fills=[]; diag=[]
for s in SYMBOLS:
    b,t=frames(s)
    if len(b):diag.append({'symbol':s,'book_events':len(b),'agg_trades':len(t),'median_spread_bp':float(b.spread_bp.median()),'p75_spread_bp':float(b.spread_bp.quantile(.75)),'median_bid_usd':float((b.bid*b.bidq).median()),'median_ask_usd':float((b.ask*b.askq).median())})
    for lat in [10,240]:
      for profile in ['all','protected','neutral']:
        d,att=scan(s,b,t,lat,profile)
        if len(d):
            fills.append(d)
            for h in [1,5,15]:d[f'value{h}_bp']=d[f'mark{h}_bp']-MAKER_FEE
            rows.append({'symbol':s,'lat_ms':lat,'profile':profile,'attempts':att,'fills':len(d),'fill_rate':len(d)/max(att,1),'mean_spread_bp':d.spread_bp.mean(),'value1_bp':d.value1_bp.mean(),'value5_bp':d.value5_bp.mean(),'value15_bp':d.value15_bp.mean(),'positive5_rate':(d.value5_bp>=0).mean()})
        else:rows.append({'symbol':s,'lat_ms':lat,'profile':profile,'attempts':att,'fills':0,'fill_rate':0.0,'mean_spread_bp':np.nan,'value1_bp':np.nan,'value5_bp':np.nan,'value15_bp':np.nan,'positive5_rate':np.nan})
R=pd.DataFrame(rows);D=pd.DataFrame(diag);F=pd.concat(fills,ignore_index=True) if fills else pd.DataFrame();R.to_csv(f'{OUT}/summary.csv',index=False);D.to_csv(f'{OUT}/diagnostics.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
valid=R[R.fills>=3].sort_values(['value5_bp','fills'],ascending=[False,False]);pos=valid[valid.value5_bp>=0]
lines=['# Current maker fill-value scan','',f'- Prospective capture: {CAPTURE}s across {len(SYMBOLS)} USDⓈ-M contracts.','- $100 passive quote. Full displayed L1 queue + own size must trade through within 3s; no cancellation credit.','- Fill value = side-adjusted future mid markout from maker fill price minus the 2bp maker fee.','- This deliberately tests one passive fill before building a round-trip strategy. A positive 5s fill value is necessary evidence, not sufficient deployment proof.','- `protected`: bid only under nonnegative 5s flow / ask only under nonpositive flow. `neutral`: |5s flow|<=0.20.','', '## Diagnostics','']
if len(D):lines.append(D.to_markdown(index=False,floatfmt='.3f'))
lines+=['','## Positive 5s after-fee passive fill value (>=3 modeled fills)','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
lines+=['','## Top candidates regardless of sign','']
if len(valid):lines.append(valid.head(20).to_markdown(index=False,floatfmt='.3f'))
else:lines.append('No configuration produced at least 3 full-queue modeled fills.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
