import asyncio, json, os, time, requests
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BTCUSDC','ETHUSDC','SOLUSDC','XRPUSDC']
CAPTURE=180
STEP=100
ORDER_USD=70.0
INV_CAP_USD=70.0
TAKER_FEE_BP=4.0
OUT='usdc_turnover_output'
os.makedirs(OUT,exist_ok=True)

# Soft toxicity controls: quotes stay live; toxic-side size is reduced rather than hard-disabled.
FILTERS={'none':0.0,'very_weak':0.25,'weak':0.50,'medium':0.75}
QFRACS=[1.0,0.5,0.25]  # queue-ahead brackets; 1.0 is the conservative primary case
LATENCIES=[10,240]

books=defaultdict(list); trades=defaultdict(list); last_id={}
BOOK_URL='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
REST='https://fapi.binance.com/fapi/v1/trades'
S=requests.Session(); S.headers.update({'User-Agent':'usdc-turnover-sweep/1.0','Cache-Control':'no-cache'})

def recent(s):
    r=S.get(REST,params={'symbol':s,'limit':1000},timeout=8); r.raise_for_status(); return r.json()

async def collect_books(stop):
    async with websockets.connect(BOOK_URL,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: d=json.loads(await asyncio.wait_for(ws.recv(),2)).get('data',{})
            except asyncio.TimeoutError: continue
            s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def poll_trades(stop):
    for s in SYMBOLS:
        try:
            rr=await asyncio.to_thread(recent,s); last_id[s]=max(int(x['id']) for x in rr) if rr else -1
        except Exception as e:
            print('seed',s,repr(e),flush=True); last_id[s]=-1
    while time.monotonic()<stop:
        for s in SYMBOLS:
            try:
                rr=await asyncio.to_thread(recent,s); old=last_id.get(s,-1)
                fresh=sorted((x for x in rr if int(x['id'])>old),key=lambda x:int(x['id']))
                for x in fresh:
                    if bool(x.get('isRPITrade',False)): continue
                    trades[s].append((int(x['id']),int(x['time']),float(x['price']),float(x['qty']),bool(x['isBuyerMaker'])))
                if fresh: last_id[s]=max(old,max(int(x['id']) for x in fresh))
            except Exception as e: print('poll',s,repr(e),flush=True)
        await asyncio.sleep(1.0)

async def capture():
    stop=time.monotonic()+CAPTURE
    await asyncio.gather(collect_books(stop),poll_trades(stop))

class Tape:
    def __init__(self,s):
        self.b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
        self.t=pd.DataFrame(trades[s],columns=['trade_id','ts','price','qty','buyer_maker']).sort_values(['ts','trade_id']).drop_duplicates('trade_id').reset_index(drop=True)
        if len(self.b):
            self.b['mid']=(self.b.bid+self.b.ask)/2
            self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
        self.bt=self.b.ts.to_numpy(np.int64) if len(self.b) else np.array([],dtype=np.int64)
        self.bid=self.b.bid.to_numpy(float) if len(self.b) else np.array([]); self.ask=self.b.ask.to_numpy(float) if len(self.b) else np.array([])
        self.bq=self.b.bidq.to_numpy(float) if len(self.b) else np.array([]); self.aq=self.b.askq.to_numpy(float) if len(self.b) else np.array([])
        self.mid=self.b.mid.to_numpy(float) if len(self.b) else np.array([]); self.spr=self.b.spread_bp.to_numpy(float) if len(self.b) else np.array([])
        self.tt=self.t.ts.to_numpy(np.int64) if len(self.t) else np.array([],dtype=np.int64)
        self.tp=self.t.price.to_numpy(float) if len(self.t) else np.array([]); self.tq=self.t.qty.to_numpy(float) if len(self.t) else np.array([])
        self.tm=self.t.buyer_maker.astype(bool).to_numpy() if len(self.t) else np.array([],dtype=bool)
    def bi(self,z):
        if len(self.bt)==0:return None
        i=np.searchsorted(self.bt,z,'right')-1; return i if i>=0 else None
    def flow(self,z,ms=1000):
        if len(self.tt)==0:return 0.0
        lo=np.searchsorted(self.tt,z-ms,'left'); hi=np.searchsorted(self.tt,z,'right')
        if hi<=lo:return 0.0
        n=self.tp[lo:hi]*self.tq[lo:hi]; sg=np.where(self.tm[lo:hi],-n,n)
        return float(sg.sum()/(n.sum()+1e-12))
    def stable(self,a,z,side,px):
        lo=np.searchsorted(self.bt,a,'right'); hi=np.searchsorted(self.bt,z,'right'); x=(self.bid if side=='buy' else self.ask)[lo:hi]
        return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
    def traded_qty(self,a,z,side,px):
        lo=np.searchsorted(self.tt,a,'right'); hi=np.searchsorted(self.tt,z,'right')
        if hi<=lo:return 0.0
        p=self.tp[lo:hi]; q=self.tq[lo:hi]; m=self.tm[lo:hi]
        mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15))
        return float(q[mask].sum())
    def markout(self,ts,side,px,h=1000):
        i=self.bi(ts+h)
        if i is None:return np.nan
        return ((self.mid[i]-px)/px*1e4) if side=='buy' else ((px-self.mid[i])/px*1e4)

