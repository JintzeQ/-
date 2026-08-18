import asyncio, json, os, time, requests
from collections import defaultdict
import numpy as np
import pandas as pd
import websockets

SYMBOLS=['BTCUSDC','ETHUSDC','SOLUSDC','XRPUSDC']
CAPTURE=120
STEP=250
LAT=10
ORDER_USD=100.0
CAP_USD=100.0
OUT='usdc_inventory_rpi_output'
os.makedirs(OUT,exist_ok=True)

books=defaultdict(list); trades=defaultdict(list); last_id={}
BOOK_URL='wss://fstream.binance.com/public/stream?streams='+'/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
REST='https://fapi.binance.com/fapi/v1/trades'
SESSION=requests.Session(); SESSION.headers.update({'User-Agent':'usdc-rpi-probe/1.0','Cache-Control':'no-cache','Pragma':'no-cache'})

def recent(s):
    r=SESSION.get(REST,params={'symbol':s,'limit':1000},timeout=8)
    r.raise_for_status(); return r.json()

async def collect_books(stop):
    async with websockets.connect(BOOK_URL,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
        while time.monotonic()<stop:
            try:o=json.loads(await asyncio.wait_for(ws.recv(),2)); d=o.get('data',o)
            except asyncio.TimeoutError:continue
            s=d.get('s')
            if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                books[s].append((int(d.get('T',d.get('E'))),float(d['b']),float(d['B']),float(d['a']),float(d['A'])))

async def poll_trades(stop):
    # Seed at current trade id so the tape is prospective only.
    for s in SYMBOLS:
        try:
            rr=await asyncio.to_thread(recent,s); last_id[s]=max(int(x['id']) for x in rr) if rr else -1
        except Exception as e:
            print('seed error',s,repr(e),flush=True); last_id[s]=-1
    while time.monotonic()<stop:
        for s in SYMBOLS:
            try:
                rr=await asyncio.to_thread(recent,s); old=last_id.get(s,-1)
                fresh=sorted((x for x in rr if int(x['id'])>old),key=lambda x:int(x['id']))
                for x in fresh:
                    # Critical: ordinary visible queue must not be depleted by RPI fills.
                    if bool(x.get('isRPITrade',False)):continue
                    trades[s].append((int(x['id']),int(x['time']),float(x['price']),float(x['qty']),bool(x['isBuyerMaker'])))
                if fresh:last_id[s]=max(old,max(int(x['id']) for x in fresh))
            except Exception as e:print('poll error',s,repr(e),flush=True)
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
        self.bt=self.b.ts.to_numpy(np.int64); self.bid=self.b.bid.to_numpy(float); self.ask=self.b.ask.to_numpy(float)
        self.bq=self.b.bidq.to_numpy(float); self.aq=self.b.askq.to_numpy(float); self.mid=self.b.mid.to_numpy(float); self.spr=self.b.spread_bp.to_numpy(float)
        self.tt=self.t.ts.to_numpy(np.int64); self.tp=self.t.price.to_numpy(float); self.tq=self.t.qty.to_numpy(float); self.tm=self.t.buyer_maker.astype(bool).to_numpy()
    def bi(self,z):
        i=np.searchsorted(self.bt,z,'right')-1; return i if i>=0 else None
    def stable(self,a,z,side,px):
        lo=np.searchsorted(self.bt,a,'right'); hi=np.searchsorted(self.bt,z,'right'); x=(self.bid if side=='buy' else self.ask)[lo:hi]
        return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
    def qty(self,a,z,side,px):
        lo=np.searchsorted(self.tt,a,'right'); hi=np.searchsorted(self.tt,z,'right')
        if hi<=lo:return 0.0
        p=self.tp[lo:hi]; q=self.tq[lo:hi]; m=self.tm[lo:hi]
        mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15))
        return float(q[mask].sum())

