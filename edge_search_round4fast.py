#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07'];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r4f_cache');OUT=Path('r4f_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 d=CACHE/s;d.mkdir(parents=True,exist_ok=True);p=d/f'{s}-{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 r=requests.get(BASE.format(s=s,m=m),timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 with zipfile.ZipFile(dl(s,m)) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open','close']].dropna()
def build(s):
 d=pd.concat([read(s,m) for m in MONTHS],ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s;d['year']=d.timestamp.dt.year.astype(np.int16)
 for lb in [240,720,1440]:
  r=d.close/d.close.shift(lb)-1;valid=(d.timestamp-d.timestamp.shift(lb)).dt.total_seconds().eq(lb*60);d[f'r{lb}']=r.where(valid)
 for h in [240,480,720]:
  xclose=d.close.shift(-(h));eopen=d.open.shift(-1);valid=(d.timestamp.shift(-h)-d.timestamp).dt.total_seconds().eq(h*60);d[f'f{h}']=(xclose/eopen-1).where(valid)
 d=d[(d.timestamp.dt.minute==0)&(d.timestamp.dt.hour%4==0)].copy();print(s,len(d),flush=True);return d
def met(a):
 if len(a)==0:return (0,np.nan,np.nan,np.nan)
 a=np.asarray(a,float);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf)
def main():
 ds=pd.concat([build(s) for s in SYMBOLS],ignore_index=True);rows=[]
 cfg=[]
 for lb,thr,h in itertools.product([240,720,1440],[.005,.01,.02],[240,480,720]):cfg.append(('SINGLE',lb,thr,h))
 for thr,h in itertools.product([.005,.01,.02],[240,480,720]):cfg.append(('ALIGN',720,thr,h))
 for model,lb,thr,h in cfg:
  x=ds.copy();base=x[f'r{lb}'].abs()>=thr
  if model=='SINGLE':side=np.sign(x[f'r{lb}'])
  else:
   base=base&(np.sign(x.r240)==np.sign(x.r720));side=np.sign(x.r720)
  pnl=(side*x[f'f{h}']-FEE).where(base);x['pnl']=pnl
  vals=[]
  for y in [2025,2026]:
   z=x.loc[x.year==y,'pnl'].dropna();n,w,e,p=met(z);pos=0
   for s,g in x[x.year==y].groupby('symbol'):
    q=g.pnl.dropna();pos+=int(len(q)>0 and q.mean()>0)
   vals.extend([n,w,e,p,pos])
  rows.append(dict(model=model,lookback=lb,thr=thr,hold=h,n_2025=vals[0],win_2025=vals[1],ev_2025=vals[2],pf_2025=vals[3],pos_symbols_2025=vals[4],n_2026=vals[5],win_2026=vals[6],ev_2026=vals[7],pf_2026=vals[8],pos_symbols_2026=vals[9]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r4f_all.csv',index=False);tr=r[(r.n_2025>=100)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r4f_train_positive.csv',index=False);rob=tr[(tr.n_2026>=75)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r4f_robust.csv',index=False);print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
