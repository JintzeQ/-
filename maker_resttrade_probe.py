import asyncio, json, os, time
from collections import defaultdict
import numpy as np
import pandas as pd
import requests, websockets

SYMBOLS={'FETUSDT':0.0001,'OPUSDT':0.0001,'WIFUSDT':0.0001}
CAPTURE_SECONDS=60
POLL_SECONDS=1.0
INFO_DELAY_MS=1100  # REST trades are never used as signal until this delay has elapsed
ORDER_NOTIONAL=100.0
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
OUT='maker_resttrade_output'
os.makedirs(OUT,exist_ok=True)

books=defaultdict(list); raw_trades={s:{} for s in SYMBOLS}; poll_log=[]
streams='/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
WS='wss://fstream.binance.com/stream?streams='+streams

def poll_once(s):
    u='https://fapi.binance.com/fapi/v1/trades'
    r=requests.get(u,params={'symbol':s,'limit':1000},timeout=10)
    r.raise_for_status()
    now=int(time.time()*1000); x=r.json(); added=0
    for z in x:
        tid=int(z['id'])
        if tid not in raw_trades[s]:
            raw_trades[s][tid]=(int(z['time']),float(z['price']),float(z['qty']),bool(z['isBuyerMaker']),now)
            added+=1
    return now,len(x),added

async def capture_books():
    end=time.monotonic()+CAPTURE_SECONDS
    async with websockets.connect(WS,ping_interval=15,ping_timeout=15,max_queue=200000) as ws:
        while time.monotonic()<end:
            try: msg=await asyncio.wait_for(ws.recv(),timeout=2)
            except asyncio.TimeoutError: continue
            obj=json.loads(msg); d=obj.get('data',obj); s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def poll_trades():
    end=time.monotonic()+CAPTURE_SECONDS
    while time.monotonic()<end:
        t0=time.monotonic()
        results=await asyncio.gather(*(asyncio.to_thread(poll_once,s) for s in SYMBOLS),return_exceptions=True)
        for s,res in zip(SYMBOLS,results):
            if isinstance(res,Exception): poll_log.append((int(time.time()*1000),s,'ERR',repr(res)))
            else: poll_log.append((res[0],s,res[1],res[2]))
        await asyncio.sleep(max(0.05,POLL_SECONDS-(time.monotonic()-t0)))

async def capture():
    await asyncio.gather(capture_books(),poll_trades())

def frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last')
    vals=list(raw_trades[s].values())
    t=pd.DataFrame(vals,columns=['ts','price','qty','buyer_maker','recv_ts']).sort_values(['ts','recv_ts']) if vals else pd.DataFrame(columns=['ts','price','qty','buyer_maker','recv_ts'])
    if len(b):
        b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4; b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    return b.reset_index(drop=True),t.reset_index(drop=True)

def at(b,ts):
    if len(b)==0:return None
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]

def visible_for_signal(t,sig):
    if len(t)==0:return t
    return t[t.ts<=sig-INFO_DELAY_MS]

def activity(t,sig,lookback=5000):
    x=visible_for_signal(t,sig)
    if len(x)==0:return 0
    return int(((x.ts>=sig-INFO_DELAY_MS-lookback)&(x.ts<=sig-INFO_DELAY_MS)).sum())

def flow(t,sig,lookback=1000):
    x=visible_for_signal(t,sig)
    x=x[(x.ts>=sig-INFO_DELAY_MS-lookback)&(x.ts<=sig-INFO_DELAY_MS)]
    if len(x)==0:return 0.0
    n=x.price*x.qty; sg=np.where(x.buyer_maker,-n,n)
    return float(sg.sum()/(n.sum()+1e-12))

def fill_time(t,start,end,side,px,queue_ahead,order_qty,qmult,qmodel):
    if len(t)==0:return None
    x=t[(t.ts>=start)&(t.ts<=end)]
    need=order_qty if qmodel=='front' else queue_ahead*qmult+order_qty
    done=0.0
    for r in x.itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None

def quote(r,side,tick,mode):
    if mode=='improve' and r.ask-r.bid>=2*tick-1e-15:
        return (float(r.bid+tick),0.0) if side=='buy' else (float(r.ask-tick),0.0)
    return (float(r.bid),float(r.bidq)) if side=='buy' else (float(r.ask),float(r.askq))

def markout(b,ts,side,px,h):
    r=at(b,ts+h)
    if r is None:return np.nan
    return ((float(r.mid)-px)/px*1e4) if side=='buy' else ((px-float(r.mid))/px*1e4)

