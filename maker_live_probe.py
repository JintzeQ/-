import asyncio, json, math, os, time, urllib.parse, urllib.request
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BMTUSDT','PORTALUSDT','BEATUSDT','ALICEUSDT','BBUSDT']
CAPTURE_SECONDS=300
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
                lid=last_ids.get(s,-1)
                fresh=[r for r in rows if int(r['id'])>lid]
                if fresh:
                    fresh.sort(key=lambda r:int(r['id']))
                    for r in fresh:
                        trades[s].append({'ts':int(r['time']),'price':float(r['price']),'qty':float(r['qty']),'isBuyerMaker':bool(r['isBuyerMaker'])})
                    last_ids[s]=max(int(r['id']) for r in fresh)
            except Exception as e:
                print('trade poll',s,repr(e))
        await asyncio.sleep(0.20)


async def capture():
    now_ms=int(time.time()*1000)
    for s in SYMBOLS:
        try:
            rows=await asyncio.to_thread(fetch_recent,s)
            rows=[r for r in rows if int(r['time'])>=now_ms-1000]
            if rows:
                last_ids[s]=max(int(r['id']) for r in rows)
                for r in rows:
                    trades[s].append({'ts':int(r['time']),'price':float(r['price']),'qty':float(r['qty']),'isBuyerMaker':bool(r['isBuyerMaker'])})
        except Exception as e: print('seed',s,repr(e))
    deadline=time.monotonic()+CAPTURE_SECONDS
    poll=asyncio.create_task(poll_trades(deadline))
    async with websockets.connect(WS_URL,ping_interval=15,ping_timeout=15,max_queue=None) as ws:
        while time.monotonic()<deadline:
            try:
                msg=await asyncio.wait_for(ws.recv(),timeout=5)
                x=json.loads(msg)['data']; s=x['s']; ts=int(x.get('T') or x.get('E'))
                b=float(x['b']); a=float(x['a']); bq=float(x['B']); aq=float(x['A'])
                if a>b>0:
                    mid=(a+b)/2; books[s].append({'ts':ts,'bid':b,'bidq':bq,'ask':a,'askq':aq,'mid':mid,'spread_bp':(a-b)/mid*1e4,'qimb':(bq-aq)/(bq+aq) if bq+aq else 0})
            except asyncio.TimeoutError: pass
    await poll


def analyze_symbol(s):
    b=pd.DataFrame(books[s]).drop_duplicates('ts').sort_values('ts')
    t=pd.DataFrame(trades[s]).drop_duplicates(['ts','price','qty','isBuyerMaker']).sort_values('ts') if trades[s] else pd.DataFrame()
    if len(b)<5 or t.empty: return [], {'symbol':s,'book_events':len(b),'trades':len(t),'median_spread_bp':float(b.spread_bp.median()) if len(b) else np.nan}
    start=max(b.ts.min(),t.ts.min()); end=min(b.ts.max(),t.ts.max())
    b=b[(b.ts>=start)&(b.ts<=end)].copy(); t=t[(t.ts>=start)&(t.ts<=end)].copy()
    out=[]
    for latency in [10,240]:
      for hold_ms in [1000,3000,10000,30000]:
       for qf in [0.25,0.5,1.0]:
        for side in ['buy','sell']:
            pnls=[]; maker_exits=0; entries=0
            step=max(1,len(b)//300)
            for _,r in b.iloc[::step].iterrows():
                if r.spread_bp<4.5: continue
                entry_ts=int(r.ts+latency); entry_px=r.bid if side=='buy' else r.ask
                queue=(r.bidq if side=='buy' else r.askq)*qf
                if side=='buy': hits=t[(t.ts>=entry_ts)&(t.ts<=entry_ts+hold_ms)&(t.isBuyerMaker==True)&(t.price<=entry_px)]
                else: hits=t[(t.ts>=entry_ts)&(t.ts<=entry_ts+hold_ms)&(t.isBuyerMaker==False)&(t.price>=entry_px)]
                if hits.qty.sum()<=queue: continue
                fill_ts=int(hits.iloc[np.searchsorted(hits.qty.cumsum().values,queue,side='right')].ts) if len(hits) else entry_ts
                exit_deadline=fill_ts+hold_ms
                bb=b[(b.ts>=fill_ts+latency)&(b.ts<=exit_deadline)]
                if bb.empty: continue
                target=(bb.iloc[0].ask if side=='buy' else bb.iloc[0].bid)
                if side=='buy': eh=t[(t.ts>=fill_ts+latency)&(t.ts<=exit_deadline)&(t.isBuyerMaker==False)&(t.price>=target)]
                else: eh=t[(t.ts>=fill_ts+latency)&(t.ts<=exit_deadline)&(t.isBuyerMaker==True)&(t.price<=target)]
                if not eh.empty:
                    exit_px=target; fees=2*MAKER_FEE_BP; maker_exits+=1
                else:
                    last=bb.iloc[-1]; exit_px=last.bid if side=='buy' else last.ask; fees=MAKER_FEE_BP+TAKER_FEE_BP
                gross=((exit_px-entry_px)/entry_px*1e4)*(1 if side=='buy' else -1)
                pnls.append(gross-fees); entries+=1
            if pnls:
                out.append({'symbol':s,'latency_ms':latency,'hold_ms':hold_ms,'queue_frac':qf,'side':side,'cycles':len(pnls),'maker_exit_rate':maker_exits/len(pnls),'net_bp_mean':float(np.mean(pnls)),'net_bp_median':float(np.median(pnls)),'positive_rate':float(np.mean(np.array(pnls)>=0))})
    return out, {'symbol':s,'book_events':len(b),'trades':len(t),'median_spread_bp':float(b.spread_bp.median()),'p75_spread_bp':float(b.spread_bp.quantile(.75))}


asyncio.run(capture())
allres=[]; diags=[]
for s in SYMBOLS:
    pd.DataFrame(books[s]).to_csv(f'{OUT}/{s}_book.csv',index=False); pd.DataFrame(trades[s]).to_csv(f'{OUT}/{s}_trades.csv',index=False)
    r,d=analyze_symbol(s); allres+=r; diags.append(d)
rdf=pd.DataFrame(allres); ddf=pd.DataFrame(diags); rdf.to_csv(f'{OUT}/results.csv',index=False); ddf.to_csv(f'{OUT}/diagnostics.csv',index=False)
lines=['# Live maker microstructure probe','',f'- Capture: {CAPTURE_SECONDS}s exchange-time BBO bookTicker + REST recent trades','- Fees: maker 2.0 bp/side; emergency taker 5.0 bp/side','- Fill model: displayed L1 queue ahead; aggressive trade-through required.','- Latency: 10ms and 240ms order-to-exchange; market-data delivery latency not modeled.','','## Diagnostics','',ddf.to_markdown(index=False),'','## Net-positive configurations (>=10 completed cycles)','']
if not rdf.empty:
    good=rdf[(rdf.cycles>=10)&(rdf.net_bp_mean>=0)].sort_values(['net_bp_mean','cycles'],ascending=[False,False]).head(20)
    lines.append(good.to_markdown(index=False) if len(good) else 'None.')
    lines += ['','## Best configurations regardless of sign','',rdf.sort_values(['net_bp_mean','cycles'],ascending=[False,False]).head(20).to_markdown(index=False)]
else: lines += ['No completed cycles.']
lines += ['','Short prospective probe only; not deployment proof. Hidden/RPI liquidity, own order size, market-data receive latency and exact exchange queue priority remain unmodeled.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines))
print('\n'.join(lines))