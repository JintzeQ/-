import os,numpy as np,pandas as pd
OUT='maker_inventory_capacity_output';os.makedirs(OUT,exist_ok=True)
CAPS={
'gala_v6':('maker_live_v6_output/GALAUSDT_book.csv','maker_live_v6_output/GALAUSDT_trades.csv'),
'gala_oos':('maker_gala_flip_oos_output/book.csv','maker_gala_flip_oos_output/trades.csv'),
'gala_fresh':('maker_inventory_fresh_ws_oos_output/GALAUSDT_book.csv','maker_inventory_fresh_ws_oos_output/GALAUSDT_trades.csv')}
STEP=250;LAT=10;MIN_SPREAD=4.25;MAKER=.0002;TAKER=.0005
class T:
 def __init__(self,bp,tp):
  b=pd.read_csv(bp).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True);t=pd.read_csv(tp).sort_values('ts').reset_index(drop=True)
  if 'mid' not in b.columns:b['mid']=(b.bid+b.ask)/2
  if 'spread_bp' not in b.columns:b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
  self.b=b;self.t=t;self.bt=b.ts.to_numpy(np.int64);self.bid=b.bid.to_numpy(float);self.ask=b.ask.to_numpy(float);self.bq=b.bidq.to_numpy(float);self.aq=b.askq.to_numpy(float);self.mid=b.mid.to_numpy(float);self.spr=b.spread_bp.to_numpy(float);self.tt=t.ts.to_numpy(np.int64);self.tp=t.price.to_numpy(float);self.tq=t.qty.to_numpy(float);self.tm=t.buyer_maker.astype(bool).to_numpy()
 def bi(self,z):i=np.searchsorted(self.bt,z,'right')-1;return i if i>=0 else None
 def stable(self,a,z,side,px):
  lo=np.searchsorted(self.bt,a,'right');hi=np.searchsorted(self.bt,z,'right');x=(self.bid if side=='buy' else self.ask)[lo:hi];return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
 def qty(self,a,z,side,px):
  lo=np.searchsorted(self.tt,a,'right');hi=np.searchsorted(self.tt,z,'right')
  if hi<=lo:return 0.
  p=self.tp[lo:hi];q=self.tq[lo:hi];m=self.tm[lo:hi];mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15));return float(q[mask].sum())
def sim(name,x,order):
 cap=order;start=max(int(x.bt[0])+1000,int(x.tt[0])+1000);end=min(int(x.bt[-1]),int(x.tt[-1]));dur=(end-start)/1000;orders={'buy':None,'sell':None};cash=fees=pos=vol=0.;prev=start;fills=attempts=0;maxinv=0.
 for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
  i=x.bi(now)
  if i is None:continue
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   a=max(prev,o['place'])
   if now<=a:continue
   if not x.stable(a,now,side,o['px']):orders[side]=None;continue
   o['need']-=x.qty(a,now,side,o['px'])
   if o['need']<=0:
    n=o['own']*o['px'];fee=n*MAKER
    if side=='buy':cash-=n;pos+=o['own']
    else:cash+=n;pos-=o['own']
    fees+=fee;vol+=n;fills+=1;orders[side]=None
  inv=pos*x.mid[i];maxinv=max(maxinv,abs(inv));ok=x.spr[i]>=MIN_SPREAD
  for side,allow in [('buy',ok and inv<cap and inv<=.5*cap),('sell',ok and inv>-cap and inv>=-.5*cap)]:
   if not allow:orders[side]=None;continue
   place=now+LAT;j=x.bi(place)
   if j is None:continue
   px=x.bid[j] if side=='buy' else x.ask[j];qq=x.bq[j] if side=='buy' else x.aq[j];own=order/px;o=orders[side]
   if o is not None and abs(o['px']-px)<=1e-15:continue
   orders[side]={'px':float(px),'own':float(own),'need':float(qq+own),'place':int(place)};attempts+=1
  prev=now
 i=x.bi(end);liq=0.
 if i is not None and abs(pos)>1e-15:
  if pos>0:n=pos*x.bid[i];cash+=n
  else:n=(-pos)*x.ask[i];cash-=n
  liq=float(n);fees+=liq*TAKER;vol+=liq
 pnl=cash-fees
 return {'capture':name,'order_usd':order,'duration_sec':dur,'maker_fills':fills,'quote_attempts':attempts,'maker_volume_usd':vol-liq,'liquidation_volume_usd':liq,'total_volume_usd':vol,'liquidation_share':liq/vol if vol else np.nan,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan,'max_inventory_usd':maxinv}
rows=[]
for n,p in CAPS.items():
 x=T(*p)
 for o in [100.,250.,500.,1000.]:rows.append(sim(n,x,o))
R=pd.DataFrame(rows);R.to_csv(f'{OUT}/per_capture.csv',index=False);agg=[]
for o,g in R.groupby('order_usd'):
 dur=g.duration_sec.sum();tv=g.total_volume_usd.sum();mv=g.maker_volume_usd.sum();pnl=g.net_pnl_usd.sum();active=g[g.total_volume_usd>0]
 agg.append({'order_usd':o,'captures_with_volume':len(active),'maker_fills':int(g.maker_fills.sum()),'total_capture_hours':dur/3600,'maker_volume_usd':mv,'total_volume_usd':tv,'maker_volume_per_hour':mv/(dur/3600),'genuine_volume_per_hour':tv/(dur/3600),'liquidation_share':g.liquidation_volume_usd.sum()/tv if tv else np.nan,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/tv*1e4 if tv else np.nan,'worst_capture_bp':active.net_bp_per_volume.min() if len(active) else np.nan})
A=pd.DataFrame(agg).sort_values('order_usd');A.to_csv(f'{OUT}/aggregate.csv',index=False)
lines=['# GALA locked-maker capacity curve','', '- Same frozen strategy as locked replay and fresh OOS: 10ms symmetric BBO, min spread 4.25bp, inventory cap = one order.','- Only order notional changes: $100 / $250 / $500 / $1000. This is capacity sensitivity, not a signal-parameter search.','- Three independent GALA captures are replayed; full displayed queue + own size must trade through; any touch change loses queue progress.','- Maker 2bp/fill; residual inventory taker-flattened at 5bp.','', '## Aggregate','',A.to_markdown(index=False,floatfmt='.4f'),'','## Per capture','',R.to_markdown(index=False,floatfmt='.4f')]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
