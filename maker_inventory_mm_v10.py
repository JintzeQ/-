import os
import numpy as np
import pandas as pd

OUT='maker_inventory_mm_v10_output'; os.makedirs(OUT,exist_ok=True)
MAKER=0.0002; TAKER=0.0005; STEP=250
CAPTURES={
 'gala_v6':('maker_live_v6_output/GALAUSDT_book.csv','maker_live_v6_output/GALAUSDT_trades.csv'),
 'gala_oos':('maker_gala_flip_oos_output/book.csv','maker_gala_flip_oos_output/trades.csv'),
 'op_v9':('maker_live_v9_output/OPUSDT_book.csv','maker_live_v9_output/OPUSDT_trades.csv'),
 'robo_v7':('maker_live_v7_output/ROBOUSDT_book.csv','maker_live_v7_output/ROBOUSDT_trades.csv'),
}
PROFILES={
 'symmetric': {'flow_cut':1.01,'spread_buffer':0.25},
 'tox20': {'flow_cut':0.20,'spread_buffer':0.25},
 'tox40': {'flow_cut':0.40,'spread_buffer':0.25},
}

class Tape:
 def __init__(self,bp,tp):
  self.b=pd.read_csv(bp).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
  self.t=pd.read_csv(tp).sort_values('ts').reset_index(drop=True)
  if 'mid' not in self.b.columns:self.b['mid']=(self.b.bid+self.b.ask)/2
  if 'spread_bp' not in self.b.columns:self.b['spread_bp']=(self.b.ask-self.b.bid)/self.b.mid*1e4
  self.bt=self.b.ts.to_numpy(np.int64);self.bid=self.b.bid.to_numpy(float);self.ask=self.b.ask.to_numpy(float);self.bidq=self.b.bidq.to_numpy(float);self.askq=self.b.askq.to_numpy(float);self.mid=self.b.mid.to_numpy(float);self.spread=self.b.spread_bp.to_numpy(float)
  self.tt=self.t.ts.to_numpy(np.int64);self.tp=self.t.price.to_numpy(float);self.tq=self.t.qty.to_numpy(float);self.tm=self.t.buyer_maker.astype(bool).to_numpy()
 def bi(self,ts):
  i=np.searchsorted(self.bt,ts,side='right')-1;return i if i>=0 else None
 def flow(self,ts,ms=1000):
  lo=np.searchsorted(self.tt,ts-ms,'left');hi=np.searchsorted(self.tt,ts,'right')
  if hi<=lo:return 0.0
  n=self.tp[lo:hi]*self.tq[lo:hi];sg=np.where(self.tm[lo:hi],-n,n);return float(sg.sum()/(n.sum()+1e-12))
 def qty_against(self,a,z,side,px):
  lo=np.searchsorted(self.tt,a,'right');hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.0
  p=self.tp[lo:hi];q=self.tq[lo:hi];m=self.tm[lo:hi]
  mask=(m & (p<=px+1e-15)) if side=='buy' else ((~m) & (p>=px-1e-15))
  return float(q[mask].sum())
 def touch_stable(self,a,z,side,px):
  lo=np.searchsorted(self.bt,a,'right');hi=np.searchsorted(self.bt,z,'right')
  if hi<=lo:return True
  x=self.bid[lo:hi] if side=='buy' else self.ask[lo:hi]
  return bool(np.all(np.abs(x-px)<=1e-15))

