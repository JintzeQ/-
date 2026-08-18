import os, math
import numpy as np
import pandas as pd

SYMS={'WOOUSDT':0.00001,'CHZUSDT':0.00001,'GALAUSDT':0.000001}
ORDER_USD=100.0; OUT='maker_inside_spread_v7_output'; os.makedirs(OUT,exist_ok=True)
ENTRY_TTL=1000  # cancel quickly if the transient spread opportunity disappears
FEE_BP=7.0      # maker entry 2bp + immediate taker flatten 5bp


def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]

def flow5(t,ts):
    a=t.ts.values; lo=np.searchsorted(a,ts-5000,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.0,0.0,0.0
    n=(x.price*x.qty).values; sg=np.where(x.buyer_maker.values,-n,n)
    return float(sg.sum()/(n.sum()+1e-12)),float(x.loc[x.buyer_maker,'qty'].sum()/5),float(x.loc[~x.buyer_maker,'qty'].sum()/5)

def first_price_change(b,start,bid,ask,end):
    a=b.ts.values; i=np.searchsorted(a,start,'right')
    while i<len(b) and int(b.ts.iloc[i])<=end:
        r=b.iloc[i]
        if float(r.bid)!=bid or float(r.ask)!=ask:return int(r.ts)
        i+=1
    return end

def maker_fill_zero_queue(t,start,end,side,px,own):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right'); done=0.0
    for r in t.iloc[lo:hi].itertuples(index=False):
        # We improve into an empty visible price level, so only our own size needs to execute.
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=own:return int(r.ts)
    return None

def run_one(s,lat,mode):
    tick=SYMS[s]
    b=pd.read_csv(f'maker_live_v6_output/{s}_book.csv').sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
    t=pd.read_csv(f'maker_live_v6_output/{s}_trades.csv').sort_values('ts').reset_index(drop=True)
    if len(b)<2 or len(t)<2:return pd.DataFrame(),0
    b['mid']=(b.bid+b.ask)/2; b['spread_ticks']=(b.ask-b.bid)/tick
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max())); free=start; out=[]; signals=0
    # react only to BBO updates: avoids repeatedly treating the same wide spread as a new opportunity
    for rr in b.itertuples(index=False):
        sig=int(rr.ts)
        if sig<start or sig<free or sig>=end:continue
        if (rr.ask-rr.bid)<2*tick-1e-12:continue
        place=sig+lat; p=at(b,place)
        if p is None or (p.ask-p.bid)<2*tick-1e-12:continue
        fl,sellrate,buyrate=flow5(t,sig)
        if mode=='protect':
            # quote away from dominant toxic aggressor flow
            side='buy' if fl>=0 else 'sell'
        else:
            # only act in roughly balanced flow; choose the side with more opposite-side arrival rate for turnover
            if abs(fl)>0.20:continue
            side='buy' if sellrate>=buyrate else 'sell'
        px=float(p.bid+tick if side=='buy' else p.ask-tick)
        # Must still improve the spread by exactly one tick at actual placement time.
        if not (p.bid+1e-12<px<p.ask-1e-12):continue
        own=ORDER_USD/px; signals+=1
        cancel=min(place+ENTRY_TTL, first_price_change(b,place,float(p.bid),float(p.ask),end))
        ft=maker_fill_zero_queue(t,place,cancel,side,px,own)
        if ft is None:
            free=cancel;continue
        # Immediately flatten after order-to-exchange latency at opposite touch as taker.
        ex=at(b,ft+lat)
        if ex is None:continue
        exit_px=float(ex.bid if side=='buy' else ex.ask)
        gross=((exit_px-px)/px*1e4)*(1 if side=='buy' else -1)
        out.append({'symbol':s,'lat_ms':lat,'mode':mode,'signal_ts':sig,'place_ts':place,'fill_ts':ft,'side':side,'entry_px':px,'exit_px':exit_px,'spread_ticks_at_place':float((p.ask-p.bid)/tick),'flow5':fl,'gross_bp':gross,'fees_bp':FEE_BP,'net_bp':gross-FEE_BP})
        free=ft+lat
    return pd.DataFrame(out),signals

rows=[]; cycles=[]
for s in SYMS:
  for lat in [10,240]:
    for mode in ['protect','neutral']:
      d,sigs=run_one(s,lat,mode)
      if len(d):
        cycles.append(d); x=d.net_bp.to_numpy(); se=x.std(ddof=1)/math.sqrt(len(x)) if len(x)>1 else np.nan
        rows.append({'symbol':s,'lat_ms':lat,'mode':mode,'signals':sigs,'fills':len(d),'fill_rate':len(d)/max(sigs,1),'mean_gross_bp':d.gross_bp.mean(),'mean_net_bp':x.mean(),'total_net_bp':x.sum(),'win_net':(x>=0).mean(),'ci95_low_net':x.mean()-1.96*se if np.isfinite(se) else np.nan,'roundtrip_volume_usd':2*ORDER_USD*len(d)})
      else:
        rows.append({'symbol':s,'lat_ms':lat,'mode':mode,'signals':sigs,'fills':0,'fill_rate':0.0,'mean_gross_bp':np.nan,'mean_net_bp':np.nan,'total_net_bp':0.0,'win_net':np.nan,'ci95_low_net':np.nan,'roundtrip_volume_usd':0.0})
R=pd.DataFrame(rows); C=pd.concat(cycles,ignore_index=True) if cycles else pd.DataFrame()
R.to_csv(f'{OUT}/summary.csv',index=False); C.to_csv(f'{OUT}/cycles.csv',index=False)
valid=R[R.fills>0].sort_values(['mean_net_bp','fills'],ascending=[False,False]); pos=valid[valid.mean_net_bp>=0]
lines=['# Inside-spread maker v7','', '- Uses the untouched 180s current-market capture from v6.','- Signal: visible spread >=2 ticks. After 10ms/240ms latency, post one tick inside the spread.','- Because the order creates a new best visible price, queue-ahead is modeled as zero; own $100 size must still fully execute.','- Entry quote is cancelled after 1s or immediately when the historical BBO changes. No fill = no trade.','- On maker fill, immediately taker-flatten after latency; fee hurdle = 7bp round trip.','- `protect` quotes away from dominant 5s aggressor flow; `neutral` only acts when |flow|<=0.20.','- Fixed rules; no parameter search on the sample.','', '## Results','',R.to_markdown(index=False,floatfmt='.3f'),'','## Net-positive configurations with at least one actual modeled fill','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
if len(C):
    lines+=['','## Completed cycles','',C.to_markdown(index=False,floatfmt='.6f')]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
