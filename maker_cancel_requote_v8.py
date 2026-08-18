import os, math
import numpy as np
import pandas as pd

SYMS=['WOOUSDT','CHZUSDT','GALAUSDT']
OUT='maker_cancel_requote_v8_output'; os.makedirs(OUT,exist_ok=True)
ORDER_USD=100.0; STEP=250; MAKER_FEE=2.0; TAKER_FEE=5.0
PROFILES={
 'protective': {'max_qsec':3.0,'min_buffer':1.0,'neutral':False,'hardstop':30_000},
 'neutral': {'max_qsec':2.0,'min_buffer':1.0,'neutral':True,'hardstop':30_000},
 'strict': {'max_qsec':1.0,'min_buffer':2.0,'neutral':False,'hardstop':20_000},
}

def at(b,ts):
    i=np.searchsorted(b.ts.values,ts,'right')-1
    return None if i<0 else b.iloc[i]

def flow_stats(t,ts,lookback):
    a=t.ts.values; lo=np.searchsorted(a,ts-lookback,'left'); hi=np.searchsorted(a,ts,'right'); x=t.iloc[lo:hi]
    if len(x)==0:return 0.,0.,0.,0
    n=(x.price*x.qty).values; sg=np.where(x.buyer_maker.values,-n,n)
    sell=float(x.loc[x.buyer_maker,'qty'].sum()/(lookback/1000)); buy=float(x.loc[~x.buyer_maker,'qty'].sum()/(lookback/1000))
    return float(sg.sum()/(n.sum()+1e-12)),sell,buy,len(x)

def fill_window(t,start,end,side,px,queue,own):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right'); need=float(queue)+float(own); done=0.
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None

def gross_bp(side,entry,exit):
    return ((exit-entry)/entry*1e4) if side=='buy' else ((entry-exit)/entry*1e4)

