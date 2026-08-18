import asyncio, json, os, time, math
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BBUSDT','ROBOUSDT','RAREUSDT']
CAPTURE_SECONDS=180
ORDER_NOTIONAL=100.0
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
DECISION_MS=250
ENTRY_TTL_MS=10_000
EXIT_TTL_MS=20_000
OUT='maker_live_v7_output'
os.makedirs(OUT,exist_ok=True)

books=defaultdict(list); trades=defaultdict(list)
book_streams='/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
trade_streams='/'.join(f'{s.lower()}@aggTrade' for s in SYMBOLS)
BOOK_URL='wss://fstream.binance.com/public/stream?streams='+book_streams
TRADE_URL='wss://fstream.binance.com/market/stream?streams='+trade_streams

async def collect_books(stop):
    async with websockets.connect(BOOK_URL,ping_interval=10,ping_timeout=10,max_queue=200000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),timeout=2)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',json.loads(raw)); s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                ts=int(d.get('T',d.get('E')))
                books[s].append((ts,float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def collect_trades(stop):
    async with websockets.connect(TRADE_URL,ping_interval=10,ping_timeout=10,max_queue=200000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),timeout=2)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',json.loads(raw)); s=d.get('s')
            if s in SYMBOLS and d.get('e')=='aggTrade':
                trades[s].append((int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))

async def capture():
    stop=time.monotonic()+CAPTURE_SECONDS
    await asyncio.gather(collect_books(stop),collect_trades(stop))

def frames(s):
    b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last')
    t=pd.DataFrame(trades[s],columns=['ts','price','qty','buyer_maker']).sort_values('ts')
    if len(b):
        b['mid']=(b.bid+b.ask)/2
        b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
        b['qimb']=(b.bidq-b.askq)/(b.bidq+b.askq+1e-12)
    return b.reset_index(drop=True),t.reset_index(drop=True)

def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,side='right')-1
    return None if i<0 else b.iloc[i]

