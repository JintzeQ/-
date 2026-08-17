#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];COST=.002
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('fsp_cache');OUT=Path('fsp_output');OUT.mkdir(exist_ok=True)
def get(url,p):
 if p.exists() and p.stat().st_size>100:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=60)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def load(s):
 ps=[];fs=[]
 for m in MONTHS:
  p=get(K.format(s=s,m=m),CACHE/'px'/s/f'{m}.zip')
  if p:
   with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COL,low_memory=False)
   ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);ps.append(d[['timestamp','open']])
  q=get(F.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
  if q:
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)))
   x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True).dt.floor('s');x['rate']=pd.to_numeric(x.last_funding_rate);fs.append(x[['timestamp','rate']])
 px=pd.concat(ps).drop_duplicates('timestamp').set_index('timestamp').sort_index().open;fr=pd.concat(fs).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);fr['avg3']=fr.rate.rolling(3,min_periods=3).mean();print(s,len(px),len(fr),flush=True);return px,fr
def met(z):
 a=np.array([q[1] for q in z],float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf),float(np.mean([q[2] for q in z])),float(np.mean([q[3] for q in z]))
def main():
 data={s:load(s) for s in SYMS};rows=[]
 # Build exact funding timestamp union and lookup tables.
 times=sorted(set().union(*[set(fr.timestamp) for _,fr in data.values()]))
 lookup={s:fr.set_index('timestamp') for s,(_,fr) in data.items()}
 for signal,mins,hold in itertools.product(['LAST','AVG3'],[0,.00005,.0001,.0002,.0005,.001],[24,72,168,336]):
  trades=[];nextfree=pd.Timestamp('1900-01-01',tz='UTC');col='rate' if signal=='LAST' else 'avg3'
  for t in times:
   if t<nextfree:continue
   vals={}
   for s in SYMS:
    fr=lookup[s]
    if t in fr.index:
     v=fr.at[t,col]
     if isinstance(v,pd.Series):v=v.iloc[-1]
     if np.isfinite(v):vals[s]=float(v)
   if len(vals)<6:continue
   hi=max(vals,key=vals.get);lo=min(vals,key=vals.get);spread=vals[hi]-vals[lo]
   if spread<mins:continue
   entry=t.ceil('h')+pd.Timedelta(hours=1);exit=entry+pd.Timedelta(hours=hold);pxH=data[hi][0];pxL=data[lo][0]
   if entry not in pxH.index or exit not in pxH.index or entry not in pxL.index or exit not in pxL.index:continue
   price=(float(pxL.at[exit]/pxL.at[entry]-1)-float(pxH.at[exit]/pxH.at[entry]-1))
   frH=data[hi][1];frL=data[lo][1];fund=float(frH.loc[(frH.timestamp>entry)&(frH.timestamp<=exit),'rate'].sum()-frL.loc[(frL.timestamp>entry)&(frL.timestamp<=exit),'rate'].sum())
   net=price+fund-COST;trades.append((entry.year,net,price,fund,spread,hi,lo));nextfree=exit
  a=met([q for q in trades if q[0]==2023]);b=met([q for q in trades if q[0]==2024]);c=met([q for q in trades if q[0]==2025]);d=met([q for q in trades if q[0]==2026])
  rows.append(dict(signal=signal,minspread=mins,hold_h=hold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],price_train=a[4],fund_train=a[5],n_2024=b[0],win_2024=b[1],ev_2024=b[2],pf_2024=b[3],n_2025=c[0],win_2025=c[1],ev_2025=c[2],pf_2025=c[3],n_2026=d[0],win_2026=d[1],ev_2026=d[2],pf_2026=d[3]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'fsp_all.csv',index=False);sel=r[(r.n_train>=20)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','win_train'],ascending=False);rob=sel[(sel.n_2024>=15)&(sel.ev_2024>0)&(sel.pf_2024>1)&(sel.n_2025>=15)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=8)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'fsp_robust.csv',index=False);print('BEST');print(r.sort_values('ev_train',ascending=False).head(25).to_string(index=False));print('\nROBUST');print(rob.head(30).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