def simulate(s,lat,name):
    p=PROFILES[name]
    b=pd.read_csv(f'maker_live_v6_output/{s}_book.csv').sort_values('ts').drop_duplicates('ts',keep='last').reset_index(drop=True)
    t=pd.read_csv(f'maker_live_v6_output/{s}_trades.csv').sort_values('ts').reset_index(drop=True)
    if len(b)<2 or len(t)<2:return pd.DataFrame(),0
    b['mid']=(b.bid+b.ask)/2; b['spread_bp']=(b.ask-b.bid)/b.mid*1e4
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max()));
    times=np.arange(((start+STEP-1)//STEP)*STEP,end,STEP,dtype=np.int64)
    pos=None; cycles=[]; attempts=0
    for sig in times:
        r=at(b,sig)
        if r is None:continue
        if pos is not None:
            # Hard inventory stop. Mark as maker-entry/taker-exit and count all costs.
            if sig-pos['fill_ts']>=p['hardstop']:
                ex=at(b,sig+lat)
                if ex is None:continue
                xpx=float(ex.bid if pos['side']=='buy' else ex.ask); g=gross_bp(pos['side'],pos['entry'],xpx)
                cycles.append({**pos,'close_ts':sig+lat,'exit_px':xpx,'gross_bp':g,'fees_bp':MAKER_FEE+TAKER_FEE,'net_bp':g-(MAKER_FEE+TAKER_FEE),'exit_mode':'MT_stop'})
                pos=None;continue
            # Maker-only exit is posted for just one 250ms window, then cancelled/reassessed.
            place=sig+lat; pr=at(b,place)
            if pr is None:continue
            es='sell' if pos['side']=='buy' else 'buy'; epx=float(pr.ask if es=='sell' else pr.bid); eg=gross_bp(pos['side'],pos['entry'],epx)
            if eg<4.0:continue  # do not voluntarily lock a negative after-fee maker cycle
            eq=float(pr.askq if es=='sell' else pr.bidq); own=ORDER_USD/epx
            ft=fill_window(t,place,min(place+STEP,end),es,epx,eq,own)
            if ft is not None:
                cycles.append({**pos,'close_ts':ft,'exit_px':epx,'gross_bp':eg,'fees_bp':4.0,'net_bp':eg-4.0,'exit_mode':'MM'})
                pos=None
            continue
        if r.spread_bp-4.0<p['min_buffer']:continue
        f5,sell,buy,n5=flow_stats(t,sig,5000); f1,_,_,n1=flow_stats(t,sig,1000)
        if n5<2:continue
        qb=float(r.bidq)/(sell+1e-12); qs=float(r.askq)/(buy+1e-12)
        side=None; qsec=None
        if p['neutral']:
            if abs(f5)>0.20 or abs(f1)>0.35:continue
            if min(qb,qs)>p['max_qsec']:continue
            side,qsec=('buy',qb) if qb<=qs else ('sell',qs)
        else:
            # Quote on the side protected by dominant flow; require the last 1s not to flip toxic.
            if f5>=0 and f1>=-0.10 and qb<=p['max_qsec']:side,qsec='buy',qb
            elif f5<0 and f1<=0.10 and qs<=p['max_qsec']:side,qsec='sell',qs
            else:continue
        place=sig+lat; pr=at(b,place)
        if pr is None:continue
        px=float(pr.bid if side=='buy' else pr.ask); q=float(pr.bidq if side=='buy' else pr.askq); own=ORDER_USD/px; attempts+=1
        # The order is exposed for only 250ms. If not filled it is cancelled and all features are recomputed.
        ft=fill_window(t,place,min(place+STEP,end),side,px,q,own)
        if ft is not None:
            pos={'symbol':s,'lat_ms':lat,'profile':name,'side':side,'signal_ts':sig,'fill_ts':ft,'entry':px,'qsec_entry':qsec,'flow5_entry':f5,'flow1_entry':f1,'spread_entry_bp':float(pr.spread_bp)}
    # No free option on residual inventory: liquidate at end with taker and include loss/profit.
    if pos is not None:
        ex=at(b,end)
        if ex is not None:
            xpx=float(ex.bid if pos['side']=='buy' else ex.ask); g=gross_bp(pos['side'],pos['entry'],xpx)
            cycles.append({**pos,'close_ts':end,'exit_px':xpx,'gross_bp':g,'fees_bp':7.0,'net_bp':g-7.0,'exit_mode':'MT_end'})
    return pd.DataFrame(cycles),attempts

rows=[]; allc=[]
for s in SYMS:
  for lat in [10,240]:
    for name in PROFILES:
      d,att=simulate(s,lat,name)
      if len(d):
        allc.append(d); x=d.net_bp.to_numpy(); se=x.std(ddof=1)/math.sqrt(len(x)) if len(x)>1 else np.nan
        rows.append({'symbol':s,'lat_ms':lat,'profile':name,'quote_attempts':att,'cycles':len(d),'mean_net_bp':x.mean(),'total_net_bp':x.sum(),'mean_gross_bp':d.gross_bp.mean(),'mm_exit_rate':(d.exit_mode=='MM').mean(),'win_net':(x>=0).mean(),'ci95_low':x.mean()-1.96*se if np.isfinite(se) else np.nan,'roundtrip_volume_usd':2*ORDER_USD*len(d)})
      else:
        rows.append({'symbol':s,'lat_ms':lat,'profile':name,'quote_attempts':att,'cycles':0,'mean_net_bp':np.nan,'total_net_bp':0.,'mean_gross_bp':np.nan,'mm_exit_rate':np.nan,'win_net':np.nan,'ci95_low':np.nan,'roundtrip_volume_usd':0.})
R=pd.DataFrame(rows); C=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame();R.to_csv(f'{OUT}/summary.csv',index=False);C.to_csv(f'{OUT}/cycles.csv',index=False)
valid=R[R.cycles>0].sort_values(['mean_net_bp','cycles'],ascending=[False,False]);pos=valid[valid.mean_net_bp>=0]
lines=['# Cancel-requote maker v8','', '- Uses the same untouched 180s current-market capture as v6.','- Every entry/exit maker quote lives for only 250ms, then is cancelled and all flow/queue conditions are recomputed.','- Full displayed L1 queue + own $100 size must trade through inside that 250ms window; no cancellation credit.','- Maker exit is accepted only if its locked gross spread is >=4bp.','- Residual inventory is never ignored: hard-stop/end liquidation uses taker, total fee 7bp.','- 10ms vs 240ms order-to-exchange latency tested explicitly.','', '## Results','',R.to_markdown(index=False,floatfmt='.3f'),'','## Net-positive configurations with completed cycles','']
if len(pos):lines.append(pos.to_markdown(index=False,floatfmt='.3f'))
else:lines.append('None.')
if len(C):lines+=['','## Cycles','',C.to_markdown(index=False,floatfmt='.4f')]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