def flow_stats(t,ts,lookback=5000):
    a=t.ts.values; lo=np.searchsorted(a,ts-lookback,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.0,0.0,0.0,0
    n=(x.price*x.qty).values; sign=np.where(x.buyer_maker.values,-1.,1.)
    flow=float((n*sign).sum()/(n.sum()+1e-12)); secs=lookback/1000
    sell=float(x.loc[x.buyer_maker,'qty'].sum()/secs); buy=float(x.loc[~x.buyer_maker,'qty'].sum()/secs)
    return flow,sell,buy,len(x)

def fill_time(t,start,end,side,px,queue_ahead,own_qty):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    need=max(float(queue_ahead)+float(own_qty),float(own_qty)); done=0.
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

PROFILES={
 'loose': {'max_qsec':10.0,'tox':1.0,'min_buffer':0.0},
 'balanced': {'max_qsec':5.0,'tox':0.50,'min_buffer':1.0},
 'strict': {'max_qsec':2.0,'tox':0.25,'min_buffer':2.0},
}

def simulate(s,b,t,lat,profile):
    p=PROFILES[profile]
    if len(b)<2 or len(t)<2:return pd.DataFrame()
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max()))
    if end<=start:return pd.DataFrame()
    sigs=np.arange(((start+DECISION_MS-1)//DECISION_MS)*DECISION_MS,end,DECISION_MS,dtype=np.int64)
    out=[]; free=start
    for sig in sigs:
        if sig<free:continue
        r=at(b,sig)
        if r is None:continue
        buffer=float(r.spread_bp)-4.0
        if buffer<p['min_buffer']:continue
        fl,sell_rate,buy_rate,nev=flow_stats(t,sig,5000)
        if nev<2:continue
        qsec_buy=float(r.bidq)/(sell_rate+1e-12); qsec_sell=float(r.askq)/(buy_rate+1e-12)
        choices=[]
        if qsec_buy<=p['max_qsec'] and fl>=-p['tox']:choices.append(('buy',qsec_buy))
        if qsec_sell<=p['max_qsec'] and fl<=p['tox']:choices.append(('sell',qsec_sell))
        if not choices:continue
        side=min(choices,key=lambda z:z[1])[0]
        place=sig+lat; pr=at(b,place)
        if pr is None:continue
        px=float(pr.bid if side=='buy' else pr.ask); qa=float(pr.bidq if side=='buy' else pr.askq); oq=ORDER_NOTIONAL/px
        ft=fill_time(t,place,min(place+ENTRY_TTL_MS,end),side,px,qa,oq)
        if ft is None:continue
        m1=markout(b,ft,side,px,1000); m5=markout(b,ft,side,px,5000)
        estart=ft+lat; er=at(b,estart)
        if er is None:continue
        eside='sell' if side=='buy' else 'buy'; epx=float(er.ask if eside=='sell' else er.bid); eq=float(er.askq if eside=='sell' else er.bidq); eoq=ORDER_NOTIONAL/epx
        eft=fill_time(t,estart,min(estart+EXIT_TTL_MS,end),eside,epx,eq,eoq)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fees=4.; mode='MM'; close=eft
        else:
            close=min(estart+EXIT_TTL_MS,end); cr=at(b,close)
            if cr is None:continue
            cpx=float(cr.bid if side=='buy' else cr.ask); gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fees=7.; mode='MT'
        out.append({'symbol':s,'profile':profile,'lat_ms':lat,'signal_ts':sig,'fill_ts':ft,'close_ts':close,'side':side,
                    'entry':px,'signal_spread_bp':float(r.spread_bp),'qsec_buy':qsec_buy,'qsec_sell':qsec_sell,'flow5s':fl,
                    'markout1_bp':m1,'markout5_bp':m5,'gross_bp':gross,'fees_bp':fees,'net_bp':gross-fees,'exit_mode':mode})
        free=close
    return pd.DataFrame(out)

asyncio.run(capture())
rows=[]; diagnostics=[]; allcycles=[]
for s in SYMBOLS:
    b,t=frames(s); b.to_csv(f'{OUT}/{s}_book.csv',index=False); t.to_csv(f'{OUT}/{s}_trades.csv',index=False)
    if len(b):
        diagnostics.append({'symbol':s,'book_events':len(b),'agg_trades':len(t),'median_spread_bp':float(b.spread_bp.median()),
                            'p25_spread_bp':float(b.spread_bp.quantile(.25)),'p75_spread_bp':float(b.spread_bp.quantile(.75)),
                            'median_bid_notional':float((b.bid*b.bidq).median()),'median_ask_notional':float((b.ask*b.askq).median())})
    for lat in [10,240]:
      for profile in PROFILES:
        d=simulate(s,b,t,lat,profile)
        if len(d):
            allcycles.append(d); x=d.net_bp.values; n=len(x); se=x.std(ddof=1)/math.sqrt(n) if n>1 else np.nan
            rows.append({'symbol':s,'lat_ms':lat,'profile':profile,'n':n,'mean_net_bp':float(x.mean()),'total_net_bp':float(x.sum()),
                         'mean_gross_bp':float(d.gross_bp.mean()),'mm_exit_rate':float((d.exit_mode=='MM').mean()),'win_net':float((d.net_bp>=0).mean()),
                         'mean_markout1_bp':float(d.markout1_bp.mean()),'mean_markout5_bp':float(d.markout5_bp.mean()),
                         'ci95_low_net':float(x.mean()-1.96*se) if np.isfinite(se) else np.nan,'notional_roundtrip_usd':float(2*ORDER_NOTIONAL*n)})
R=pd.DataFrame(rows); D=pd.DataFrame(diagnostics); C=pd.concat(allcycles,ignore_index=True) if allcycles else pd.DataFrame()
R.to_csv(f'{OUT}/summary.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False); C.to_csv(f'{OUT}/cycles.csv',index=False)
valid=R[R.n>=2].sort_values(['mean_net_bp','n'],ascending=[False,False]) if len(R) else R; pos=valid[valid.mean_net_bp>=0] if len(valid) else valid
lines=['# BB/ROBO/RARE maker v7 — prospective queue-turnover probe','',f'- Capture: {CAPTURE_SECONDS}s; order $100.','- BBO from Futures public stream; aggTrades from Futures market stream.','- Displayed L1 queue + own order must be traded through; no cancellation credit.','- Fees: maker/maker 4bp; maker/taker emergency exit 7bp.','- Latency: 10ms and 240ms. Profiles fixed ex ante.','', '## Diagnostics','']
if len(D):lines.append(D.to_markdown(index=False,floatfmt='.3f'))
lines+=['','## Net-positive configs (n>=2)','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
lines+=['','## Best regardless of sign','']
if len(valid):lines.append(valid.head(12).to_markdown(index=False,floatfmt='.3f'))
else:lines.append('No config completed at least two cycles.')
lines+=['','Short prospective candidate screen only; exact queue priority, hidden/RPI liquidity, market-data receive latency and cancel/replace behavior remain unmodeled.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
