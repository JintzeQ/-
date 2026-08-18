import asyncio, json, os, time, math
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS={'FETUSDT':0.0001,'OPUSDT':0.0001,'WIFUSDT':0.0001}
CAPTURE_SECONDS=180
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
ORDER_NOTIONAL=100.0
OUT='maker_active_output'
os.makedirs(OUT,exist_ok=True)

streams=[]
for s in SYMBOLS:
    x=s.lower(); streams += [f'{x}@bookTicker', f'{x}@aggTrade']
URL='wss://fstream.binance.com/stream?streams=' + '/'.join(streams)
books=defaultdict(list); trades=defaultdict(list)

async def capture():
    stop=time.monotonic()+CAPTURE_SECONDS
    async with websockets.connect(URL,ping_interval=15,ping_timeout=15,max_queue=200000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),timeout=5)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',{})
            s=d.get('s')
            if s not in SYMBOLS: continue
            if d.get('e')=='aggTrade':
                trades[s].append((int(d.get('T',d['E'])),float(d['p']),float(d['q']),bool(d['m'])))
            elif all(k in d for k in ('b','B','a','A')):
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

def frame(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last')
    t=pd.DataFrame(trades[s],columns=['ts','price','qty','buyer_maker']).sort_values('ts')
    if len(b):
        b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
        b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    return b.reset_index(drop=True),t.reset_index(drop=True)

def at(b,ts):
    if len(b)==0:return None
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]

