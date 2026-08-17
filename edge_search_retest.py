#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')]
FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/4h/{s}-4h-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('retest_cache');OUT=Path('retest_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 p=dl(s,m)
 if p is None:return None
 with zipfile.ZipFile(p) as z:
  n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','high','low','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True)
 return d[['timestamp','open','high','low','close']].dropna()
def load(s):
 fs=[]
 for m in MONTHS:
  x=read(s,m)
  if x is not None:fs.append(x)
 if not fs:return None
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s;d['year']=d.timestamp.dt.year.astype(np.int16);print(s,len(d),flush=True);return d
def simulate(d,en,buf,wait,tol,xn,maxhold=120):
 op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();yr=d.year.to_numpy();ts=d.timestamp.to_numpy();ph=d.high.shift(1).rolling(en,min_periods=en).max().to_numpy();pl=d.low.shift(1).rolling(en,min_periods=en).min().to_numpy();trailL=d.low.shift(1).rolling(xn,min_periods=xn).min().to_numpy();trailH=d.high.shift(1).rolling(xn,min_periods=xn).max().to_numpy();out=[];i=max(en,xn)
 while i<len(d)-3:
  side=1 if cl[i]>ph[i]*(1+buf) else (-1 if cl[i]<pl[i]*(1-buf) else 0)
  if side==0:i+=1;continue
  level=ph[i] if side==1 else pl[i];retest=None;invalid=False
  for j in range(i+1,min(i+1+wait,len(d)-2)):
   if (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=14400:invalid=True;break
   if side==1:
    # pull back to prior resistance and reclaim it with a bullish 4h close
    if lo[j] <= level*(1+tol) and cl[j]>level and cl[j]>op[j]:retest=j;break
   else:
    if hi[j] >= level*(1-tol) and cl[j]<level and cl[j]<op[j]:retest=j;break
  if invalid or retest is None:i+=1;continue
  e=retest+1
  if e>=len(d) or (pd.Timestamp(ts[e])-pd.Timestamp(ts[retest])).total_seconds()!=14400:i=retest+1;continue
  entry=op[e];x=e;reason='MAX'
  for k in range(e+1,min(e+maxhold,len(d)-1)):
   if (pd.Timestamp(ts[k])-pd.Timestamp(ts[k-1])).total_seconds()!=14400:x=k-1;reason='GAP';break
   if side==1 and cl[k]<trailL[k]:x=k+1 if k+1<len(d) else k;reason='TRAIL';break
   if side==-1 and cl[k]>trailH[k]:x=k+1 if k+1<len(d) else k;reason='TRAIL';break
   x=k
  xp=op[x] if reason=='TRAIL' and x>e else cl[x];out.append((int(yr[e]),str(d.symbol.iloc[e]),side*(xp/entry-1)-FEE,x-e,side));i=max(x+1,e+1)
 return out
def met(z):
 a=np.array([q[2] for q in z],float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan,0,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for _,s,p,*_ in z:ss.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.median([q[3] for q in z])),float(np.mean([q[4]>0 for q in z]))
def main():
 data={s:load(s) for s in SYMBOLS};data={s:d for s,d in data.items() if d is not None};rows=[]
 for en,buf,wait,tol,xn in itertools.product([10,20,30],[0,.001],[3,6,12],[.002,.005],[5,10,20]):
  tr=[]
  for d in data.values():tr+=simulate(d,en,buf,wait,tol,xn)
  train=[q for q in tr if q[0] in (2023,2024)];a=met(train);b=met([q for q in tr if q[0]==2025]);c=met([q for q in tr if q[0]==2026])
  rows.append(dict(entry_bars=en,buffer=buf,wait_bars=wait,tolerance=tol,exit_bars=xn,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],medhold_train=a[5],long_share_train=a[6],n_2025=b[0],win_2025=b[1],ev_2025=b[2],pf_2025=b[3],pos_symbols_2025=b[4],n_2026=c[0],win_2026=c[1],ev_2026=c[2],pf_2026=c[3],pos_symbols_2026=c[4]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'retest_all.csv',index=False)
 sel=r[(r.n_train>=150)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','win_train'],ascending=False);sel.to_csv(OUT/'retest_train_positive.csv',index=False)
 rob=sel[(sel.n_2025>=70)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=40)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'retest_robust.csv',index=False)
 print('BEST TRAIN');print(r.sort_values('ev_train',ascending=False).head(20).to_string(index=False));print('\nROBUST TWO HOLDOUTS');print(rob.head(30).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
