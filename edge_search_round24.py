#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools,concurrent.futures
from pathlib import Path
import numpy as np,pandas as pd,requests
CANDIDATES=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','XLMUSDT','ATOMUSDT','UNIUSDT','AAVEUSDT','FILUSDT','ALGOUSDT','EOSUSDT','NEARUSDT','SANDUSDT','MANAUSDT','AXSUSDT','GALAUSDT','RUNEUSDT','1000SHIBUSDT','OPUSDT','APTUSDT','LDOUSDT','IMXUSDT','CRVUSDT','KAVAUSDT','ZECUSDT']
MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];PAIR_FEE=.002
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/8h/{s}-8h-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r24_cache');OUT=Path('r24_output');OUT.mkdir(exist_ok=True)
def gz(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 for _ in range(3):
  try:
   r=requests.get(url,timeout=60)
   if r.status_code==404:return None
   r.raise_for_status();p.write_bytes(r.content);return p
  except Exception: pass
 return None
def rk(s,m):
 p=gz(K.format(s=s,m=m),CACHE/'k'/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
  ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d['open_time']=pd.to_numeric(d.open_time,errors='coerce');d['open']=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('8h');return d[['ts','open']].dropna().drop_duplicates('ts')
 except Exception:return None
def rf(s,m):
 p=gz(F.format(s=s,m=m),CACHE/'f'/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
  ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce');d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True).dt.floor('8h');return d[['ts','rate']].dropna().groupby('ts',as_index=False).rate.sum()
 except Exception:return None
def load(s):
 ks=[];fs=[]
 for m in MONTHS:
  a=rk(s,m);b=rf(s,m)
  if a is not None:ks.append(a)
  if b is not None:fs.append(b)
 if not ks or not fs:return None
 k=pd.concat(ks).sort_values('ts').drop_duplicates('ts');f=pd.concat(fs).sort_values('ts').groupby('ts',as_index=False).rate.sum();d=k.merge(f,on='ts',how='left').sort_values('ts').reset_index(drop=True);d['rate']=d.rate.fillna(0)
 # Predeclared robustness universe: continuous from Jan-2023 through Jul-2026.
 if d.ts.min()>pd.Timestamp('2023-01-31',tz='UTC') or d.ts.max()<pd.Timestamp('2026-07-31',tz='UTC') or len(d)<3800:return None
 print('keep',s,len(d),flush=True);return s,d
def met(z):
 if not z:return (0,np.nan,np.nan,np.nan)
 a=np.array([q[1] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf)
def main():
 data={}
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
  for res in ex.map(load,CANDIDATES):
   if res is not None:data[res[0]]=res[1]
 print('UNIVERSE',len(data),sorted(data),flush=True)
 allts=sorted(set().union(*[set(d.ts) for d in data.values()]));idx=pd.DatetimeIndex(allts);opens=pd.DataFrame(index=idx);rates=pd.DataFrame(index=idx)
 for s,d in data.items():x=d.set_index('ts');opens[s]=x.open;rates[s]=x.rate
 rows=[]
 for avg_n,spread_thr,hold in itertools.product([1,3,6],[.0001,.0002,.0005,.001],[1,2,3,6]):
  score=rates.rolling(avg_n,min_periods=avg_n).mean();tr=[];i=avg_n-1
  while i<len(idx)-hold-2:
   sc=score.iloc[i].dropna()
   if len(sc)<12:i+=1;continue
   hi=sc.idxmax();lo=sc.idxmin();spread=float(sc[hi]-sc[lo])
   if hi==lo or spread<spread_thr:i+=1;continue
   e=i+1;x=e+hold
   if idx[e]-idx[i]!=pd.Timedelta(hours=8) or idx[x]-idx[e]!=pd.Timedelta(hours=8*hold):i+=1;continue
   vals=[opens.at[idx[e],hi],opens.at[idx[x],hi],opens.at[idx[e],lo],opens.at[idx[x],lo]]
   if not np.isfinite(vals).all():i+=1;continue
   peh,pxh,pel,pxl=map(float,vals);price=(pxl/pel-1)-(pxh/peh-1);fund=float(rates[hi].iloc[e+1:x].fillna(0).sum()-rates[lo].iloc[e+1:x].fillna(0).sum());net=float(price+fund-PAIR_FEE);tr.append((idx[e].year,net,price,fund,hi,lo,spread));i=x+1
  a=met([q for q in tr if q[0] in (2023,2024)]);p=met([q for q in tr if q[0]==2023]);q=met([q for q in tr if q[0]==2024]);b=met([q for q in tr if q[0]==2025]);c=met([q for q in tr if q[0]==2026])
  rows.append(dict(avg_n=avg_n,spread_thr=spread_thr,hold=hold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],ev_2023=p[2],ev_2024=q[2],n_2025=b[0],win_2025=b[1],ev_2025=b[2],pf_2025=b[3],n_2026=c[0],win_2026=c[1],ev_2026=c[2],pf_2026=c[3],price_2026=c[2] if False else np.nan))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r24_all.csv',index=False);sel=r[(r.n_train>=100)&(r.ev_2023>0)&(r.ev_2024>0)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);rob=sel[(sel.n_2025>=40)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=25)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'r24_robust.csv',index=False);print('BEST TRAIN');print(sel.head(25).to_string(index=False));print('\nROBUST EXPANDED');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
