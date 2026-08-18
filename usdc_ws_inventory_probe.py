import asyncio, json, os, time
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['SOLUSDC','XRPUSDC','DOGEUSDC','LTCUSDC']
CAPTURE=150
STEP=100
ORDER_USD=70.0
INV_CAP_USD=70.0
TAKER_FEE_BP=4.0
LATENCIES=[10,240]
QUEUE_FRACS=[1.0,0.5]
TRADE_CREDITS=[0.5,1.0]  # 0.5 primary conservative haircut; 1.0 sensitivity
FILTERS=['none','flow_protected']
OUT='usdc_ws_inventory_output'; os.makedirs(OUT,exist_ok=True)
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
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def collect_trade(stop):
    async with websockets.connect(TRADE,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try: raw=await asyncio.wait_for(ws.recv(),2)
            except asyncio.TimeoutError: continue
            d=json.loads(raw).get('data',{})
            s=d.get('s')
            if s in SYMBOLS and d.get('e')=='aggTrade':
                trades[s].append((int(d.get('a',0)),int(d['T']),float(d['p']),float(d['q']),bool(d['m'])))

async def capture():
    stop=time.monotonic()+CAPTURE
    await asyncio.gather(collect_book(stop),collect_trade(stop))

class Tape:
    def __init__(self,s):
        self.b=pd.DataFrame(books[s],columns=['ts','bid','bidq','ask','askq']).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
        self.t=pd.DataFrame(trades[s],columns=['agg_id','ts','price','qty','buyer_maker']).sort_values(['ts','agg_id']).drop_duplicates('agg_id').reset_index(drop=True)
        if len(self.b):
            self.b['mid']=(self.b.bid+self.b.ask)/2
            self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
            self.b['qimb']=(self.b.bidq-self.b.askq)/(self.b.bidq+self.b.askq+1e-12)
        self.bt=self.b.ts.to_numpy(np.int64) if len(self.b) else np.array([],dtype=np.int64)
        self.bid=self.b.bid.to_numpy(float) if len(self.b) else np.array([]); self.ask=self.b.ask.to_numpy(float) if len(self.b) else np.array([])
        self.bq=self.b.bidq.to_numpy(float) if len(self.b) else np.array([]); self.aq=self.b.askq.to_numpy(float) if len(self.b) else np.array([])
        self.mid=self.b.mid.to_numpy(float) if len(self.b) else np.array([]); self.spr=self.b.spread_bp.to_numpy(float) if len(self.b) else np.array([])
        self.qimb=self.b.qimb.to_numpy(float) if len(self.b) else np.array([])
        self.tt=self.t.ts.to_numpy(np.int64) if len(self.t) else np.array([],dtype=np.int64)
        self.tp=self.t.price.to_numpy(float) if len(self.t) else np.array([]); self.tq=self.t.qty.to_numpy(float) if len(self.t) else np.array([])
        self.tm=self.t.buyer_maker.astype(bool).to_numpy() if len(self.t) else np.array([],dtype=bool)
    def bi(self,z):
        if len(self.bt)==0:return None
        i=np.searchsorted(self.bt,z,'right')-1; return i if i>=0 else None
    def stable(self,a,z,side,px):
        lo=np.searchsorted(self.bt,a,'right'); hi=np.searchsorted(self.bt,z,'right')
        x=(self.bid if side=='buy' else self.ask)[lo:hi]
        return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
    def traded_qty(self,a,z,side,px):
        if len(self.tt)==0:return 0.0
        lo=np.searchsorted(self.tt,a,'right'); hi=np.searchsorted(self.tt,z,'right')
        if hi<=lo:return 0.0
        p=self.tp[lo:hi]; q=self.tq[lo:hi]; m=self.tm[lo:hi]
        mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15))
        return float(q[mask].sum())
    def flow(self,z,ms=1000):
        if len(self.tt)==0:return 0.0
        lo=np.searchsorted(self.tt,z-ms,'left'); hi=np.searchsorted(self.tt,z,'right')
        if hi<=lo:return 0.0
        n=self.tp[lo:hi]*self.tq[lo:hi]
        return float(np.where(self.tm[lo:hi],-n,n).sum()/(n.sum()+1e-12))
    def mark(self,z,side,px,h):
        i=self.bi(z+h)
        if i is None:return np.nan
        return ((self.mid[i]-px)/px*1e4) if side=='buy' else ((px-self.mid[i])/px*1e4)

