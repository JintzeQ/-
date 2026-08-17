#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];PAIR_FEE=.002
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/8h/{s}-8h-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r19_cache');OUT=Path('r19_output');OUT.mkdir(exist_ok=True)
def gz(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def rk(s,m):
 p=gz(K.format(s=s,m=m),CACHE/'k'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d['open_time']=pd.to_numeric(d.open_time,errors='coerce');d['open']=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('8h');return d[['ts','open']].dropna().drop_duplicates('ts')
def rf(s,m):
 p=gz(F.format(s=s,m=m),CACHE/'f'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
 ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce');d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True).dt.floor('8h');return d[['ts','rate']].dropna().groupby('ts',as_index=False).rate.sum()
def load(s):
 ks=[];fs=[]
 for m in MONTHS:
  a=rk(s,m);b=rf(s,m)
  if a is not None:ks.append(a)
  if b is not None:fs.append(b)
 if not ks or not fs:return None
 k=pd.concat(ks).sort_values('ts').drop_duplicates('ts');f=pd.concat(fs).sort_values('ts').groupby('ts',as_index=False).rate.sum();d=k.merge(f,on='ts',how='left').sort_values('ts').reset_index(drop=True);d['rate']=d.rate.fillna(0);return d

def main():
 ds={s:load(s) for s in SYMBOLS};ds={s:d for s,d in ds.items() if d is not None};print('loaded',list(ds),flush=True)
 # common 8h timeline/panels
 allts=sorted(set().union(*[set(d.ts) for d in ds.values()]));idx=pd.DatetimeIndex(allts);opens=pd.DataFrame(index=idx);rates=pd.DataFrame(index=idx)
 for s,d in ds.items():
  x=d.set_index('ts');opens[s]=x.open;rates[s]=x.rate
 rows=[]
 for avg_n,spread_thr,hold in itertools.product([1,3,6],[.0001,.0002,.0005,.001,.002],[1,2,3,6]):
  score=rates.rolling(avg_n,min_periods=avg_n).mean();tr=[];i=avg_n-1
  while i<len(idx)-hold-2:
   sc=score.iloc[i].dropna()
   if len(sc)<6:i+=1;continue
   hi=sc.idxmax();lo=sc.idxmin();spread=float(sc[hi]-sc[lo])
   if spread<spread_thr:i+=1;continue
   e=i+1;x=e+hold
   if idx[e]-idx[i]!=pd.Timedelta(hours=8) or idx[x]-idx[e]!=pd.Timedelta(hours=8*hold):i+=1;continue
   try: pe_hi=float(opens.iloc[e][hi]);px_hi=float(opens.iloc[x][hi]);pe_lo=float(opens.iloc[e][lo]);px_lo=float(opens.iloc[x][lo])
   except Exception:i+=1;continue
   if not np.isfinite([pe_hi,px_hi,pe_lo,px_lo]).all():i+=1;continue
   # long lowest funding, short highest funding
   price=(px_lo/pe_lo-1) -(px_hi/pe_hi-1)
   fr_hi=float(rates[hi].iloc[e+1:x].fillna(0).sum());fr_lo=float(rates[lo].iloc[e+1:x].fillna(0).sum());fund=fr_hi-fr_lo
   net=float(price+fund-PAIR_FEE);tr.append((idx[e].year,net,price,fund,hi,lo,spread));i=x+1
  def met(z):
   if not z:return (0,np.nan,np.nan,np.nan,np.nan,np.nan)
   a=np.array([q[1] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf
   return len(a),float((a>0).mean()),float(a.mean()),float(pf),float(np.mean([q[2] for q in z])),float(np.mean([q[3] for q in z]))
  a=met([q for q in tr if q[0] in (2023,2024)]);p=met([q for q in tr if q[0]==2023]);q=met([q for q in tr if q[0]==2024]);b=met([q for q in tr if q[0]==2025]);c=met([q for q in tr if q[0]==2026])
  rows.append(dict(avg_n=avg_n,spread_thr=spread_thr,hold=hold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],ev_2023=p[2],ev_2024=q[2],n_2025=b[0],win_2025=b[1],ev_2025=b[2],pf_2025=b[3],n_2026=c[0],win_2026=c[1],ev_2026=c[2],pf_2026=c[3],price_2026=c[4],fund_2026=c[5]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r19_all.csv',index=False);sel=r[(r.n_train>=50)&(r.ev_2023>0)&(r.ev_2024>0)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);rob=sel[(sel.n_2025>=25)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=15)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'r19_robust.csv',index=False);print('BEST TRAIN');print(sel.head(25).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
