#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];FEE=.001
PERP='https://data.binance.vision/data/futures/um/monthly/klines/{s}/8h/{s}-8h-{m}.zip';FUND='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r17_cache');OUT=Path('r17_output');OUT.mkdir(exist_ok=True)
def getzip(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read_k(s,m):
 p=getzip(PERP.format(s=s,m=m),CACHE/'perp'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d['open_time']=pd.to_numeric(d.open_time,errors='coerce');d['open']=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('8h');return d[['ts','open']].dropna().drop_duplicates('ts')
def read_f(s,m):
 p=getzip(FUND.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
 ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce');d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True).dt.floor('8h');return d[['ts','rate']].dropna().drop_duplicates('ts')
def load(s):
 ks=[];fs=[]
 for m in MONTHS:
  a=read_k(s,m);b=read_f(s,m)
  if a is not None:ks.append(a)
  if b is not None:fs.append(b)
 if not ks or not fs:return None
 k=pd.concat(ks).sort_values('ts').drop_duplicates('ts');f=pd.concat(fs).sort_values('ts').drop_duplicates('ts');d=k.merge(f,on='ts',how='left').sort_values('ts').reset_index(drop=True);d['rate']=d.rate.fillna(0);d['year']=d.ts.dt.year.astype(np.int16);d['symbol']=s;print(s,len(d),int((d.rate!=0).sum()),flush=True);return d
def simulate(d,n,thr,hold):
 av=d.rate.rolling(n,min_periods=n).mean().to_numpy();r=d.rate.to_numpy();op=d.open.to_numpy();ts=d.ts.to_numpy();yr=d.year.to_numpy();out=[];i=n-1
 while i<len(d)-hold-2:
  side=-1 if av[i]>=thr else (1 if av[i]<=-thr else 0)
  if not side:i+=1;continue
  e=i+1;x=e+hold
  if any((pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=28800 for j in range(e,x+1)):i+=1;continue
  price=side*(op[x]/op[e]-1);fund=-side*float(np.nansum(r[e+1:x]));net=float(price+fund-FEE);out.append((int(yr[e]),str(d.symbol.iloc[e]),net,price,fund,side));i=x+1
 return out
def met(z):
 if not z:return (0,np.nan,np.nan,np.nan,0,np.nan,np.nan,np.nan)
 a=np.array([q[2] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for q in z:ss.setdefault(q[1],[]).append(q[2])
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.mean([q[3] for q in z])),float(np.mean([q[4] for q in z])),float(np.mean([q[5] for q in z]))
def main():
 data={s:load(s) for s in SYMBOLS};data={s:d for s,d in data.items() if d is not None};rows=[]
 for n,thr,hold in itertools.product([1,3,6],[.0001,.0002,.0003,.0005,.001],[1,2,3,6]):
  tr=[]
  for d in data.values():tr+=simulate(d,n,thr,hold)
  a=met([q for q in tr if q[0] in (2023,2024)]);y23=met([q for q in tr if q[0]==2023]);y24=met([q for q in tr if q[0]==2024]);b=met([q for q in tr if q[0]==2025]);c=met([q for q in tr if q[0]==2026])
  rows.append(dict(avg_n=n,thr=thr,hold_bars=hold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],ev_2023=y23[2],ev_2024=y24[2],n_2025=b[0],win_2025=b[1],ev_2025=b[2],pf_2025=b[3],pos_symbols_2025=b[4],n_2026=c[0],win_2026=c[1],ev_2026=c[2],pf_2026=c[3],pos_symbols_2026=c[4],price_2026=c[5],fund_2026=c[6]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r17_all.csv',index=False);sel=r[(r.n_train>=60)&(r.ev_2023>0)&(r.ev_2024>0)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);rob=sel[(sel.n_2025>=25)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=15)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'r17_robust.csv',index=False);print('BEST TRAIN');print(sel.head(25).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