def recent_flow(t,ts,ms=1000):
    if len(t)==0:return 0.0,0
    a=t.ts.values; lo=np.searchsorted(a,ts-ms,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.0,0
    n=x.price*x.qty; sg=np.where(x.buyer_maker,-n,n)
    return float(sg.sum()/(n.sum()+1e-12)),len(x)

def activity(t,ts,ms=5000):
    if len(t)==0:return 0
    a=t.ts.values; lo=np.searchsorted(a,ts-ms,'left'); hi=np.searchsorted(a,ts,'right')
    if hi<=lo:return 0
    return int(t.iloc[lo:hi].ts.nunique())

def fill(t,start,end,side,px,queue_ahead,order_qty,qmult):
    if len(t)==0:return None
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    need=max(queue_ahead*qmult+order_qty,order_qty); done=0.0
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None

def quote_price(r,side,tick,mode):
    spread=r.ask-r.bid
    if mode=='improve' and spread>=2*tick-1e-15:
        return (r.bid+tick,0.0) if side=='buy' else (r.ask-tick,0.0)
    return (r.bid,r.bidq) if side=='buy' else (r.ask,r.askq)

def markout(b,ts,side,px,h):
    r=at(b,ts+h)
    if r is None:return np.nan
    return ((r.mid-px)/px*1e4) if side=='buy' else ((px-r.mid)/px*1e4)

def simulate(s,b,t,lat,activity_min,flow_cap,qmin,qmult,mode):
    if len(b)<2 or len(t)==0:return pd.DataFrame()
    tick=SYMBOLS[s]; start=int(max(b.ts.min(),t.ts.min())); end=int(min(b.ts.max(),t.ts.max()))
    sigs=np.arange(((start+249)//250)*250,end,250,dtype=np.int64); out=[]; free=start
    for sig in sigs:
        if sig<free or activity(t,sig)<activity_min:continue
        r=at(b,sig)
        if r is None or r.spread_bp<4.25:continue
        fl,_=recent_flow(t,sig)
        choices=[]
        if r.qimb>=qmin and fl>=-flow_cap: choices.append(('buy',r.qimb))
        if r.qimb<=-qmin and fl<=flow_cap: choices.append(('sell',-r.qimb))
        if not choices:continue
        side=max(choices,key=lambda z:z[1])[0]
        place=sig+lat; p=at(b,place)
        if p is None:continue
        px,q=quote_price(p,side,tick,mode); oq=ORDER_NOTIONAL/px
        ft=fill(t,place,min(place+3000,end),side,px,q,oq,qmult)
        if ft is None:continue
        m1=markout(b,ft,side,px,1000); m5=markout(b,ft,side,px,5000)
        ex_start=ft+lat; er=at(b,ex_start)
        if er is None:continue
        es='sell' if side=='buy' else 'buy'; epx,eq=quote_price(er,es,tick,mode); eoq=ORDER_NOTIONAL/epx
        eft=fill(t,ex_start,min(ex_start+15000,end),es,epx,eq,eoq,qmult)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fee=4.0; exitmode='MM'; close=eft
        else:
            close=min(ex_start+15000,end); cr=at(b,close)
            if cr is None:continue
            cpx=cr.bid if side=='buy' else cr.ask
            gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fee=7.0; exitmode='MT'
        out.append({'symbol':s,'lat_ms':lat,'activity_min':activity_min,'flow_cap':flow_cap,'qmin':qmin,'qmult':qmult,'mode':mode,'side':side,'spread_bp':r.spread_bp,'markout1_bp':m1,'markout5_bp':m5,'gross_bp':gross,'fees_bp':fee,'net_bp':gross-fee,'exit_mode':exitmode,'fill_ts':ft,'close_ts':close})
        free=close
    return pd.DataFrame(out)

asyncio.run(capture())
rows=[]; diagnostics=[]
for s in SYMBOLS:
    b,t=frame(s); b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
    if len(b): diagnostics.append({'symbol':s,'book_events':len(b),'trade_events':len(t),'distinct_trade_times':int(t.ts.nunique()) if len(t) else 0,'median_spread_bp':b.spread_bp.median(),'p75_spread_bp':b.spread_bp.quantile(.75)})
    for lat in [10,240]:
      for amin in [1,2,4]:
       for fc in [1.0,0.5,0.2]:
        for qm in [0.0,0.2]:
         for qmul in [1.0,1.5]:
          for mode in ['join','improve']:
            d=simulate(s,b,t,lat,amin,fc,qm,qmul,mode)
            if len(d)==0:continue
            rows.append({'symbol':s,'lat_ms':lat,'activity_min':amin,'flow_cap':fc,'qmin':qm,'qmult':qmul,'mode':mode,'n':len(d),'mean_spread_bp':d.spread_bp.mean(),'mean_markout1_bp':d.markout1_bp.mean(),'mean_markout5_bp':d.markout5_bp.mean(),'mm_exit_rate':(d.exit_mode=='MM').mean(),'mean_gross_bp':d.gross_bp.mean(),'mean_net_bp':d.net_bp.mean(),'total_net_bp':d.net_bp.sum(),'win_net':(d.net_bp>=0).mean()})
res=pd.DataFrame(rows); diag=pd.DataFrame(diagnostics)
diag.to_csv(f'{OUT}/diagnostics.csv',index=False); res.to_csv(f'{OUT}/results.csv',index=False)
rank=res[res.n>=3].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(res) else res
pos=rank[rank.mean_net_bp>=0] if len(rank) else rank
lines=['# Active wide-tick maker probe','',f'- Capture {CAPTURE_SECONDS}s; order size ${ORDER_NOTIONAL:.0f}','- Symbols: FETUSDT, OPUSDT, WIFUSDT','- Conservative L1 queue-ahead; no cancellation credit; own order size added behind queue.','- Maker/maker = 4bp fees; maker/taker emergency unwind = 7bp.','- Activity gate uses distinct trade timestamps in prior 5s.','- Latency: 10ms and 240ms order-to-exchange; market-data receive latency not modeled.','', '## Diagnostics','', '| symbol | book events | trades | distinct trade times | median spread bp | p75 |','|---|---:|---:|---:|---:|---:|']
for r in diagnostics:lines.append(f"| {r['symbol']} | {r['book_events']} | {r['trade_events']} | {r['distinct_trade_times']} | {r['median_spread_bp']:.2f} | {r['p75_spread_bp']:.2f} |")
lines+=['','## Net-positive completed-cycle configs (n>=3)','']
if len(pos):
    lines+=['| symbol | lat | activity | flowcap | qmin | qmult | mode | n | net bp | MM exit | 1s markout |','|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|']
    for r in pos.head(15).itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.activity_min} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {r.mode} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mm_exit_rate:.2f} | {r.mean_markout1_bp:.2f} |')
else:lines.append('None.')
lines+=['','## Best regardless of sign','']
if len(rank):
    lines+=['| symbol | lat | activity | flowcap | qmin | qmult | mode | n | net bp | gross bp | MM exit |','|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|']
    for r in rank.head(12).itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.activity_min} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {r.mode} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mean_gross_bp:.2f} | {r.mm_exit_rate:.2f} |')
else:lines.append('No completed cycles.')
lines+=['','Short prospective sample only. A positive configuration is a candidate, not deployment proof; exact exchange queue priority, hidden/RPI liquidity, feed latency and our own cancel/replace behavior remain unmodeled.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines))
