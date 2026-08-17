#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];PAIR_FEE=.002
PERP='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';SPOT='https://data.binance.vision/data/spot/monthly/klines/{s}/1h/{s}-1h-{m}.zip';FUND='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r21_cache');OUT=Path('r21_output');OUT.mkdir(exist_ok=True)
def gz(url,p):
 if p.exists() and p.stat().st_size>300:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def rk(s,m,kind):
 url=(PERP if kind=='p' else SPOT).format(s=s,m=m);p=gz(url,CACHE/kind/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('h');return d[['ts','open','close']].dropna().drop_duplicates('ts')
def rf(s,m):
 p=gz(FUND.format(s=s,m=m),CACHE/'f'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
 ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce');d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True).dt.floor('h');return d[['ts','rate']].dropna().groupby('ts',as_index=False).rate.sum()
def load(s):
 ps=[];ss=[];fs=[]
 for m in MONTHS:
  a=rk(s,m,'p');b=rk(s,m,'s');c=rf(s,m)
  if a is not None:ps.append(a)
  if b is not None:ss.append(b)
  if c is not None:fs.append(c)
 if not ps or not ss or not fs:return None
 p=pd.concat(ps).drop_duplicates('ts').sort_values('ts').rename(columns={'open':'po','close':'pc'});q=pd.concat(ss).drop_duplicates('ts').sort_values('ts').rename(columns={'open':'so','close':'sc'});f=pd.concat(fs).sort_values('ts').groupby('ts',as_index=False).rate.sum();d=p.merge(q,on='ts').merge(f,on='ts',how='left').sort_values('ts').reset_index(drop=True);d['rate']=d.rate.fillna(0);d['basis']=d.pc/d.sc-1;d['year']=d.ts.dt.year.astype(np.int16);d['symbol']=s;print(s,len(d),flush=True);return d
def sim(d,entry,exit_frac,maxhold):
 b=d.basis.to_numpy();po=d.po.to_numpy();so=d.so.to_numpy();r=d.rate.to_numpy();ts=d.ts.to_numpy();yr=d.year.to_numpy();out=[];i=1
 while i<len(d)-2:
  if not np.isfinite(b[i]) or b[i]<entry:i+=1;continue
  e=i+1
  if pd.Timestamp(ts[e])-pd.Timestamp(ts[i])!=pd.Timedelta(hours=1):i+=1;continue
  x=None
  for j in range(e+1,min(e+maxhold,len(d)-1)):
   if pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])!=pd.Timedelta(hours=1):x=j-1;break
   if np.isfinite(b[j]) and b[j]<=entry*exit_frac:x=j+1 if j+1<len(d) else j;break
  if x is None:x=min(e+maxhold,len(d)-1)
  if x<=e:i+=1;continue
  basis_pnl=(so[x]/so[e]-1)-(po[x]/po[e]-1);fund=float(np.nansum(r[e+1:x]));net=float(basis_pnl+fund-PAIR_FEE);out.append((int(yr[e]),str(d.symbol.iloc[e]),net,basis_pnl,fund,x-e,float(b[i])));i=x+1
 return out
def met(z):
 if not z:return (0,np.nan,np.nan,np.nan,0,np.nan,np.nan,np.nan)
 a=np.array([q[2] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for q in z:ss.setdefault(q[1],[]).append(q[2])
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.median([q[5] for q in z])),float(np.mean([q[3] for q in z])),float(np.mean([q[4] for q in z]))
def main():
 data={s:load(s) for s in SYMBOLS};data={s:d for s,d in data.items() if d is not None};rows=[]
 for entry,xf,maxhold in itertools.product([.0015,.002,.003,.005,.01],[0,.25,.5],[24,72,168]):
  tr=[]
  for d in data.values():tr+=sim(d,entry,xf,maxhold)
  a=met([q for q in tr if q[0] in (2023,2024)]);p=met([q for q in tr if q[0]==2023]);q=met([q for q in tr if q[0]==2024]);y25=met([q for q in tr if q[0]==2025]);y26=met([q for q in tr if q[0]==2026])
  rows.append(dict(entry_basis=entry,exit_frac=xf,maxhold=maxhold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],ev_2023=p[2],ev_2024=q[2],n_2025=y25[0],win_2025=y25[1],ev_2025=y25[2],pf_2025=y25[3],pos_symbols_2025=y25[4],n_2026=y26[0],win_2026=y26[1],ev_2026=y26[2],pf_2026=y26[3],pos_symbols_2026=y26[4],medhold_2026=y26[5],basis_pnl_2026=y26[6],fund_2026=y26[7]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r21_all.csv',index=False);sel=r[(r.n_train>=50)&(r.ev_2023>0)&(r.ev_2024>0)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);rob=sel[(sel.n_2025>=20)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=12)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'r21_robust.csv',index=False);print('BEST TRAIN');print(sel.head(30).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
