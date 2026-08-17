#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,time,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')];COST=.002
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r8_cache');OUT=Path('r8_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 with zipfile.ZipFile(dl(s,m)) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open']].dropna()
def prices(s):return pd.concat([read(s,m) for m in MONTHS]).drop_duplicates('timestamp').set_index('timestamp').sort_index().open
def funding(s):
 start=int(pd.Timestamp('2025-01-01',tz='UTC').timestamp()*1000);end=int(pd.Timestamp('2026-08-01',tz='UTC').timestamp()*1000);rows=[];cur=start
 while cur<end:
  r=requests.get('https://fapi.binance.com/fapi/v1/fundingRate',params={'symbol':s,'startTime':cur,'endTime':end,'limit':1000},timeout=60);r.raise_for_status();z=r.json()
  if not z:break
  rows+=z;cur=int(z[-1]['fundingTime'])+1
  if len(z)<1000:break
 d=pd.DataFrame(rows);d['timestamp']=pd.to_datetime(pd.to_numeric(d.fundingTime),unit='ms',utc=True);d['rate']=pd.to_numeric(d.fundingRate);d=d[['timestamp','rate']].drop_duplicates('timestamp').sort_values('timestamp');d['avg3']=d.rate.rolling(3,min_periods=3).mean();return d.reset_index(drop=True)
def main():
 px={s:prices(s) for s in SYMBOLS};fr={s:funding(s) for s in SYMBOLS}
 # Create a rate table on exact funding timestamps.
 rt=None
 for s,d in fr.items():
  x=d.set_index('timestamp')[['rate','avg3']].rename(columns={'rate':f'{s}_rate','avg3':f'{s}_avg3'})
  rt=x if rt is None else rt.join(x,how='outer')
 rt=rt.sort_index();rows=[]
 for signal,minspread,hold in itertools.product(['LAST','AVG3'],[0,.00005,.0001,.0002,.0005],[24,72,168]):
  trades=[];next_free=pd.Timestamp('1900-01-01',tz='UTC')
  col='rate' if signal=='LAST' else 'avg3'
  for t,r in rt.iterrows():
   if t<next_free:continue
   vals={s:r.get(f'{s}_{col}',np.nan) for s in SYMBOLS};vals={s:float(v) for s,v in vals.items() if np.isfinite(v)}
   if len(vals)<6:continue
   hi=max(vals,key=vals.get);lo=min(vals,key=vals.get);spread=vals[hi]-vals[lo]
   if spread<minspread:continue
   entry=t+pd.Timedelta(hours=1);exit=entry+pd.Timedelta(hours=hold)
   if any(entry not in px[s].index or exit not in px[s].index for s in (hi,lo)):continue
   # long low-funding perp, short high-funding perp
   price=(float(px[lo].at[exit]/px[lo].at[entry]-1) - float(px[hi].at[exit]/px[hi].at[entry]-1))
   f_hi=fr[hi][(fr[hi].timestamp>entry)&(fr[hi].timestamp<=exit)].rate.sum();f_lo=fr[lo][(fr[lo].timestamp>entry)&(fr[lo].timestamp<=exit)].rate.sum();fund=float(f_hi-f_lo)
   net=price+fund-COST;trades.append((entry.year,net,price,fund,hi,lo,spread));next_free=exit
  for y in [2025,2026]:
   z=[q for q in trades if q[0]==y];a=np.array([q[1] for q in z],float)
   if len(a):
    pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;win=float((a>0).mean());ev=float(a.mean());pr=float(np.mean([q[2] for q in z]));fu=float(np.mean([q[3] for q in z]))
   else:pf=win=ev=pr=fu=np.nan
   if y==2025:a25=(len(a),win,ev,pf,pr,fu)
   else:a26=(len(a),win,ev,pf,pr,fu)
  rows.append(dict(signal=signal,minspread=minspread,hold_h=hold,n_2025=a25[0],win_2025=a25[1],ev_2025=a25[2],pf_2025=a25[3],price_2025=a25[4],fund_2025=a25[5],n_2026=a26[0],win_2026=a26[1],ev_2026=a26[2],pf_2026=a26[3],price_2026=a26[4],fund_2026=a26[5]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r8_all.csv',index=False);tr=r[(r.n_2025>=30)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r8_train_positive.csv',index=False);rob=tr[(tr.n_2026>=20)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r8_robust.csv',index=False);print('BEST');print(r.sort_values('ev_2025',ascending=False).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