def replay(s,x,lat,qfrac,filter_name):
    if len(x.bt)<3 or len(x.tt)<2:return None,[]
    alpha=FILTERS[filter_name]
    start=max(int(x.bt[0])+1500,int(x.tt[0])+1500); end=min(int(x.bt[-1]),int(x.tt[-1]))
    if end<=start:return None,[]
    cash=0.0; pos=0.0; maker_vol=0.0; max_inv=0.0; attempts=0; prev=start; fills=[]; orders={'buy':None,'sell':None}
    for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
        i=x.bi(now)
        if i is None: continue
        # Process existing orders. Touch change means cancel/requote and queue progress is forfeited.
        for side in ['buy','sell']:
            o=orders[side]
            if o is None:continue
            a=max(prev,o['place'])
            if now<=a:continue
            if not x.stable(a,now,side,o['px']): orders[side]=None; continue
            o['need']-=x.traded_qty(a,now,side,o['px'])
            if o['need']<=0:
                n=o['own']*o['px']
                if side=='buy': cash-=n; pos+=o['own']
                else: cash+=n; pos-=o['own']
                maker_vol+=n
                fills.append({'symbol':s,'filter':filter_name,'lat_ms':lat,'qfrac':qfrac,'ts':int(now),'side':side,'px':o['px'],'notional':n,'mark1_bp':x.markout(now,side,o['px'],1000),'inventory_usd_after':pos*x.mid[i]})
                orders[side]=None
        inv=pos*x.mid[i]; max_inv=max(max_inv,abs(inv)); fl=x.flow(now,1000)
        # Always quote both sides when inventory allows. Toxicity only reduces size on the exposed side.
        for side in ['buy','sell']:
            increases=(side=='buy' and inv>=0) or (side=='sell' and inv<=0)
            if side=='buy' and inv>=INV_CAP_USD-1e-9: orders[side]=None; continue
            if side=='sell' and inv<=-INV_CAP_USD+1e-9: orders[side]=None; continue
            tox=max(0.0,-fl) if side=='buy' else max(0.0,fl)
            tox_scale=max(0.25,1.0-alpha*tox)
            inv_scale=max(0.25,1.0-abs(inv)/INV_CAP_USD) if increases else 1.0
            notional=ORDER_USD*tox_scale*inv_scale
            if notional<5.0: orders[side]=None; continue
            place=int(now+lat); j=x.bi(place)
            if j is None:continue
            px=float(x.bid[j] if side=='buy' else x.ask[j]); qa=float(x.bq[j] if side=='buy' else x.aq[j]); own=notional/px
            o=orders[side]
            if o is not None and abs(o['px']-px)<=1e-15 and abs(o['own']-own)/max(own,1e-12)<0.10: continue
            orders[side]={'px':px,'own':own,'need':qa*qfrac+own,'place':place}; attempts+=1
        prev=now
    i=x.bi(end); liq=0.0; liq_fee=0.0
    if i is not None and abs(pos)>1e-15:
        if pos>0: n=pos*x.bid[i]; cash+=n
        else: n=(-pos)*x.ask[i]; cash-=n
        liq=float(abs(n)); liq_fee=liq*TAKER_FEE_BP/1e4
    total_vol=maker_vol+liq; net=cash-liq_fee
    dur_min=(end-start)/60000.0
    fdf=pd.DataFrame(fills)
    return {
        'symbol':s,'filter':filter_name,'lat_ms':lat,'qfrac':qfrac,'quote_attempts':attempts,'maker_fills':len(fills),
        'maker_volume_usd':maker_vol,'maker_volume_per_min':maker_vol/max(dur_min,1e-9),'liquidation_volume_usd':liq,
        'liquidation_share':liq/total_vol if total_vol else np.nan,'net_pnl_usd':net,'net_bp_per_volume':net/total_vol*1e4 if total_vol else np.nan,
        'mean_mark1_bp':float(fdf.mark1_bp.mean()) if len(fdf) else np.nan,'positive_mark1_rate':float((fdf.mark1_bp>=0).mean()) if len(fdf) else np.nan,
        'max_inventory_usd':max_inv,'capture_minutes':dur_min
    },fills

