#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,time,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT'];MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')]
# Two legs: spot + perp. User's 10 bps round-trip per traded instrument -> 20 bps combined.
COST=.002
SPOT='https://data.binance.vision/data/spot/monthly/klines/{s}/1h/{s}-1h-{m}.zip';FUT='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r7_cache');OUT=Path('r7_output');OUT.mkdir(exist_ok=True)
def dl(url,p):
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 for k in range(4):
  try:
   r=requests.get(url,timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
  except Exception:
   if k==3:raise
   time.sleep(2*(k+1))
def read_zip(p):
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce')
 med=float(d.open_time.median());unit='us' if med>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open']].dropna()
def prices(s):
 sf=[];ff=[]
 for m in MONTHS:
  sf.append(read_zip(dl(SPOT.format(s=s,m=m),CACHE/'spot'/s/f'{m}.zip')).rename(columns={'open':'spot'}))
  ff.append(read_zip(dl(FUT.format(s=s,m=m),CACHE/'fut'/s/f'{m}.zip')).rename(columns={'open':'perp'}))
 a=pd.concat(sf).drop_duplicates('timestamp').set_index('timestamp');b=pd.concat(ff).drop_duplicates('timestamp').set_index('timestamp');x=a.join(b,how='inner').sort_index();return x
def funding(s):
 start=int(pd.Timestamp('2025-01-01',tz='UTC').timestamp()*1000);end=int(pd.Timestamp('2026-08-01',tz='UTC').timestamp()*1000);rows=[];cur=start
 while cur<end:
  r=requests.get('https://fapi.binance.com/fapi/v1/fundingRate',params={'symbol':s,'startTime':cur,'endTime':end,'limit':1000},timeout=60);r.raise_for_status();z=r.json()
  if not z:break
  rows.extend(z);nxt=int(z[-1]['fundingTime'])+1
  if nxt<=cur:break
  cur=nxt
  if len(z)<1000:break
 d=pd.DataFrame(rows);d['timestamp']=pd.to_datetime(pd.to_numeric(d.fundingTime),unit='ms',utc=True);d['rate']=pd.to_numeric(d.fundingRate);d=d[['timestamp','rate']].drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True);return d
def run_symbol(s,signal,thr,hold):
 px=prices(s);fr=funding(s);fr['avg3']=fr.rate.rolling(3,min_periods=3).mean();out=[];next_free=pd.Timestamp.min.tz_localize('UTC')
 for i,r in fr.iterrows():
  t=r.timestamp
  if t<next_free:continue
  observed=r.rate if signal=='LAST' else r.avg3
  if not np.isfinite(observed) or observed<thr:continue
  entry_t=t+pd.Timedelta(hours=1);exit_t=entry_t+pd.Timedelta(hours=hold)
  if entry_t not in px.index or exit_t not in px.index:continue
  # Require no sampled/missing history issue; all months here are continuous.
  sp0=float(px.at[entry_t,'spot']);sp1=float(px.at[exit_t,'spot']);fp0=float(px.at[entry_t,'perp']);fp1=float(px.at[exit_t,'perp'])
  # Long spot + short perp. Funding after entry and through exit is collected if positive, paid if negative.
  future=fr[(fr.timestamp>entry_t)&(fr.timestamp<=exit_t)]
  fund=float(future.rate.sum())
  basis=(sp1/sp0-1) - (fp1/fp0-1)
  net=basis+fund-COST
  out.append((entry_t.year,s,float(net),float(basis),fund,observed));next_free=exit_t
 return out
def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[2] for r in z],float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan,np.nan,np.nan,0
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),float(np.mean([r[3] for r in z])),float(np.mean([r[4] for r in z])),len(set(r[1] for r in z if r[2]>0))
def main():
 rows=[]
 for signal,thr,hold in itertools.product(['LAST','AVG3'],[0,.00005,.0001,.0002],[24,72,168]):
  tr=[]
  for s in SYMBOLS:tr.extend(run_symbol(s,signal,thr,hold))
  a=met(tr,2025);b=met(tr,2026)
  rows.append(dict(signal=signal,threshold=thr,hold_h=hold,n_2025=a[0],win_2025=a[1],ev_2025=a[2],pf_2025=a[3],basis_2025=a[4],funding_2025=a[5],pos_symbols_2025=a[6],n_2026=b[0],win_2026=b[1],ev_2026=b[2],pf_2026=b[3],basis_2026=b[4],funding_2026=b[5],pos_symbols_2026=b[6]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r7_all.csv',index=False);tr=r[(r.n_2025>=30)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r7_train_positive.csv',index=False);rob=tr[(tr.n_2026>=20)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r7_robust.csv',index=False)
 print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
