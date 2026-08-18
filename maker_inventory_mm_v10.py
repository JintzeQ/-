import os, math
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


def prepare(bp,tp):
 b=pd.read_csv(bp).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
 t=pd.read_csv(tp).sort_values('ts').reset_index(drop=True)
 if 'mid' not in b:b['mid']=(b.bid+b.ask)/2
 if 'spread_bp' not in b:b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
 return b,t

def at(b,ts):
 i=np.searchsorted(b.ts.values,ts,'right')-1
 return None if i<0 else b.iloc[i]

def flow(t,ts,ms=1000):
 x=t[(t.ts>ts-ms)&(t.ts<=ts)]
 if len(x)==0:return 0.0
 n=(x.price*x.qty).to_numpy();sg=np.where(x.buyer_maker.to_numpy(),-n,n)
 return float(sg.sum()/(n.sum()+1e-12))

def traded_qty(t,a,z,side,px):
 x=t[(t.ts>a)&(t.ts<=z)]
 if side=='buy':x=x[(x.buyer_maker)&(x.price<=px+1e-15)]
 else:x=x[(~x.buyer_maker)&(x.price>=px-1e-15)]
 return float(x.qty.sum()) if len(x) else 0.0

def simulate(name,b,t,lat,profile,order_usd,cap_usd):
 p=PROFILES[profile]
 if len(b)<3 or len(t)<2:return None,pd.DataFrame()
 start=max(int(b.ts.min())+1000,int(t.ts.min())+1000);end=min(int(b.ts.max()),int(t.ts.max()))
 if end<=start:return None,pd.DataFrame()
 cash=0.;fees=0.;pos=0.;vol=0.;fills=[];orders={'buy':None,'sell':None}; max_inv=0.; quote_attempts=0
 times=np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64)
 prev=start
 for now in times:
  r=at(b,now)
  if r is None:continue
  # First process aggressive volume against resting quotes during (prev, now].
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   # Conservative cancellation rule: if our quoted price is no longer current touch, assume no fill after touch changed.
   touch=float(r.bid if side=='buy' else r.ask)
   if abs(touch-o['px'])>1e-15:
    orders[side]=None;continue
   q=traded_qty(t,max(prev,o['place_ts']),now,side,o['px'])
   if q<=0:continue
   o['need']-=q
   if o['need']<=0:
    notional=o['own']*o['px']
    if side=='buy':cash-=notional;pos+=o['own']
    else:cash+=notional;pos-=o['own']
    fee=notional*MAKER;fees+=fee;vol+=notional
    fills.append({'capture':name,'lat_ms':lat,'profile':profile,'order_usd':order_usd,'cap_usd':cap_usd,'ts':now,'side':side,'px':o['px'],'notional':notional,'fee':fee,'flow_entry':o['flow_entry'],'spread_entry_bp':o['spread_entry_bp'],'inventory_after_qty':pos})
    orders[side]=None
  mid=float(r.mid);inv_usd=pos*mid;max_inv=max(max_inv,abs(inv_usd))
  f=flow(t,now,1000)
  spread_ok=float(r.spread_bp)>=4.0+p['spread_buffer']
  # Cancel quotes that have become toxic or inventory-inappropriate. No cancellation credit; re-entry loses queue priority.
  buy_ok=spread_ok and f>=-p['flow_cut'] and inv_usd<cap_usd-1e-9
  sell_ok=spread_ok and f<=p['flow_cut'] and inv_usd>-cap_usd+1e-9
  # Inventory skew: once inventory exceeds half cap, stop adding to it; continue quoting the reducing side.
  if inv_usd>0.5*cap_usd:buy_ok=False
  if inv_usd<-0.5*cap_usd:sell_ok=False
  for side,ok in [('buy',buy_ok),('sell',sell_ok)]:
   if not ok:
    orders[side]=None;continue
   place=now+lat;pr=at(b,place)
   if pr is None:continue
   px=float(pr.bid if side=='buy' else pr.ask);qa=float(pr.bidq if side=='buy' else pr.askq);own=order_usd/px
   # Keep existing quote if same price; otherwise cancel-requote behind full displayed queue.
   o=orders[side]
   if o is not None and abs(o['px']-px)<=1e-15:continue
   orders[side]={'px':px,'own':own,'need':qa+own,'place_ts':place,'flow_entry':f,'spread_entry_bp':float(pr.spread_bp)};quote_attempts+=1
  prev=now
 # Final residual inventory is forcibly taker-flattened at opposite touch.
 last=at(b,end); liquidation_notional=0.; liquidation_fee=0.; liquidation_gross_cash=0.
 if last is not None and abs(pos)>1e-15:
  if pos>0:
   px=float(last.bid);notional=pos*px;cash+=notional;liquidation_gross_cash=notional
  else:
   px=float(last.ask);notional=(-pos)*px;cash-=notional;liquidation_gross_cash=-notional
  liquidation_notional=notional;liquidation_fee=notional*TAKER;fees+=liquidation_fee;vol+=notional;pos=0.
 pnl=cash-fees;net_bp_turnover=pnl/vol*1e4 if vol>0 else np.nan
 result={'capture':name,'lat_ms':lat,'profile':profile,'order_usd':order_usd,'cap_usd':cap_usd,'maker_fills':len(fills),'quote_attempts':quote_attempts,
         'filled_volume_usd':vol,'maker_volume_usd':vol-liquidation_notional,'liquidation_volume_usd':liquidation_notional,'liquidation_share':liquidation_notional/vol if vol else np.nan,
         'gross_cash_pnl':cash,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':net_bp_turnover,'max_inventory_usd':max_inv}
 return result,pd.DataFrame(fills)

rows=[];allfills=[]
for name,(bp,tp) in CAPTURES.items():
 b,t=prepare(bp,tp)
 for lat in [10,240]:
  for profile in PROFILES:
   for order_usd in [25.,100.]:
    for cap_usd in [100.,300.]:
     r,f=simulate(name,b,t,lat,profile,order_usd,cap_usd)
     if r is not None:rows.append(r)
     if len(f):allfills.append(f)
R=pd.DataFrame(rows);F=pd.concat(allfills,ignore_index=True) if allfills else pd.DataFrame();R.to_csv(f'{OUT}/summary.csv',index=False);F.to_csv(f'{OUT}/fills.csv',index=False)
# Robustness view: same parameter set aggregated across captures. Require positive aggregate AND no single capture worse than -2bp/volume for candidate labeling.
grp=[]
keys=['lat_ms','profile','order_usd','cap_usd']
for k,g in R.groupby(keys):
 total_vol=g.filled_volume_usd.sum();total_pnl=g.net_pnl_usd.sum();bp=total_pnl/total_vol*1e4 if total_vol>0 else np.nan
 grp.append(dict(zip(keys,k))|{'captures_with_volume':int((g.filled_volume_usd>0).sum()),'total_volume_usd':total_vol,'total_pnl_usd':total_pnl,'net_bp_per_volume':bp,
                               'worst_capture_bp':g.loc[g.filled_volume_usd>0,'net_bp_per_volume'].min() if (g.filled_volume_usd>0).any() else np.nan,
                               'maker_fills':int(g.maker_fills.sum()),'liquidation_share':g.liquidation_volume_usd.sum()/total_vol if total_vol else np.nan})
G=pd.DataFrame(grp).sort_values(['net_bp_per_volume','total_volume_usd'],ascending=[False,False]);G.to_csv(f'{OUT}/aggregate.csv',index=False)
cand=G[(G.captures_with_volume>=2)&(G.net_bp_per_volume>=0)&(G.worst_capture_bp>=-2.0)]
lines=['# Inventory-aware continuous maker v10','',
'- Replays four independent prospective captures: GALA v6, fresh GALA OOS, OP v9, ROBO v7.','- Quotes at current BBO and keeps queue priority while touch is unchanged; any touch change/toxicity cancellation loses priority and requotes behind the full displayed L1 queue.','- Full displayed queue + own order size must trade through; no cancellation credit.','- Maker fills are NOT force-paired into short cycles. Inventory can naturally net against later opposite maker fills, subject to a hard USD inventory cap and half-cap skew.','- Maker fee 2bp each fill. Any residual inventory at end is forcibly liquidated at opposite touch with 5bp taker fee.','- KPI = final net PnL / total genuinely filled notional, in bp. Parameter grid is exploratory; no result is deployment proof.','', '## Robust candidates','']
if len(cand):lines.append(cand.to_markdown(index=False,floatfmt='.4f'))
else:lines.append('None.')
lines+=['','## Aggregate ranking','',G.head(20).to_markdown(index=False,floatfmt='.4f'),'','## Per-capture top rows','']
parts=[]
for name in CAPTURES:
 x=R[(R.capture==name)&(R.filled_volume_usd>0)].sort_values('net_bp_per_volume',ascending=False).head(6);parts.append(x)
P=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()
if len(P):lines.append(P.to_markdown(index=False,floatfmt='.4f'))
else:lines.append('No fills.')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