def simulate(name,x,lat,profile,order_usd,cap_usd):
 p=PROFILES[profile]
 if len(x.bt)<3 or len(x.tt)<2:return None,[]
 start=max(int(x.bt[0])+1000,int(x.tt[0])+1000);end=min(int(x.bt[-1]),int(x.tt[-1]))
 if end<=start:return None,[]
 cash=fees=pos=vol=0.0;max_inv=0.0;attempts=0;fills=[];orders={'buy':None,'sell':None}
 prev=start
 times=np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64)
 for now in times:
  i=x.bi(now)
  if i is None:continue
  # Process resting quotes. Any intervening touch change cancels the order and forfeits queue priority (conservative).
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   a=max(prev,o['place_ts'])
   if now<=a:continue
   if not x.touch_stable(a,now,side,o['px']):orders[side]=None;continue
   o['need']-=x.qty_against(a,now,side,o['px'])
   if o['need']<=0:
    notional=o['own']*o['px'];fee=notional*MAKER
    if side=='buy':cash-=notional;pos+=o['own']
    else:cash+=notional;pos-=o['own']
    fees+=fee;vol+=notional
    fills.append({'capture':name,'lat_ms':lat,'profile':profile,'order_usd':order_usd,'cap_usd':cap_usd,'ts':now,'side':side,'px':o['px'],'notional':notional,'fee':fee,'flow_entry':o['flow_entry'],'spread_entry_bp':o['spread_entry_bp'],'inventory_after_qty':pos})
    orders[side]=None
  inv=pos*x.mid[i];max_inv=max(max_inv,abs(inv));f=x.flow(now,1000);spread_ok=x.spread[i]>=4.0+p['spread_buffer']
  buy_ok=spread_ok and f>=-p['flow_cut'] and inv<cap_usd-1e-9
  sell_ok=spread_ok and f<=p['flow_cut'] and inv>-cap_usd+1e-9
  if inv>0.5*cap_usd:buy_ok=False
  if inv<-0.5*cap_usd:sell_ok=False
  for side,ok in [('buy',buy_ok),('sell',sell_ok)]:
   if not ok:orders[side]=None;continue
   place=now+lat;j=x.bi(place)
   if j is None:continue
   px=x.bid[j] if side=='buy' else x.ask[j];qa=x.bidq[j] if side=='buy' else x.askq[j];own=order_usd/px
   o=orders[side]
   if o is not None and abs(o['px']-px)<=1e-15:continue
   orders[side]={'px':float(px),'own':float(own),'need':float(qa+own),'place_ts':int(place),'flow_entry':float(f),'spread_entry_bp':float(x.spread[j])};attempts+=1
  prev=now
 # Residual inventory is never ignored: taker flatten at end.
 i=x.bi(end);liq=0.0
 if i is not None and abs(pos)>1e-15:
  if pos>0:notional=pos*x.bid[i];cash+=notional
  else:notional=(-pos)*x.ask[i];cash-=notional
  liq=float(notional);fees+=liq*TAKER;vol+=liq;pos=0.0
 pnl=cash-fees
 return {'capture':name,'lat_ms':lat,'profile':profile,'order_usd':order_usd,'cap_usd':cap_usd,'maker_fills':len(fills),'quote_attempts':attempts,'filled_volume_usd':vol,'maker_volume_usd':vol-liq,'liquidation_volume_usd':liq,'liquidation_share':liq/vol if vol else np.nan,'gross_cash_pnl':cash,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan,'max_inventory_usd':max_inv},fills

rows=[];allfills=[]
for name,(bp,tp) in CAPTURES.items():
 x=Tape(bp,tp)
 for lat in [10,240]:
  for profile in PROFILES:
   for order_usd in [25.,100.]:
    for cap_usd in [100.,300.]:
     r,f=simulate(name,x,lat,profile,order_usd,cap_usd)
     if r is not None:rows.append(r)
     allfills.extend(f)
R=pd.DataFrame(rows);F=pd.DataFrame(allfills);R.to_csv(f'{OUT}/summary.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
keys=['lat_ms','profile','order_usd','cap_usd'];grp=[]
for k,g in R.groupby(keys):
 tv=g.filled_volume_usd.sum();tpnl=g.net_pnl_usd.sum();active=g[g.filled_volume_usd>0]
 grp.append(dict(zip(keys,k))|{'captures_with_volume':len(active),'total_volume_usd':tv,'total_pnl_usd':tpnl,'net_bp_per_volume':tpnl/tv*1e4 if tv else np.nan,'worst_capture_bp':active.net_bp_per_volume.min() if len(active) else np.nan,'maker_fills':int(g.maker_fills.sum()),'liquidation_share':g.liquidation_volume_usd.sum()/tv if tv else np.nan})
G=pd.DataFrame(grp).sort_values(['net_bp_per_volume','total_volume_usd'],ascending=[False,False]);G.to_csv(f'{OUT}/aggregate.csv',index=False)
cand=G[(G.captures_with_volume>=2)&(G.net_bp_per_volume>=0)&(G.worst_capture_bp>=-2.0)]
lines=['# Inventory-aware continuous maker v10','', '- Replays four independent prospective captures: GALA v6, fresh GALA OOS, OP v9, ROBO v7.','- Quotes at BBO, retains queue only while touch is unchanged; touch change/toxicity cancellation loses priority and requotes behind full displayed L1 queue.','- Full displayed queue + own order must trade through; no cancellation credit.','- Fills are not force-paired. Inventory may naturally net against later opposite maker fills, with hard USD cap and half-cap inventory skew.','- Maker fee 2bp per fill; all residual inventory is forcibly taker-flattened at opposite touch with 5bp fee.','- KPI = final net PnL / total genuine filled notional (bp). Grid is exploratory, not deployment proof.','', '## Robust candidates','']
lines.append(cand.to_markdown(index=False,floatfmt='.4f') if len(cand) else 'None.')
lines+=['','## Aggregate ranking','',G.head(20).to_markdown(index=False,floatfmt='.4f'),'','## Best per capture','']
P=pd.concat([R[(R.capture==n)&(R.filled_volume_usd>0)].sort_values('net_bp_per_volume',ascending=False).head(6) for n in CAPTURES],ignore_index=True)
lines.append(P.to_markdown(index=False,floatfmt='.4f') if len(P) else 'No fills.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