def replay(s,x):
    if len(x.bt)<3 or len(x.tt)<2:return None,[]
    start=max(int(x.bt[0])+1000,int(x.tt[0])+1000); end=min(int(x.bt[-1]),int(x.tt[-1]))
    if end<=start:return None,[]
    cash=pos=maker_vol=0.0; max_inv=0.0; attempts=0; prev=start; fills=[]; orders={'buy':None,'sell':None}
    for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
        i=x.bi(now)
        if i is None:continue
        for side in ['buy','sell']:
            o=orders[side]
            if o is None:continue
            a=max(prev,o['place'])
            if now<=a:continue
            if not x.stable(a,now,side,o['px']):orders[side]=None; continue
            o['need']-=x.qty(a,now,side,o['px'])
            if o['need']<=0:
                n=o['own']*o['px']
                if side=='buy':cash-=n; pos+=o['own']
                else:cash+=n; pos-=o['own']
                maker_vol+=n; fills.append({'symbol':s,'ts':now,'side':side,'px':o['px'],'notional':n,'inventory_qty_after':pos}); orders[side]=None
        inv=pos*x.mid[i]; max_inv=max(max_inv,abs(inv))
        # Symmetric continuous maker. Half-cap skew prevents adding to an already material inventory.
        for side,allow in [('buy',inv<CAP_USD and inv<=0.5*CAP_USD),('sell',inv>-CAP_USD and inv>=-0.5*CAP_USD)]:
            if not allow:orders[side]=None; continue
            place=now+LAT; j=x.bi(place)
            if j is None:continue
            px=x.bid[j] if side=='buy' else x.ask[j]; qa=x.bq[j] if side=='buy' else x.aq[j]; own=ORDER_USD/px; o=orders[side]
            if o is not None and abs(o['px']-px)<=1e-15:continue
            orders[side]={'px':float(px),'own':float(own),'need':float(qa+own),'place':int(place)}; attempts+=1
        prev=now
    i=x.bi(end); liq=0.0
    if i is not None and abs(pos)>1e-15:
        if pos>0:n=pos*x.bid[i]; cash+=n
        else:n=(-pos)*x.ask[i]; cash-=n
        liq=float(n)
    total_vol=maker_vol+liq
    return {'symbol':s,'quote_attempts':attempts,'maker_fills':len(fills),'maker_volume_usd':maker_vol,'liquidation_volume_usd':liq,'total_volume_usd':total_vol,'liquidation_share':liq/total_vol if total_vol else np.nan,'gross_cash_pnl':cash,'max_inventory_usd':max_inv},fills

asyncio.run(capture())
base=[]; fill_rows=[]; diag=[]
for s in SYMBOLS:
    x=Tape(s); r,f=replay(s,x)
    x.b.to_csv(f'{OUT}/{s}_book.csv',index=False); x.t.to_csv(f'{OUT}/{s}_nonrpi_trades.csv',index=False)
    diag.append({'symbol':s,'book_events':len(x.b),'nonrpi_trades':len(x.t),'median_spread_bp':x.b.spread_bp.median() if len(x.b) else np.nan,'p75_spread_bp':x.b.spread_bp.quantile(.75) if len(x.b) else np.nan})
    if r:base.append(r)
    fill_rows.extend(f)
B=pd.DataFrame(base); D=pd.DataFrame(diag); F=pd.DataFrame(fill_rows)
B.to_csv(f'{OUT}/base.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False); F.to_csv(f'{OUT}/fills.csv',index=False)
scenarios=[('usdc_zero_assumption',0.0,4.0),('standard_user_fee',2.0,5.0)]
rows=[]
for r in base:
    for name,maker_bp,taker_bp in scenarios:
        fees=r['maker_volume_usd']*maker_bp/1e4+r['liquidation_volume_usd']*taker_bp/1e4
        pnl=r['gross_cash_pnl']-fees; vol=r['total_volume_usd']
        rows.append({**r,'fee_scenario':name,'maker_fee_bp':maker_bp,'taker_fee_bp':taker_bp,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan})
R=pd.DataFrame(rows); R.to_csv(f'{OUT}/results.csv',index=False)
lines=['# RPI-aware USDC inventory-maker probe','',f'- Fresh prospective capture: {CAPTURE}s; BTC/ETH/SOL/XRP USDC USDⓈ-M.','- BBO: Futures public bookTicker. Fill tape: REST recent trades with `isRPITrade=true` explicitly excluded.','- Locked execution model: 10ms order-to-exchange, symmetric BBO, $100/order, $100 inventory cap.','- Full visible L1 queue + own size must trade through; any touch change cancels and forfeits queue progress; no cancellation credit.','- Same realized fills are scored under two fee scenarios: conditional USDC 0bp maker/4bp taker and normal 2bp maker/5bp taker.','', '## Diagnostics','',D.to_markdown(index=False,floatfmt='.4f'),'','## Results','']
lines.append(R.to_markdown(index=False,floatfmt='.4f') if len(R) else 'No valid tapes/fills.')
for name,_,_ in scenarios:
    z=R[(R.fee_scenario==name)&(R.total_volume_usd>0)]
    tv=z.total_volume_usd.sum(); pnl=z.net_pnl_usd.sum(); bp=pnl/tv*1e4 if tv else np.nan
    lines+=['',f'**{name} aggregate:** volume=${tv:.2f}, net=${pnl:.4f}, net={bp:.4f} bp/volume.']
lines+=['','The 0-maker scenario is conditional until the account-specific effective USDC Futures fee is independently confirmed. Short sample only; positive values require longer prospective replication.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines),flush=True)
