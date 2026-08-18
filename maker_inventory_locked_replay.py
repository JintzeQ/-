import os, numpy as np, pandas as pd
OUT='maker_inventory_locked_output';os.makedirs(OUT,exist_ok=True)
CAPTURES={
'gala_v6':('maker_live_v6_output/GALAUSDT_book.csv','maker_live_v6_output/GALAUSDT_trades.csv'),
'gala_oos':('maker_gala_flip_oos_output/book.csv','maker_gala_flip_oos_output/trades.csv'),
'op_v9':('maker_live_v9_output/OPUSDT_book.csv','maker_live_v9_output/OPUSDT_trades.csv'),
'robo_v7':('maker_live_v7_output/ROBOUSDT_book.csv','maker_live_v7_output/ROBOUSDT_trades.csv')}
STEP=250;LAT=10;ORDER_USD=100.;CAP_USD=100.;MAKER=.0002;TAKER=.0005;MIN_SPREAD=4.25

def run(name,bp,tp):
 b=pd.read_csv(bp).sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True);t=pd.read_csv(tp).sort_values('ts').reset_index(drop=True)
 if 'mid' not in b.columns:b['mid']=(b.bid+b.ask)/2
 if 'spread_bp' not in b.columns:b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
 bt=b.ts.to_numpy(np.int64);bid=b.bid.to_numpy(float);ask=b.ask.to_numpy(float);bq=b.bidq.to_numpy(float);aq=b.askq.to_numpy(float);mid=b.mid.to_numpy(float);spr=b.spread_bp.to_numpy(float)
 tt=t.ts.to_numpy(np.int64);tpv=t.price.to_numpy(float);tq=t.qty.to_numpy(float);tm=t.buyer_maker.astype(bool).to_numpy()
 def bi(ts):
  i=np.searchsorted(bt,ts,'right')-1;return i if i>=0 else None
 def stable(a,z,side,px):
  lo=np.searchsorted(bt,a,'right');hi=np.searchsorted(bt,z,'right');x=(bid if side=='buy' else ask)[lo:hi]
  return True if len(x)==0 else bool(np.all(np.abs(x-px)<=1e-15))
 def qty(a,z,side,px):
  lo=np.searchsorted(tt,a,'right');hi=np.searchsorted(tt,z,'right')
  if hi<=lo:return 0.
  p=tpv[lo:hi];q=tq[lo:hi];m=tm[lo:hi];mask=(m&(p<=px+1e-15)) if side=='buy' else ((~m)&(p>=px-1e-15));return float(q[mask].sum())
 start=max(int(bt[0])+1000,int(tt[0])+1000);end=min(int(bt[-1]),int(tt[-1]));orders={'buy':None,'sell':None};cash=fees=pos=vol=0.;prev=start;fills=[];attempts=0;maxinv=0.
 for now in np.arange(((start+STEP-1)//STEP)*STEP,end+1,STEP,dtype=np.int64):
  i=bi(now)
  if i is None:continue
  for side in ['buy','sell']:
   o=orders[side]
   if o is None:continue
   a=max(prev,o['place'])
   if now<=a:continue
   if not stable(a,now,side,o['px']):orders[side]=None;continue
   o['need']-=qty(a,now,side,o['px'])
   if o['need']<=0:
    n=o['own']*o['px'];fee=n*MAKER
    if side=='buy':cash-=n;pos+=o['own']
    else:cash+=n;pos-=o['own']
    fees+=fee;vol+=n;fills.append((now,side,n,pos));orders[side]=None
  inv=pos*mid[i];maxinv=max(maxinv,abs(inv));spread_ok=spr[i]>=MIN_SPREAD
  buy_ok=spread_ok and inv<CAP_USD and inv<=0.5*CAP_USD
  sell_ok=spread_ok and inv>-CAP_USD and inv>=-0.5*CAP_USD
  for side,ok in [('buy',buy_ok),('sell',sell_ok)]:
   if not ok:orders[side]=None;continue
   place=now+LAT;j=bi(place)
   if j is None:continue
   px=bid[j] if side=='buy' else ask[j];qq=bq[j] if side=='buy' else aq[j];own=ORDER_USD/px;o=orders[side]
   if o is not None and abs(o['px']-px)<=1e-15:continue
   orders[side]={'px':float(px),'own':float(own),'need':float(qq+own),'place':int(place)};attempts+=1
  prev=now
 i=bi(end);liq=0.
 if i is not None and abs(pos)>1e-15:
  if pos>0:n=pos*bid[i];cash+=n
  else:n=(-pos)*ask[i];cash-=n
  liq=float(n);fees+=liq*TAKER;vol+=liq
 pnl=cash-fees
 return {'capture':name,'maker_fills':len(fills),'quote_attempts':attempts,'maker_volume_usd':vol-liq,'liquidation_volume_usd':liq,'total_volume_usd':vol,'liquidation_share':liq/vol if vol else np.nan,'gross_cash_pnl':cash,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan,'max_inventory_usd':maxinv}
rows=[run(n,*p) for n,p in CAPTURES.items()];R=pd.DataFrame(rows);R.to_csv(f'{OUT}/summary.csv',index=False)
tv=R.total_volume_usd.sum();pnl=R.net_pnl_usd.sum();agg=pnl/tv*1e4 if tv else np.nan
lines=['# Locked conservative inventory-maker replay','', '- Configuration frozen: 10ms, symmetric BBO quoting, $100/order, $100 inventory cap, minimum spread 4.25bp.','- No flow filter and no parameter search.','- Full displayed L1 queue + own size must trade through. Any BBO touch change while resting cancels the order and forfeits all queue progress. No cancellation credit.','- Maker fee 2bp/fill; residual inventory always taker-flattened at end with 5bp fee.','',R.to_markdown(index=False,floatfmt='.4f'),'',f'Aggregate net bp per genuine volume: **{agg:.4f} bp**',f'Aggregate volume: **${tv:.2f}**; aggregate net PnL: **${pnl:.4f}**.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
