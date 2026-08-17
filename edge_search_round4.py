#!/usr/bin/env python3
from __future__ import annotations
import io,itertools,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07']
FEE=0.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r4_cache');OUT=Path('r4_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 d=CACHE/s;d.mkdir(parents=True,exist_ok=True);p=d/f'{s}-{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 r=requests.get(BASE.format(s=s,m=m),timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read(p,s):
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in COLS:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);d['symbol']=s
 return d[['timestamp','symbol','open','close']].dropna()
def load(s):
 fs=[]
 for m in MONTHS:
  p=dl(s,m)
  if p is not None:fs.append(read(p,s))
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['year']=d.timestamp.dt.year.astype(np.int16)
 print(s,len(d),flush=True);return d
def ret_valid(d,i,lb):
 j=i-lb
 if j<0:return np.nan
 if (d.timestamp.iloc[i]-d.timestamp.iloc[j]).total_seconds()!=lb*60:return np.nan
 return d.close.iloc[i]/d.close.iloc[j]-1
def simulate(d,model,lb,thr,hold):
 out=[];n=len(d)
 for i in range(n):
  t=d.timestamp.iloc[i]
  if t.minute!=0 or t.hour%4!=0:continue
  r=ret_valid(d,i,lb)
  if not np.isfinite(r) or abs(r)<thr:continue
  if model=='ALIGN':
   r4=ret_valid(d,i,240);r12=ret_valid(d,i,720)
   if not np.isfinite(r4) or not np.isfinite(r12) or np.sign(r4)!=np.sign(r12):continue
   side=1 if r12>0 else -1
  else: side=1 if r>0 else -1
  e=i+1;x=e+hold-1
  if x>=n:continue
  if (d.timestamp.iloc[x]-d.timestamp.iloc[e]).total_seconds()!=(hold-1)*60:continue
  net=side*(d.close.iloc[x]/d.open.iloc[e]-1)-FEE
  out.append((int(d.year.iloc[e]),str(d.symbol.iloc[e]),float(net)))
 return out
def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[2] for r in z],float)
 if len(a)==0:return (0,np.nan,np.nan,np.nan,0)
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;se={}
 for _,s,p in z:se.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in se.values())
def main():
 data={s:load(s) for s in SYMBOLS};rows=[]
 configs=[]
 for lb,thr,hold in itertools.product([240,720,1440],[0.005,0.01,0.02],[240,480,720]):configs.append(('SINGLE',lb,thr,hold))
 for thr,hold in itertools.product([0.005,0.01,0.02],[240,480,720]):configs.append(('ALIGN',720,thr,hold))
 for model,lb,thr,hold in configs:
  tr=[]
  for d in data.values():tr.extend(simulate(d,model,lb,thr,hold))
  n25,w25,e25,p25,s25=met(tr,2025);n26,w26,e26,p26,s26=met(tr,2026)
  rows.append(dict(model=model,lookback_min=lb,threshold=thr,hold_min=hold,n_2025=n25,win_2025=w25,ev_2025=e25,pf_2025=p25,pos_symbols_2025=s25,n_2026=n26,win_2026=w26,ev_2026=e26,pf_2026=p26,pos_symbols_2026=s26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r4_all.csv',index=False)
 train=r[(r.n_2025>=100)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);train.to_csv(OUT/'r4_train_positive.csv',index=False)
 robust=train[(train.n_2026>=75)&(train.ev_2026>0)&(train.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);robust.to_csv(OUT/'r4_robust.csv',index=False)
 print('BEST 2025');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(robust.head(20).to_string(index=False) if len(robust) else 'NONE')
if __name__=='__main__':main()
