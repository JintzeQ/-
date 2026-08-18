import asyncio, json, os, time
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BTCUSDC','ETHUSDC','SOLUSDC','XRPUSDC','DOGEUSDC','LINKUSDC','AVAXUSDC','LTCUSDC']
CAPTURE=180
STEP_MS=250
ENTRY_TTL_MS=3000
ORDER_USD=70.0
MAKER_FEE_BP=0.0
TAKER_FEE_BP=4.0
OUT='usdc_maker_output'
os.makedirs(OUT,exist_ok=True)
books=defaultdict(list); trades=defaultdict(list)
BOOK='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
TRADE='wss://fstream.binance.com/market/stream?streams='+'/'.join(f'{s.lower()}@aggTrade' for s in SYMBOLS)

async def collect_book(stop):
    async with websockets.connect(BOOK,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),2)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',{})
            s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                ts=int(d.get('T',d.get('E')))
                books[s].append((ts,float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def collect_trade(stop):
    async with websockets.connect(TRADE,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),2)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',{})
            s=d.get('s')
            if s in SYMBOLS and d.get('e')=='aggTrade':
                trades[s].append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))

async def capture():
    stop=time.monotonic()+CAPTURE
    await asyncio.gather(collect_book(stop),collect_trade(stop))


def frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq'])
    t=pd.DataFrame(trades[s],columns=['ts','price','qty','buyer_maker'])
    if len(b):
        b=b.sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
        b['mid']=(b.bid+b.ask)/2
        b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
        b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    if len(t): t=t.sort_values('ts').reset_index(drop=True)
    return b,t


def at(b,ts):
    if len(b)==0:return None
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]


