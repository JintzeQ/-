import asyncio, json, math, os, time
from collections import defaultdict, deque
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['4USDT','PTBUSDT','BBUSDT']
CAPTURE_SECONDS=180
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
GRID_MS=250
OUT='maker_live_output'
os.makedirs(OUT,exist_ok=True)

streams=[]
for s in SYMBOLS:
    x=s.lower()
    streams += [f'{x}@bookTicker', f'{x}@aggTrade']
URL='wss://fstream.binance.com/stream?streams=' + '/'.join(streams)

books=defaultdict(list)
trades=defaultdict(list)

async def capture():
    deadline=time.monotonic()+CAPTURE_SECONDS
    async with websockets.connect(URL, ping_interval=15, ping_timeout=15, max_queue=200000) as ws:
        while time.monotonic()<deadline:
            try:
                msg=await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            obj=json.loads(msg); d=obj.get('data',obj)
            s=d.get('s')
            if s not in SYMBOLS: continue
            et=d.get('T',d.get('E'))
            if 'b' in d and 'a' in d and 'B' in d and 'A' in d and d.get('e')!='aggTrade':
                books[s].append((int(et),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))
            elif d.get('e')=='aggTrade':
                trades[s].append((int(d.get('T',d['E'])),float(d['p']),float(d['q']),bool(d['m'])))


def to_frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last')
    t=pd.DataFrame(trades[s],columns=['ts','price','qty','buyer_maker']).sort_values('ts')
    if len(b)==0: return b,t
    b['mid']=(b.bid+b.ask)/2
    b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
    b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    return b.reset_index(drop=True),t.reset_index(drop=True)


def bbo_at(b,ts):
    a=b.ts.values; i=np.searchsorted(a,ts,side='right')-1
    return None if i<0 else b.iloc[i]


def flow_at(t,ts,lookback=1000):
    if len(t)==0: return 0.0
    a=t.ts.values; lo=np.searchsorted(a,ts-lookback,side='left'); hi=np.searchsorted(a,ts,side='right')
    x=t.iloc[lo:hi]
    if len(x)==0: return 0.0
    notional=x.price*x.qty
    signed=np.where(x.buyer_maker,-notional,notional)
    return float(np.sum(signed)/(np.sum(notional)+1e-12))


def fill_time(t,start,end,side,px,queue,qmult):
    if len(t)==0: return None
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    done=0.0; need=max(float(queue)*qmult,0.0)
    for r in t.iloc[lo:hi].itertuples(index=False):
        # buy maker fills from sell-aggressor: buyer_maker=True; vice versa for sell maker
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done += r.qty
            if done>need: return int(r.ts)
    return None


def markout(b,fill_ts,side,px,horizon_ms):
    r=bbo_at(b,fill_ts+horizon_ms)
    if r is None:return np.nan
    if side=='buy': return (float(r.mid)-px)/px*1e4
    return (px-float(r.mid))/px*1e4


def simulate(b,t,lat_ms,spread_floor,flow_cap,qmin,entry_ttl_ms=2000,exit_ttl_ms=15000,qmult=1.0):
    if len(b)<2:return pd.DataFrame()
    start=int(b.ts.iloc[0]); end=int(b.ts.iloc[-1])
    decisions=np.arange(((start+GRID_MS-1)//GRID_MS)*GRID_MS,end,GRID_MS,dtype=np.int64)
    out=[]; next_free=start
    for sig in decisions:
        if sig<next_free: continue
        r=bbo_at(b,sig)
        if r is None or r.spread_bp<spread_floor: continue
        fl=flow_at(t,sig)
        # Quote the side supported by L1 imbalance, while refusing strongly toxic recent flow.
        choices=[]
        if r.qimb>=qmin and fl>=-flow_cap: choices.append(('buy',float(r.qimb)))
        if r.qimb<=-qmin and fl<=flow_cap: choices.append(('sell',float(-r.qimb)))
        if not choices: continue
        side=max(choices,key=lambda z:z[1])[0]
        place_ts=sig+lat_ms
        p=bbo_at(b,place_ts)
        if p is None: continue
        px=float(p.bid if side=='buy' else p.ask); q=float(p.bidq if side=='buy' else p.askq)
        ft=fill_time(t,place_ts,min(place_ts+entry_ttl_ms,end),side,px,q,qmult)
        if ft is None: continue
        m1=markout(b,ft,side,px,1000); m5=markout(b,ft,side,px,5000); m15=markout(b,ft,side,px,15000)
        exit_start=ft+lat_ms
        er=bbo_at(b,exit_start)
        if er is None:continue
        eside='sell' if side=='buy' else 'buy'
        epx=float(er.ask if eside=='sell' else er.bid); eq=float(er.askq if eside=='sell' else er.bidq)
        eft=fill_time(t,exit_start,min(exit_start+exit_ttl_ms,end),eside,epx,eq,qmult)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1)
            fee=2*MAKER_FEE_BP; mode='MM'; close=eft
        else:
            close=min(exit_start+exit_ttl_ms,end)
            cr=bbo_at(b,close)
            if cr is None: continue
            cpx=float(cr.bid if side=='buy' else cr.ask)
            gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1)
            fee=MAKER_FEE_BP+TAKER_FEE_BP; mode='MT'
        out.append({'signal_ts':sig,'side':side,'spread_signal_bp':float(r.spread_bp),'flow1s':fl,'qimb':float(r.qimb),'fill_ts':ft,'markout1_bp':m1,'markout5_bp':m5,'markout15_bp':m15,'gross_bp':gross,'fees_bp':fee,'net_bp':gross-fee,'exit_mode':mode})
        next_free=close
    return pd.DataFrame(out)