def simulate(s,b,t,lat,amin,flowcap,qmin,qmult,mode,qmodel):
    if len(b)<2 or len(t)==0:return pd.DataFrame()
    start=max(int(b.ts.min()),int(t.ts.min())+INFO_DELAY_MS+5000); end=min(int(b.ts.max()),int(t.ts.max()))
    if end<=start:return pd.DataFrame()
    sigs=np.arange(((start+249)//250)*250,end,250,dtype=np.int64); out=[]; free=start; tick=SYMBOLS[s]
    for sig in sigs:
        if sig<free or activity(t,sig)<amin:continue
        r=at(b,sig)
        if r is None or r.spread_bp<4.25:continue
        fl=flow(t,sig)
        choices=[]
        if r.qimb>=qmin and fl>=-flowcap: choices.append(('buy',float(r.qimb)))
        if r.qimb<=-qmin and fl<=flowcap: choices.append(('sell',float(-r.qimb)))
        if not choices:continue
        side=max(choices,key=lambda z:z[1])[0]
        place=sig+lat; p=at(b,place)
        if p is None:continue
        px,q=quote(p,side,tick,mode); oq=ORDER_NOTIONAL/px
        ft=fill_time(t,place,min(place+3000,end),side,px,q,oq,qmult,qmodel)
        if ft is None:continue
        m1=markout(b,ft,side,px,1000); m5=markout(b,ft,side,px,5000)
        es='sell' if side=='buy' else 'buy'; estart=ft+lat; er=at(b,estart)
        if er is None:continue
        epx,eq=quote(er,es,tick,mode); eoq=ORDER_NOTIONAL/epx
        eft=fill_time(t,estart,min(estart+15000,end),es,epx,eq,eoq,qmult,qmodel)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fee=4.0; ex='MM'; close=eft
        else:
            close=min(estart+15000,end); cr=at(b,close)
            if cr is None:continue
            cpx=float(cr.bid if side=='buy' else cr.ask)
            gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fee=7.0; ex='MT'
        out.append({'symbol':s,'lat_ms':lat,'activity_min':amin,'flow_cap':flowcap,'qmin':qmin,'qmult':qmult,'mode':mode,'qmodel':qmodel,'side':side,'spread_signal_bp':float(r.spread_bp),'markout1_bp':m1,'markout5_bp':m5,'gross_bp':gross,'fees_bp':fee,'net_bp':gross-fee,'exit_mode':ex,'fill_ts':ft,'close_ts':close})
        free=close
    return pd.DataFrame(out)

asyncio.run(capture())
pd.DataFrame(poll_log,columns=['recv_ts','symbol','returned','new']).to_csv(f'{OUT}/poll_log.csv',index=False)
rows=[]; di=[]
for s in SYMBOLS:
    b,t=frames(s); b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
    di.append({'symbol':s,'book_events':len(b),'raw_trades':len(t),'distinct_trade_times':int(t.ts.nunique()) if len(t) else 0,'median_spread_bp':float(b.spread_bp.median()) if len(b) else np.nan,'p75_spread_bp':float(b.spread_bp.quantile(.75)) if len(b) else np.nan})
    for lat in [10,240]:
      for amin in [1,3,6]:
       for fc in [1.0,0.5,0.2]:
        for qm in [0.0,0.2]:
         for qmul in [1.0,1.5]:
          for mode in ['join','improve']:
           for qmodel in ['front','displayed']:
            d=simulate(s,b,t,lat,amin,fc,qm,qmul,mode,qmodel)
            if len(d)==0:continue
            rows.append({'symbol':s,'lat_ms':lat,'activity_min':amin,'flow_cap':fc,'qmin':qm,'qmult':qmul,'mode':mode,'qmodel':qmodel,'n':len(d),'mean_spread_bp':d.spread_signal_bp.mean(),'mean_markout1_bp':d.markout1_bp.mean(),'mean_markout5_bp':d.markout5_bp.mean(),'mm_exit_rate':(d.exit_mode=='MM').mean(),'mean_gross_bp':d.gross_bp.mean(),'mean_net_bp':d.net_bp.mean(),'total_net_bp':d.net_bp.sum(),'win_net':(d.net_bp>=0).mean()})
res=pd.DataFrame(rows); diag=pd.DataFrame(di); diag.to_csv(f'{OUT}/diagnostics.csv',index=False); res.to_csv(f'{OUT}/results.csv',index=False)
rank=res[res.n>=3].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(res) else res
cons=rank[rank.qmodel=='displayed'] if len(rank) else rank; upper=rank[rank.qmodel=='front'] if len(rank) else rank
pos=cons[cons.mean_net_bp>=0] if len(cons) else cons
lines=['# REST-trade maker probe','',f'- Capture {CAPTURE_SECONDS}s; REST raw trades polled every {POLL_SECONDS:.1f}s; BBO via WebSocket.','- Signal-side trade information is delayed by 1.1s to avoid look-ahead from REST polling.','- $100 order; maker/maker fees 4bp; maker/taker emergency unwind 7bp.','- `displayed` queue model requires full displayed L1 queue + our order to trade through; no cancellation credit.','- `front` is only an optimistic upper bound and is never treated as deployable proof.','', '## Diagnostics','', '| symbol | book events | raw trades | distinct trade times | median spread bp | p75 |','|---|---:|---:|---:|---:|---:|']
for r in di:lines.append(f"| {r['symbol']} | {r['book_events']} | {r['raw_trades']} | {r['distinct_trade_times']} | {r['median_spread_bp']:.2f} | {r['p75_spread_bp']:.2f} |")
lines+=['','## Conservative net-positive configs (`displayed`, n>=3)','']
if len(pos):
    lines+=['| symbol | lat | activity | flowcap | qmin | qmult | mode | n | net bp | gross bp | MM exit | 1s markout |','|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|']
    for r in pos.head(15).itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.activity_min} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {r.mode} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mean_gross_bp:.2f} | {r.mm_exit_rate:.2f} | {r.mean_markout1_bp:.2f} |')
else:lines.append('None.')
lines+=['','## Best conservative configs regardless of sign','']
if len(cons):
    lines+=['| symbol | lat | activity | flowcap | qmin | qmult | mode | n | net bp | gross bp | MM exit |','|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|']
    for r in cons.head(12).itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.activity_min} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {r.mode} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mean_gross_bp:.2f} | {r.mm_exit_rate:.2f} |')
else:lines.append('No conservative completed cycles.')
lines+=['','## Front-of-queue upper bound','']
if len(upper):
    for r in upper.head(8).itertuples():lines.append(f'- {r.symbol} {r.lat_ms}ms {r.mode}: n={int(r.n)}, net={r.mean_net_bp:.2f}bp, MM-exit={r.mm_exit_rate:.2f}')
else:lines.append('No completed cycles even at the front-of-queue upper bound.')
lines+=['','A positive short probe is only a candidate. Longer prospective capture is required before risking capital.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines))