def flow(t,ts,ms):
    if len(t)==0:return 0.0,0
    a=t.ts.values; lo=np.searchsorted(a,ts-ms,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.0,0
    n=(x.price*x.qty).values
    signed=np.where(x.buyer_maker.values,-n,n)
    return float(signed.sum()/(n.sum()+1e-12)),len(x)


def fill(t,start,end,side,px,queue_ahead,own_qty,queue_mult):
    if len(t)==0:return None
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    need=max(float(queue_ahead)*queue_mult+float(own_qty),float(own_qty)); done=0.0
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None


def markout(b,ts,side,px,h):
    r=at(b,ts+h)
    if r is None:return np.nan
    return ((float(r.mid)-px)/px*1e4) if side=='buy' else ((px-float(r.mid))/px*1e4)


def eligible(profile,side,f1,f5,qimb):
    if profile=='all': return True
    if profile=='flow_protected':
        return (side=='buy' and f1>=-0.15 and f5>=-0.20) or (side=='sell' and f1<=0.15 and f5<=0.20)
    if profile=='neutral': return abs(f1)<=0.20 and abs(f5)<=0.25
    if profile=='queue_support': return (side=='buy' and qimb>=0.10) or (side=='sell' and qimb<=-0.10)
    return False


def scan_fills(s,b,t,lat,profile,qmult):
    if len(b)<2 or len(t)<2:return pd.DataFrame(),0
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max()))
    next_free={'buy':start,'sell':start}; out=[]; attempts=0
    for sig in np.arange(((start+STEP_MS-1)//STEP_MS)*STEP_MS,end,STEP_MS,dtype=np.int64):
        r=at(b,sig)
        if r is None:continue
        f1,_=flow(t,sig,1000); f5,_=flow(t,sig,5000)
        for side in ['buy','sell']:
            if sig<next_free[side] or not eligible(profile,side,f1,f5,float(r.qimb)):continue
            p=at(b,sig+lat)
            if p is None:continue
            px=float(p.bid if side=='buy' else p.ask)
            q=float(p.bidq if side=='buy' else p.askq)
            own=ORDER_USD/px; attempts+=1
            ft=fill(t,sig+lat,min(sig+lat+ENTRY_TTL_MS,end),side,px,q,own,qmult)
            if ft is None:
                next_free[side]=sig+ENTRY_TTL_MS;continue
            out.append({'symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'side':side,'signal_ts':sig,'fill_ts':ft,
                        'entry_px':px,'spread_bp':float(p.spread_bp),'qimb':float(p.qimb),'flow1':f1,'flow5':f5,
                        'mark1_bp':markout(b,ft,side,px,1000),'mark5_bp':markout(b,ft,side,px,5000),'mark15_bp':markout(b,ft,side,px,15000)})
            next_free[side]=ft+5000
    return pd.DataFrame(out),attempts


def roundtrip(s,b,t,lat,profile,qmult,exit_ttl):
    if len(b)<2 or len(t)<2:return pd.DataFrame()
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max())); free=start; out=[]
    for sig in np.arange(((start+STEP_MS-1)//STEP_MS)*STEP_MS,end,STEP_MS,dtype=np.int64):
        if sig<free:continue
        r=at(b,sig)
        if r is None:continue
        f1,_=flow(t,sig,1000); f5,_=flow(t,sig,5000)
        choices=[]
        for side in ['buy','sell']:
            if eligible(profile,side,f1,f5,float(r.qimb)):
                score=float(r.qimb) if side=='buy' else -float(r.qimb); choices.append((score,side))
        if not choices:continue
        side=max(choices,key=lambda z:z[0])[1]
        p=at(b,sig+lat)
        if p is None:continue
        entry_px=float(p.bid if side=='buy' else p.ask); entry_q=float(p.bidq if side=='buy' else p.askq); own=ORDER_USD/entry_px
        ft=fill(t,sig+lat,min(sig+lat+ENTRY_TTL_MS,end),side,entry_px,entry_q,own,qmult)
        if ft is None:continue
        er=at(b,ft+lat)
        if er is None:continue
        exit_side='sell' if side=='buy' else 'buy'
        exit_px=float(er.ask if side=='buy' else er.bid); exit_q=float(er.askq if side=='buy' else er.bidq); exit_own=ORDER_USD/exit_px
        eft=fill(t,ft+lat,min(ft+lat+exit_ttl,end),exit_side,exit_px,exit_q,exit_own,qmult)
        mm=eft is not None
        if mm:
            close_ts=eft; close_px=exit_px; fee=0.0
        else:
            close_ts=min(ft+lat+exit_ttl,end); cr=at(b,close_ts)
            if cr is None:continue
            close_px=float(cr.bid if side=='buy' else cr.ask); fee=TAKER_FEE_BP
        gross=((close_px-entry_px)/entry_px*1e4)*(1 if side=='buy' else -1)
        out.append({'symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'exit_ttl_ms':exit_ttl,'side':side,
                    'entry_spread_bp':float(p.spread_bp),'mm_exit':mm,'gross_bp':gross,'fee_bp':fee,'net_bp':gross-fee,
                    'entry_fill_ts':ft,'close_ts':close_ts})
        free=close_ts
    return pd.DataFrame(out)

asyncio.run(capture())
summary=[]; fill_rows=[]; cycle_rows=[]; diag=[]
for s in SYMBOLS:
    b,t=frames(s)
    b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
    if len(b):
        diag.append({'symbol':s,'book_events':len(b),'agg_trades':len(t),'trade_ts':int(t.ts.nunique()) if len(t) else 0,
                     'median_spread_bp':float(b.spread_bp.median()),'p75_spread_bp':float(b.spread_bp.quantile(.75)),
                     'median_bid_usd':float((b.bid*b.bidq).median()),'median_ask_usd':float((b.ask*b.askq).median())})
    for lat in [10,240]:
      for profile in ['all','flow_protected','neutral','queue_support']:
       for qmult in [0.5,1.0]:
        d,att=scan_fills(s,b,t,lat,profile,qmult)
        if len(d):
            fill_rows.append(d)
            summary.append({'kind':'fill','symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'exit_ttl_ms':0,
                            'attempts':att,'n':len(d),'fill_rate':len(d)/max(att,1),'mean_spread_bp':d.spread_bp.mean(),
                            'mark1_bp':d.mark1_bp.mean(),'mark5_bp':d.mark5_bp.mean(),'mark15_bp':d.mark15_bp.mean(),
                            'mm_exit_rate':np.nan,'mean_net_bp':d.mark5_bp.mean(),'positive_rate':(d.mark5_bp>=0).mean()})
        for ttl in [3000,10000,30000]:
            c=roundtrip(s,b,t,lat,profile,qmult,ttl)
            if len(c):
                cycle_rows.append(c)
                summary.append({'kind':'cycle','symbol':s,'lat_ms':lat,'profile':profile,'qmult':qmult,'exit_ttl_ms':ttl,
                                'attempts':np.nan,'n':len(c),'fill_rate':np.nan,'mean_spread_bp':c.entry_spread_bp.mean(),
                                'mark1_bp':np.nan,'mark5_bp':np.nan,'mark15_bp':np.nan,
                                'mm_exit_rate':c.mm_exit.mean(),'mean_net_bp':c.net_bp.mean(),'positive_rate':(c.net_bp>=0).mean()})

S=pd.DataFrame(summary); D=pd.DataFrame(diag)
F=pd.concat(fill_rows,ignore_index=True) if fill_rows else pd.DataFrame(); C=pd.concat(cycle_rows,ignore_index=True) if cycle_rows else pd.DataFrame()
S.to_csv(f'{OUT}/summary.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False); F.to_csv(f'{OUT}/fills.csv',index=False); C.to_csv(f'{OUT}/cycles.csv',index=False)

fill_rank=S[(S.kind=='fill')&(S.n>=5)].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(S) else pd.DataFrame()
cycle_rank=S[(S.kind=='cycle')&(S.n>=5)].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(S) else pd.DataFrame()
cycle_pos=cycle_rank[cycle_rank.mean_net_bp>=0] if len(cycle_rank) else cycle_rank
lines=['# USDC zero-maker-fee microstructure probe','',f'- Capture: {CAPTURE}s across {len(SYMBOLS)} USDC USDⓈ-M contracts.','- Assumed account fees from user screenshot: maker 0.0000%, taker 0.0400% (4bp).',f'- Modeled order notional: ${ORDER_USD:.0f}.','- Conservative fill: displayed L1 queue * qmult + own order must be traded through by matching aggressive flow.','- No touch=fill. No cancellation credit. Market-data receive latency, hidden/RPI liquidity, exact queue priority and funding are not modeled.','- `flow_protected`: avoid quoting into same-direction toxic aggressive flow; `neutral`: only low-flow states; `queue_support`: quote only with supportive L1 imbalance.','','## Diagnostics','']
lines.append(D.to_markdown(index=False,floatfmt='.3f') if len(D) else 'No diagnostics.')
lines+=['','## Best 5s passive fill value (maker fee = 0, n>=5)','']
lines.append(fill_rank.head(20).to_markdown(index=False,floatfmt='.3f') if len(fill_rank) else 'No config reached 5 modeled fills.')
lines+=['','## Net-positive completed-cycle configs incl. 4bp emergency taker exits (n>=5)','']
lines.append(cycle_pos.head(20).to_markdown(index=False,floatfmt='.3f') if len(cycle_pos) else 'None.')
lines+=['','## Best completed-cycle configs regardless of sign (n>=5)','']
lines.append(cycle_rank.head(20).to_markdown(index=False,floatfmt='.3f') if len(cycle_rank) else 'No config reached 5 modeled cycles.')
lines+=['','A positive short live sample is only a candidate. Deployment requires longer prospective OOS capture and real exchange acknowledgement/fill telemetry.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