def summarize(s,b,t):
    if len(b)==0:return [],{}
    diag={'symbol':s,'book_events':len(b),'trade_events':len(t),'seconds':(b.ts.iloc[-1]-b.ts.iloc[0])/1000,
          'median_spread_bp':float(b.spread_bp.median()),'p25_spread_bp':float(b.spread_bp.quantile(.25)),'p75_spread_bp':float(b.spread_bp.quantile(.75)),'p90_spread_bp':float(b.spread_bp.quantile(.90))}
    floors=sorted(set([4.0,6.0,8.0,10.0,12.0, round(diag['median_spread_bp'],1)]))
    rows=[]
    for lat in [10,240]:
      for floor in floors:
       for flowcap in [1.0,0.5,0.2]:
        for qmin in [0.0,0.2,0.5]:
         for qmult in [1.0,1.5]:
          d=simulate(b,t,lat,floor,flowcap,qmin,qmult=qmult)
          if len(d)==0:continue
          rows.append({'symbol':s,'lat_ms':lat,'spread_floor':floor,'flow_cap':flowcap,'qmin':qmin,'qmult':qmult,'n':len(d),
                       'mean_signal_spread_bp':d.spread_signal_bp.mean(),'mean_markout1_bp':d.markout1_bp.mean(),'mean_markout5_bp':d.markout5_bp.mean(),'mean_markout15_bp':d.markout15_bp.mean(),
                       'mm_exit_rate':(d.exit_mode=='MM').mean(),'mean_gross_bp':d.gross_bp.mean(),'mean_net_bp':d.net_bp.mean(),'total_net_bp':d.net_bp.sum(),
                       'win_net':(d.net_bp>=0).mean()})
    return rows,diag

asyncio.run(capture())
allrows=[]; diags=[]
for s in SYMBOLS:
    b,t=to_frames(s)
    rows,diag=summarize(s,b,t); allrows+=rows; diags.append(diag)
    b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
res=pd.DataFrame(allrows)
if len(res):
    # Require >=3 completed cycles in this short prospective probe; rank net then turnover.
    ranked=res[res.n>=3].sort_values(['mean_net_bp','n'],ascending=[False,False])
    ranked.to_csv(f'{OUT}/results.csv',index=False)
else:
    ranked=res; res.to_csv(f'{OUT}/results.csv',index=False)
pd.DataFrame(diags).to_csv(f'{OUT}/diagnostics.csv',index=False)
lines=['# Live maker microstructure probe','',f'- Capture: {CAPTURE_SECONDS}s exchange-time BBO bookTicker + aggTrade','- Symbols: '+', '.join(SYMBOLS),f'- Fees: maker {MAKER_FEE_BP} bp/side; emergency taker {TAKER_FEE_BP} bp/side','- Fill model: displayed L1 queue ahead; no cancellation credit; aggressive trade-through required.','- Latency scenarios: 10ms and 240ms order-to-exchange. Market-data delivery latency is NOT modeled.','']
lines+=['## Market diagnostics','', '| symbol | book events | trades | median spread bp | p75 | p90 |','|---|---:|---:|---:|---:|---:|']
for d in diags:
    if d:
        lines.append(f"| {d['symbol']} | {d['book_events']} | {d['trade_events']} | {d['median_spread_bp']:.2f} | {d['p75_spread_bp']:.2f} | {d['p90_spread_bp']:.2f} |")
lines+=['','## Net-positive completed-cycle configurations','']
if len(ranked):
    pos=ranked[ranked.mean_net_bp>=0].head(15)
else: pos=ranked
if len(pos):
    lines+=['| symbol | latency | spread floor | flow cap | qmin | qmult | n | net bp | MM exit | 1s markout |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in pos.itertuples():
        lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.spread_floor:.1f} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mm_exit_rate:.2f} | {r.mean_markout1_bp:.2f} |')
else:
    lines.append('None in this short probe.')
lines+=['','## Best configurations regardless of sign','']
if len(ranked):
    lines+=['| symbol | latency | floor | flowcap | qmin | qmult | n | net bp | gross bp | MM exit |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in ranked.head(12).itertuples():
        lines.append(f'| {r.symbol} | {r.lat_ms}ms | {r.spread_floor:.1f} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.qmult:.1f} | {int(r.n)} | {r.mean_net_bp:.2f} | {r.mean_gross_bp:.2f} | {r.mm_exit_rate:.2f} |')
else: lines.append('No completed cycles.')
lines+=['','This is a prospective microstructure probe, not deployment proof. Short sample size, hidden/RPI liquidity, own-order size, market-data receive latency, and exact exchange queue priority remain unmodeled.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines))
print('\n'.join(lines))
