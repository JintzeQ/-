import asyncio, json, math, os, time, urllib.parse, urllib.request
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BBUSDT','ROBOUSDT','RAREUSDT']
CAPTURE_SECONDS=180
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
GRID_MS=250
OUT='maker_live_output'
os.makedirs(OUT,exist_ok=True)

streams=[f'{s.lower()}@bookTicker' for s in SYMBOLS]
WS_URL='wss://fstream.binance.com/stream?streams=' + '/'.join(streams)
REST_TRADES='https://fapi.binance.com/fapi/v1/trades'
books=defaultdict(list)
trades=defaultdict(list)
last_ids={}


def fetch_recent(symbol):
    q=urllib.parse.urlencode({'symbol':symbol,'limit':1000})
    req=urllib.request.Request(REST_TRADES+'?'+q,headers={'User-Agent':'maker-probe/1.0'})
    with urllib.request.urlopen(req,timeout=8) as r:
        return json.loads(r.read().decode())


async def poll_trades(deadline):
    while time.monotonic()<deadline:
        for s in SYMBOLS:
            try:
                rows=await asyncio.to_thread(fetch_recent,s)
                if not rows: continue
                if s not in last_ids:
                    last_ids[s]=int(rows[-1]['id'])
                    continue
                old=last_ids[s]
                for x in rows:
                    tid=int(x['id'])
                    if tid<=old: continue
                    # RPI executions do not deplete the visible API maker queue; exclude them.
                    if not bool(x.get('isRPITrade',False)):
                        trades[s].append((tid,int(x['time']),float(x['price']),float(x['qty']),bool(x['isBuyerMaker'])))
                last_ids[s]=max(old,int(rows[-1]['id']))
            except Exception as e:
                print('trade poll error',s,repr(e),flush=True)
        await asyncio.sleep(0.5)


async def capture_books(deadline):
    async with websockets.connect(WS_URL,ping_interval=15,ping_timeout=15,max_queue=200000) as ws:
        while time.monotonic()<deadline:
            try:
                msg=await asyncio.wait_for(ws.recv(),timeout=5)
            except asyncio.TimeoutError:
                continue
            obj=json.loads(msg); d=obj.get('data',obj); s=d.get('s')
            if s not in SYMBOLS: continue
            if all(k in d for k in ('b','B','a','A')):
                et=d.get('T',d.get('E'))
                if et is None: et=int(time.time()*1000)
                books[s].append((int(et),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))


async def capture():
    deadline=time.monotonic()+CAPTURE_SECONDS
    await asyncio.gather(capture_books(deadline),poll_trades(deadline))


def to_frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq'])
    t=pd.DataFrame(trades[s],columns=['trade_id','ts','price','qty','buyer_maker'])
    if len(b):
        b=b.sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
        b['mid']=(b.bid+b.ask)/2
        b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
        b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    if len(t):
        t=t.sort_values(['ts','trade_id']).drop_duplicates('trade_id').reset_index(drop=True)
    return b,t


def bbo_at(b,ts):
    if len(b)==0:return None
    a=b.ts.values; i=np.searchsorted(a,ts,side='right')-1
    return None if i<0 else b.iloc[i]


def flow_at(t,ts,lookback=1000):
    if len(t)==0:return 0.0
    a=t.ts.values; lo=np.searchsorted(a,ts-lookback,'left'); hi=np.searchsorted(a,ts,'right')
    x=t.iloc[lo:hi]
    if len(x)==0:return 0.0
    n=x.price.values*x.qty.values
    signed=np.where(x.buyer_maker.values,-n,n)
    return float(signed.sum()/(n.sum()+1e-12))


def fill_time(t,start,end,side,px,queue,qmult):
    if len(t)==0:return None
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    done=0.0; need=max(float(queue)*qmult,0.0)
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>need:return int(r.ts)
    return None


