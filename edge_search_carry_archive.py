#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT']
MONTHS=[str(p) for p in pd.period_range('2022-01','2026-07',freq='M')]
COST=.002  # 10 bps round trip on spot + 10 bps round trip on perp
SPOT='https://data.binance.vision/data/spot/monthly/klines/{s}/1h/{s}-1h-{m}.zip'
FUT='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip'
FUND='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
KCOL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('carry_cache');OUT=Path('carry_output');OUT.mkdir(exist_ok=True)
def get(url,p):
 if p.exists() and p.stat().st_size>100:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=60)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read_kline(url,p,name):
 p=get(url,p)
 if p is None:return None
 with zipfile.ZipFile(p) as z:
  n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=KCOL,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True)
 return d[['timestamp','open']].rename(columns={'open':name})
def read_funding(s,m):
 p=get(FUND.format(s=s,m=m),CACHE/'funding'/s/f'{m}.zip')
 if p is None:return None
 with zipfile.ZipFile(p) as z:
  n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)))
 d['calc_time']=pd.to_numeric(d['calc_time'],errors='coerce');d['rate']=pd.to_numeric(d['last_funding_rate'],errors='coerce');d=d.dropna(subset=['calc_time','rate']);d['timestamp']=pd.to_datetime(d.calc_time,unit='ms',utc=True).dt.floor('s')
 return d[['timestamp','rate']]
def load(s):
 sp=[];fu=[];fr=[]
 for m in MONTHS:
  a=read_kline(SPOT.format(s=s,m=m),CACHE/'spot'/s/f'{m}.zip','spot')
  b=read_kline(FUT.format(s=s,m=m),CACHE/'perp'/s/f'{m}.zip','perp')
  c=read_funding(s,m)
  if a is not None:sp.append(a)
  if b is not None:fu.append(b)
  if c is not None:fr.append(c)
 sp=pd.concat(sp,ignore_index=True).drop_duplicates('timestamp').set_index('timestamp').sort_index();fu=pd.concat(fu,ignore_index=True).drop_duplicates('timestamp').set_index('timestamp').sort_index();px=sp.join(fu,how='inner')
 f=pd.concat(fr,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);f['avg3']=f.rate.rolling(3,min_periods=3).mean();f['avg6']=f.rate.rolling(6,min_periods=6).mean()
 print(s,len(px),len(f),flush=True);return px,f
def simulate(s,px,f,signal,thr,hold_h):
 out=[];next_free=pd.Timestamp('1900-01-01',tz='UTC')
 col={'LAST':'rate','AVG3':'avg3','AVG6':'avg6'}[signal]
 for _,r in f.iterrows():
  t=r.timestamp
  if t<next_free:continue
  observed=float(r[col]) if np.isfinite(r[col]) else np.nan
  if not np.isfinite(observed) or observed<thr:continue
  entry=t.ceil('h')+pd.Timedelta(hours=1);exit_t=entry+pd.Timedelta(hours=hold_h)
  if entry not in px.index or exit_t not in px.index:continue
  sp0=float(px.at[entry,'spot']);sp1=float(px.at[exit_t,'spot']);fp0=float(px.at[entry,'perp']);fp1=float(px.at[exit_t,'perp'])
  fund=float(f.loc[(f.timestamp>entry)&(f.timestamp<=exit_t),'rate'].sum())
  basis=(sp1/sp0-1)-(fp1/fp0-1)
  net=basis+fund-COST
  out.append((entry.year,s,net,basis,fund,observed,hold_h));next_free=exit_t
 return out
def met(z):
 a=np.array([q[2] for q in z],float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan,0,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for _,s,p,*_ in z:ss.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.mean([q[3] for q in z])),float(np.mean([q[4] for q in z]))
def main():
 data={s:load(s) for s in SYMBOLS};rows=[]
 for signal,thr,hold in itertools.product(['LAST','AVG3','AVG6'],[0,.00005,.0001,.0002,.0005],[72,168,336,720]):
  tr=[]
  for s,(px,f) in data.items():tr+=simulate(s,px,f,signal,thr,hold)
  train=[q for q in tr if q[0] in (2022,2023)];a=met(train);b=met([q for q in tr if q[0]==2024]);c=met([q for q in tr if q[0]==2025]);d=met([q for q in tr if q[0]==2026])
  rows.append(dict(signal=signal,threshold=thr,hold_h=hold,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],basis_train=a[5],fund_train=a[6],n_2024=b[0],win_2024=b[1],ev_2024=b[2],pf_2024=b[3],n_2025=c[0],win_2025=c[1],ev_2025=c[2],pf_2025=c[3],n_2026=d[0],win_2026=d[1],ev_2026=d[2],pf_2026=d[3]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'carry_all.csv',index=False)
 sel=r[(r.n_train>=30)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','win_train'],ascending=False);sel.to_csv(OUT/'carry_train_positive.csv',index=False)
 rob=sel[(sel.n_2024>=15)&(sel.ev_2024>0)&(sel.pf_2024>1)&(sel.n_2025>=15)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=8)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025','ev_2024'],ascending=False);rob.to_csv(OUT/'carry_robust.csv',index=False)
 print('BEST TRAIN');print(r.sort_values('ev_train',ascending=False).head(25).to_string(index=False));print('\nROBUST 2024/25/26');print(rob.head(30).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