asyncio.run(capture())
rows=[]; fillrows=[]; diag=[]
for s in SYMBOLS:
    x=Tape(s)
    x.b.to_csv(f'{OUT}/{s}_book.csv',index=False); x.t.to_csv(f'{OUT}/{s}_nonrpi_trades.csv',index=False)
    diag.append({'symbol':s,'book_events':len(x.b),'nonrpi_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan,'p75_spread_bp':x.b.spread_bp.quantile(.75) if len(x.b) else np.nan})
    for lat in LATENCIES:
      for qfrac in QFRACS:
       for fn in FILTERS:
        r,f=replay(s,x,lat,qfrac,fn)
        if r:rows.append(r); fillrows.extend(f)
R=pd.DataFrame(rows); D=pd.DataFrame(diag); F=pd.DataFrame(fillrows)
R.to_csv(f'{OUT}/results.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False); F.to_csv(f'{OUT}/fills.csv',index=False)
eligible=R[(R.maker_fills>=3)&(R.net_bp_per_volume>=0)].sort_values(['maker_volume_per_min','net_bp_per_volume'],ascending=[False,False]) if len(R) else R
primary=eligible[eligible.qfrac==1.0] if len(eligible) else eligible
lines=['# USDC turnover-first maker sweep','',f'- Prospective capture: {CAPTURE}s; BTC/ETH/SOL/XRP USDC perpetuals.','- Objective: maximize genuine modeled maker volume/min subject to net EV >= 0.','- Maker fee = 0bp; only residual end-of-sample taker flatten pays 4bp.','- $70 nominal quote, $70 inventory cap; 10ms and 240ms order-to-exchange.','- Soft toxicity: quotes remain active; exposed-side size is reduced, never hard-filtered solely by flow.','- Queue brackets: 1.0x displayed L1 is conservative primary; 0.5x/0.25x are sensitivity only.','- RPI trades are excluded from ordinary visible-queue depletion. Any BBO touch change cancels/requotes and forfeits queue progress.','','## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f') if len(D) else 'None.','','## Conservative queue (1.0x): net>=0 ranked by maker volume/min','',primary.head(20).to_markdown(index=False,floatfmt='.4f') if len(primary) else 'None.','','## All queue sensitivities: net>=0 ranked by maker volume/min','',eligible.head(30).to_markdown(index=False,floatfmt='.4f') if len(eligible) else 'None.','','## Best turnover regardless of EV','',R[R.maker_fills>=3].sort_values('maker_volume_per_min',ascending=False).head(20).to_markdown(index=False,floatfmt='.4f') if len(R) else 'No valid results.','','Short prospective sample only. Positive EV is a research candidate, not deployment proof; market-data receive latency, exact exchange queue priority/cancellations ahead, funding, and account-specific fee/VIP-volume eligibility remain outside this replay.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