def markout(b,fill_ts,side,px,h):
    r=bbo_at(b,fill_ts+h)
    if r is None:return np.nan
    return ((float(r.mid)-px)/px*1e4) if side=='buy' else ((px-float(r.mid))/px*1e4)


def simulate(b,t,lat_ms,spread_floor,flow_cap,qmin,qmult,entry_ttl_ms,exit_ttl_ms):
    if len(b)<2 or len(t)==0:return pd.DataFrame(),0
    start=max(int(b.ts.iloc[0]),int(t.ts.iloc[0])); end=min(int(b.ts.iloc[-1]),int(t.ts.iloc[-1]))
    decisions=np.arange(((start+GRID_MS-1)//GRID_MS)*GRID_MS,end,GRID_MS,dtype=np.int64)
    out=[]; attempts=0; next_free=start
    for sig in decisions:
        if sig<next_free:continue
        r=bbo_at(b,sig)
        if r is None or r.spread_bp<spread_floor:continue
        fl=flow_at(t,sig)
        choices=[]
        if r.qimb>=qmin and fl>=-flow_cap:choices.append(('buy',float(r.qimb)))
        if r.qimb<=-qmin and fl<=flow_cap:choices.append(('sell',float(-r.qimb)))
        if not choices:continue
        side=max(choices,key=lambda z:z[1])[0]; attempts+=1
        place=sig+lat_ms; p=bbo_at(b,place)
        if p is None:continue
        px=float(p.bid if side=='buy' else p.ask); q=float(p.bidq if side=='buy' else p.askq)
        ft=fill_time(t,place,min(place+entry_ttl_ms,end),side,px,q,qmult)
        if ft is None:continue
        m1=markout(b,ft,side,px,1000); m5=markout(b,ft,side,px,5000); m15=markout(b,ft,side,px,15000)
        exit_start=ft+lat_ms; er=bbo_at(b,exit_start)
        if er is None:continue
        eside='sell' if side=='buy' else 'buy'
        epx=float(er.ask if eside=='sell' else er.bid); eq=float(er.askq if eside=='sell' else er.bidq)
        eft=fill_time(t,exit_start,min(exit_start+exit_ttl_ms,end),eside,epx,eq,qmult)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fee=2*MAKER_FEE_BP; mode='MM'; close=eft
        else:
            close=min(exit_start+exit_ttl_ms,end); cr=bbo_at(b,close)
            if cr is None:continue
            cpx=float(cr.bid if side=='buy' else cr.ask)
            gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fee=MAKER_FEE_BP+TAKER_FEE_BP; mode='MT'
        out.append({'signal_ts':sig,'side':side,'spread_signal_bp':float(r.spread_bp),'flow1s':fl,'qimb':float(r.qimb),'fill_ts':ft,'markout1_bp':m1,'markout5_bp':m5,'markout15_bp':m15,'gross_bp':gross,'fees_bp':fee,'net_bp':gross-fee,'exit_mode':mode})
        next_free=close
    return pd.DataFrame(out),attempts


def summarize(s,b,t):
    if len(b)==0:return [],{}
    diag={'symbol':s,'book_events':len(b),'trade_events':len(t),'seconds':(b.ts.iloc[-1]-b.ts.iloc[0])/1000,
          'median_spread_bp':float(b.spread_bp.median()),'p25_spread_bp':float(b.spread_bp.quantile(.25)),
          'p75_spread_bp':float(b.spread_bp.quantile(.75)),'p90_spread_bp':float(b.spread_bp.quantile(.90))}
    floors=sorted(set([4.0,6.0,8.0,10.0,12.0,round(diag['median_spread_bp'],1)]))
    rows=[]
    for lat in [10,240]:
      for floor in floors:
       for flowcap in [1.0,0.5,0.2]:
        for qmin in [0.0,0.2,0.5]:
         for qmult in [1.0,1.5]:
          for entry_ttl in [2000,5000]:
           for exit_ttl in [10000,30000]:
            d,attempts=simulate(b,t,lat,floor,flowcap,qmin,qmult,entry_ttl,exit_ttl)
            if len(d)==0:continue
            rows.append({'symbol':s,'lat_ms':lat,'spread_floor':floor,'flow_cap':flowcap,'qmin':qmin,'qmult':qmult,
                         'entry_ttl_s':entry_ttl/1000,'exit_ttl_s':exit_ttl/1000,'attempts':attempts,'n':len(d),
                         'fill_rate':len(d)/max(attempts,1),'mean_signal_spread_bp':d.spread_signal_bp.mean(),
                         'mean_markout1_bp':d.markout1_bp.mean(),'mean_markout5_bp':d.markout5_bp.mean(),'mean_markout15_bp':d.markout15_bp.mean(),
                         'mm_exit_rate':(d.exit_mode=='MM').mean(),'mean_gross_bp':d.gross_bp.mean(),'mean_net_bp':d.net_bp.mean(),
                         'total_net_bp':d.net_bp.sum(),'win_net':(d.net_bp>=0).mean()})
    return rows,diag

asyncio.run(capture())
allrows=[]; diags=[]
for s in SYMBOLS:
    b,t=to_frames(s); rows,diag=summarize(s,b,t); allrows+=rows; diags.append(diag)
    b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
res=pd.DataFrame(allrows)
ranked=res[res.n>=3].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(res) else res
ranked.to_csv(f'{OUT}/results.csv',index=False); pd.DataFrame(diags).to_csv(f'{OUT}/diagnostics.csv',index=False)
lines=['# Hybrid live maker probe','',f'- Capture: {CAPTURE_SECONDS}s','- Symbols: '+', '.join(SYMBOLS),'- BBO: futures bookTicker WebSocket; trades: REST recent-trades polling with exchange timestamps; RPI trades excluded.',f'- Fees: maker {MAKER_FEE_BP} bp/side; emergency taker {TAKER_FEE_BP} bp/side','- Fill: full displayed L1 queue ahead (x1.0/x1.5), no cancellation credit; aggressive trade-through required.','- Latency: 10ms and 240ms order-to-exchange; market-data receive latency not modeled.','']
lines+=['## Diagnostics','','| symbol | BBO events | trades captured | median spread bp | p75 | p90 |','|---|---:|---:|---:|---:|---:|']
for d in diags:
    if d:lines.append(f"| {d['symbol']} | {d['book_events']} | {d['trade_events']} | {d['median_spread_bp']:.2f} | {d['p75_spread_bp']:.2f} | {d['p90_spread_bp']:.2f} |")
lines+=['','## Net-positive configs (>=3 completed cycles)','']
pos=ranked[ranked.mean_net_bp>=0].head(15) if len(ranked) else ranked
if len(pos):
    lines+=['| symbol | lat | floor | flowcap | qmin | qmult | entry/exit ttl | n | fill rate | net bp | MM exit | 1s markout |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in pos.itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.spread_floor:.1f} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {r.entry_ttl_s:.0f}/{r.exit_ttl_s:.0f}s | {int(r.n)} | {r.fill_rate:.3f} | {r.mean_net_bp:.2f} | {r.mm_exit_rate:.2f} | {r.mean_markout1_bp:.2f} |')
else:lines.append('None.')
lines+=['','## Best configs regardless of sign','']
if len(ranked):
    lines+=['| symbol | lat | floor | qmin | qmult | n | fill rate | net bp | gross bp | MM exit | 1s markout |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in ranked.head(12).itertuples():lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.spread_floor:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {int(r.n)} | {r.fill_rate:.3f} | {r.mean_net_bp:.2f} | {r.mean_gross_bp:.2f} | {r.mm_exit_rate:.2f} | {r.mean_markout1_bp:.2f} |')
else:lines.append('No completed cycles.')
lines+=['','Short prospective probe only. Hidden liquidity, exact queue priority, own size, and market-data delivery latency remain unmodeled; positive results are candidates, not deployment proof.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines))