def replay(s,x,lat,qfrac,credit,flt):
    if len(x.bt)<3 or len(x.tt)<3:return None,[]
    start=max(int(x.bt[0])+1500,int(x.tt[0])+1500); end=min(int(x.bt[-1]),int(x.tt[-1]))
    if end<=start:return None,[]
    cash=0.; pos=0.; maker_vol=0.; attempts=0; max_inv=0.; prev=start; orders={'buy':None,'sell':None}; fills=[]
    for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
        i=x.bi(now)
        if i is None:continue
        # fill/cancel existing quotes
        for side in ['buy','sell']:
            o=orders[side]
            if o is None:continue
            a=max(prev,o['place'])
            if now<=a:continue
            if not x.stable(a,now,side,o['px']):
                orders[side]=None; continue
            o['need']-=credit*x.traded_qty(a,now,side,o['px'])
            if o['need']<=0:
                n=o['own']*o['px']
                if side=='buy':cash-=n; pos+=o['own']
                else:cash+=n; pos-=o['own']
                maker_vol+=n
                fills.append({'symbol':s,'lat_ms':lat,'qfrac':qfrac,'trade_credit':credit,'filter':flt,'ts':int(now),'side':side,'px':o['px'],'notional':n,'mark1_bp':x.mark(now,side,o['px'],1000),'mark5_bp':x.mark(now,side,o['px'],5000),'inventory_usd_after':pos*x.mid[i]})
                orders[side]=None
        inv=pos*x.mid[i]; max_inv=max(max_inv,abs(inv)); fl=x.flow(now,1000)
        # inventory-first two-sided quoting; do not add beyond cap
        for side in ['buy','sell']:
            room=(INV_CAP_USD-inv) if side=='buy' else (INV_CAP_USD+inv)
            room=max(0.0,min(ORDER_USD,room))
            if room<5.0:
                orders[side]=None; continue
            if flt=='flow_protected':
                toxic=(-fl if side=='buy' else fl)
                if toxic>0.25: room*=0.5
                if toxic>0.60: room*=0.5
                if room<5.0: orders[side]=None; continue
            place=int(now+lat); j=x.bi(place)
            if j is None:continue
            px=float(x.bid[j] if side=='buy' else x.ask[j]); qa=float(x.bq[j] if side=='buy' else x.aq[j]); own=room/px
            o=orders[side]
            if o is not None and abs(o['px']-px)<=1e-15 and abs(o['own']-own)/max(own,1e-12)<0.05:continue
            orders[side]={'px':px,'own':own,'need':qa*qfrac+own,'place':place}; attempts+=1
        prev=now
    i=x.bi(end); liq=0.; fee=0.
    if i is not None and abs(pos)>1e-15:
        if pos>0:n=pos*x.bid[i]; cash+=n
        else:n=(-pos)*x.ask[i]; cash-=n
        liq=float(abs(n)); fee=liq*TAKER_FEE_BP/1e4
    total=maker_vol+liq; net=cash-fee; mins=(end-start)/60000.
    f=pd.DataFrame(fills)
    return {'symbol':s,'lat_ms':lat,'qfrac':qfrac,'trade_credit':credit,'filter':flt,'book_events':len(x.b),'agg_trades':len(x.t),'maker_fills':len(fills),'quote_attempts':attempts,'maker_volume_usd':maker_vol,'maker_volume_per_min':maker_vol/max(mins,1e-9),'liquidation_volume_usd':liq,'liquidation_share':liq/total if total else np.nan,'net_pnl_usd':net,'net_bp_per_volume':net/total*1e4 if total else np.nan,'mean_mark1_bp':float(f.mark1_bp.mean()) if len(f) else np.nan,'mean_mark5_bp':float(f.mark5_bp.mean()) if len(f) else np.nan,'max_inventory_usd':max_inv},fills

asyncio.run(capture())
rows=[]; fillrows=[]; diag=[]
for s in SYMBOLS:
    x=Tape(s); x.b.to_csv(f'{OUT}/{s}_book.csv',index=False); x.t.to_csv(f'{OUT}/{s}_aggtrades.csv',index=False)
    diag.append({'symbol':s,'book_events':len(x.b),'agg_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan,'p75_spread_bp':x.b.spread_bp.quantile(.75) if len(x.b) else np.nan})
    for lat in LATENCIES:
      for qf in QUEUE_FRACS:
       for cr in TRADE_CREDITS:
        for flt in FILTERS:
            r,f=replay(s,x,lat,qf,cr,flt)
            if r:rows.append(r); fillrows.extend(f)
R=pd.DataFrame(rows); D=pd.DataFrame(diag); F=pd.DataFrame(fillrows)
R.to_csv(f'{OUT}/results.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False); F.to_csv(f'{OUT}/fills.csv',index=False)
valid=R[(R.maker_fills>=3)&(R.net_bp_per_volume>=0)].sort_values(['maker_volume_per_min','net_bp_per_volume'],ascending=[False,False]) if len(R) else R
primary=valid[(valid.qfrac==1.0)&(valid.trade_credit==0.5)] if len(valid) else valid
best=R[R.maker_fills>=3].sort_values(['net_bp_per_volume','maker_fills'],ascending=[False,False]) if len(R) else R
lines=['# Robust USDC WebSocket inventory-maker probe','',f'- Prospective capture {CAPTURE}s; SOL/XRP/DOGE/LTC USDC perpetuals.','- Maker fee 0bp; only residual end-of-window taker flatten charged 4bp.','- $70 order / $70 inventory cap; 10ms and 240ms order-to-exchange.','- bookTicker and aggTrade use separate Futures WebSocket endpoints.','- Any touch change cancels quote and forfeits queue progress; no cancellation credit; own size sits behind modeled queue.','- Primary conservative fill model: 1.0x displayed L1 queue and only 50% of qualifying aggTrade volume credited to depletion. 0.5x queue / 100% credit are sensitivity only.','','## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f') if len(D) else 'None.','','## PRIMARY conservative configs: net>=0, >=3 maker fills, ranked turnover','',primary.head(20).to_markdown(index=False,floatfmt='.4f') if len(primary) else 'None.','','## All sensitivity configs net>=0, >=3 maker fills','',valid.head(30).to_markdown(index=False,floatfmt='.4f') if len(valid) else 'None.','','## Best net EV configs regardless of sign','',best.head(20).to_markdown(index=False,floatfmt='.4f') if len(best) else 'No config reached 3 maker fills.','','Short prospective sample only. aggTrade does not expose RPI status here, so trade-credit haircut is used as a conservative sensitivity rather than assuming every aggressive trade depletes visible queue. Exact queue priority, market-data receive latency, funding and VIP-volume credit still require live/account validation.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
