import os, numpy as np, pandas as pd
OUT='maker_gala_context_output';os.makedirs(OUT,exist_ok=True)
b=pd.read_csv('maker_gala_flip_oos_output/book.csv').sort_values('ts').reset_index(drop=True)
t=pd.read_csv('maker_gala_flip_oos_output/trades.csv').sort_values('ts').reset_index(drop=True)
c=pd.read_csv('maker_gala_flip_oos_output/cycles.csv')

def at(ts):
 i=np.searchsorted(b.ts.values,ts,'right')-1;return None if i<0 else b.iloc[i]
def flow(ts,ms):
 x=t[(t.ts>ts-ms)&(t.ts<=ts)]
 if len(x)==0:return 0.,0.,0.
 n=(x.price*x.qty).to_numpy();sg=np.where(x.buyer_maker.to_numpy(),-n,n)
 buy=float(x.loc[~x.buyer_maker,'qty'].sum()/(ms/1000));sell=float(x.loc[x.buyer_maker,'qty'].sum()/(ms/1000))
 return float(sg.sum()/(n.sum()+1e-12)),buy,sell
rows=[]
for r in c.itertuples(index=False):
 s=int(r.signal_ts);m=at(s);m5=at(s-5000);m15=at(s-15000)
 if m is None or m5 is None or m15 is None:continue
 ret5=(m.mid-m5.mid)/m5.mid*1e4;ret15=(m.mid-m15.mid)/m15.mid*1e4;f5,buy,sell=flow(s,5000)
 # Exit-side queue-turnover proxy at signal: long later needs ask maker filled by buys; short needs bid filled by sells.
 qsec=(m.askq/(buy+1e-12)) if r.side=='buy' else (m.bidq/(sell+1e-12))
 aligned5=(ret5>=0 if r.side=='buy' else ret5<=0);aligned15=(ret15>=0 if r.side=='buy' else ret15<=0)
 flowalign=(f5>=0 if r.side=='buy' else f5<=0)
 rows.append({**r._asdict(),'ret5_bp':ret5,'ret15_bp':ret15,'flow5':f5,'exit_qsec_proxy':qsec,'aligned5':aligned5,'aligned15':aligned15,'flowalign':flowalign})
D=pd.DataFrame(rows);D.to_csv(f'{OUT}/enriched_cycles.csv',index=False)
profiles={
 'all':np.ones(len(D),dtype=bool),
 'trend5':D.aligned5,
 'trend15':D.aligned15,
 'trend5_flow':D.aligned5&D.flowalign,
 'trend15_flow':D.aligned15&D.flowalign,
 'trend5_q5':D.aligned5&(D.exit_qsec_proxy<=5),
 'trend5_q2':D.aligned5&(D.exit_qsec_proxy<=2),
 'trend15_q5':D.aligned15&(D.exit_qsec_proxy<=5),
 'trend15_q2':D.aligned15&(D.exit_qsec_proxy<=2),
}
out=[]
for lat in [10,240]:
 x=D[D.lat_ms==lat]
 for name,maskfull in profiles.items():
  mask=maskfull.loc[x.index] if hasattr(maskfull,'loc') else pd.Series(maskfull,index=D.index).loc[x.index]
  y=x[mask]
  out.append({'lat_ms':lat,'profile':name,'n':len(y),'mean_net_bp':y.net_bp.mean() if len(y) else np.nan,'total_net_bp':y.net_bp.sum() if len(y) else 0.,'mm_exit_rate':(y.exit_mode=='MM').mean() if len(y) else np.nan,'win_net':(y.net_bp>=0).mean() if len(y) else np.nan,'mean_ret5':y.ret5_bp.mean() if len(y) else np.nan,'mean_qsec':y.exit_qsec_proxy.mean() if len(y) else np.nan})
R=pd.DataFrame(out);R.to_csv(f'{OUT}/summary.csv',index=False)
valid=R[R.n>=2].sort_values(['mean_net_bp','n'],ascending=[False,False])
lines=['# GALA sweep-flip context screen','', '- Exploratory post-analysis of the fresh GALA OOS capture; this is NOT a new OOS result.','- Tests whether successful round trips cluster when the fade direction is aligned with the preceding 5s/15s mid trend and/or the prospective maker-exit side has low queue-seconds based on prior 5s aggressor rate.','- No flow-entry thresholds or exit accounting are changed. Any promising context rule must be frozen and re-run on another fresh capture.','', '## Results (n>=2)','',valid.to_markdown(index=False,floatfmt='.3f'),'','## Enriched 10ms cycles','',D[D.lat_ms==10][['side','net_bp','exit_mode','ret5_bp','ret15_bp','flow5','exit_qsec_proxy','aligned5','aligned15','flowalign']].to_markdown(index=False,floatfmt='.3f')]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
