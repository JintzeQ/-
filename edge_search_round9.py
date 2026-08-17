#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r9_cache');OUT=Path('r9_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 with zipfile.ZipFile(dl(s,m)) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','high','low','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open','high','low','close']].dropna()
def load(s):
 d=pd.concat([read(s,m) for m in MONTHS],ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s;d['year']=d.timestamp.dt.year.astype(np.int16);return d
def simulate(d,entry_n,exit_n,buffer,maxhold):
 op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();yr=d.year.to_numpy();ts=d.timestamp.to_numpy();ph=d.high.shift(1).rolling(entry_n,min_periods=entry_n).max().to_numpy();pl=d.low.shift(1).rolling(entry_n,min_periods=entry_n).min().to_numpy();trailL=d.low.shift(1).rolling(exit_n,min_periods=exit_n).min().to_numpy();trailH=d.high.shift(1).rolling(exit_n,min_periods=exit_n).max().to_numpy();out=[];i=max(entry_n,exit_n)
 while i<len(d)-2:
  side=0
  if cl[i]>ph[i]*(1+buffer):side=1
  elif cl[i]<pl[i]*(1-buffer):side=-1
  if side==0:i+=1;continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).total_seconds()!=3600:i+=1;continue
  entry=op[e];x=e;reason='MAX'
  for j in range(e+1,min(e+maxhold,len(d)-1)):
   if (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=3600:x=j-1;reason='GAP';break
   if side==1 and cl[j]<trailL[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   if side==-1 and cl[j]>trailH[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   x=j
  if x>=len(d):x=len(d)-1
  exitp=op[x] if reason=='TRAIL' and x>e else cl[x]
  net=side*(exitp/entry-1)-FEE;out.append((int(yr[e]),str(d.symbol.iloc[e]),float(net),side,reason,x-e));i=max(x+1,e+1)
 return out
def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[2] for r in z],float)
 if not len(a):return 0,np.nan,np.nan,np.nan,0,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;se={}
 for _,s,p,*_ in z:se.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in se.values()),float(np.median([r[5] for r in z]))
def main():
 data={s:load(s) for s in SYMBOLS};rows=[]
 for en,xn,buf,mh in itertools.product([24,48,72],[12,24,48],[0,.001],[168,336]):
  tr=[]
  for d in data.values():tr.extend(simulate(d,en,xn,buf,mh))
  a=met(tr,2025);b=met(tr,2026);rows.append(dict(entry_h=en,exit_h=xn,buffer=buf,maxhold_h=mh,n_2025=a[0],win_2025=a[1],ev_2025=a[2],pf_2025=a[3],pos_symbols_2025=a[4],median_hold_2025=a[5],n_2026=b[0],win_2026=b[1],ev_2026=b[2],pf_2026=b[3],pos_symbols_2026=b[4],median_hold_2026=b[5]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r9_all.csv',index=False);tr=r[(r.n_2025>=100)&(r.ev_2025>0)].sort_values(['ev_2025','pf_2025'],ascending=False);tr.to_csv(OUT/'r9_train_positive.csv',index=False);rob=tr[(tr.n_2026>=75)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','pf_2026'],ascending=False);rob.to_csv(OUT/'r9_robust.csv',index=False);print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
