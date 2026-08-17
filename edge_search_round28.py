#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,concurrent.futures,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT','DOGEUSDT','ADAUSDT']
MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')]
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r28_cache');OUT=Path('r28_output');OUT.mkdir(exist_ok=True);FEE=.001

def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 for _ in range(3):
  try:
   r=requests.get(BASE.format(s=s,m=m),timeout=120)
   if r.status_code==404:return None
   r.raise_for_status();p.write_bytes(r.content);return p
  except Exception:pass
 return None

def read_month(s,m):
 p=dl(s,m)
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:
   n=[x for x in z.namelist() if x.endswith('.csv')][0]
   d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
  ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
  for c in ['open_time','open','quote_volume','taker_quote']:d[c]=pd.to_numeric(d[c],errors='coerce')
  unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('min')
  d['imb']=2*d.taker_quote/d.quote_volume-1
  return d[['ts','open','quote_volume','imb']].replace([np.inf,-np.inf],np.nan).dropna().drop_duplicates('ts')
 except Exception:return None

def load(s):
 fs=[]
 for m in MONTHS:
  d=read_month(s,m)
  if d is not None:fs.append(d)
 if not fs:return None
 d=pd.concat(fs,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True);d['symbol']=s
 print(s,len(d),d.ts.min(),d.ts.max(),flush=True);return s,d

def run_symbol(d,phase,thr,hold):
 ts=d.ts.to_numpy();op=d.open.to_numpy();imb=d.imb.to_numpy();mins=d.ts.dt.minute.to_numpy();yr=d.ts.dt.year.to_numpy();out=[];i=0;step=pd.Timedelta(minutes=1)
 while i<len(d)-hold-2:
  if mins[i]%15!=phase:i+=1;continue
  if not np.isfinite(imb[i]) or abs(imb[i])<thr:i+=1;continue
  side=1 if imb[i]>0 else -1;e=i+1;x=e+hold
  if pd.Timestamp(ts[e])-pd.Timestamp(ts[i])!=step or x>=len(d) or pd.Timestamp(ts[x])-pd.Timestamp(ts[e])!=pd.Timedelta(minutes=hold):i+=1;continue
  net=float(side*(op[x]/op[e]-1)-FEE);out.append((int(yr[e]),net,side,float(imb[i]),pd.Timestamp(ts[e]),str(d.symbol.iloc[e])));i=x+1
 return out

def met(z):
 if not z:return (0,np.nan,np.nan,np.nan,0,0)
 a=np.array([q[1] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf
 longs=[q for q in z if q[2]>0];shorts=[q for q in z if q[2]<0]
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),len(longs),len(shorts)

def main():
 data={}
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
  for res in ex.map(load,SYMBOLS):
   if res:data[res[0]]=res[1]
 rows=[]
 for phase,thr,hold in itertools.product([0,5],[.20,.30,.40],[240,480,720]):
  tr=[]
  for d in data.values():tr+=run_symbol(d,phase,thr,hold)
  y25=met([q for q in tr if q[0]==2025]);y26=met([q for q in tr if q[0]==2026])
  rows.append(dict(phase=phase,imb_thr=thr,hold_min=hold,n_2025=y25[0],win_2025=y25[1],ev_2025=y25[2],pf_2025=y25[3],long_2025=y25[4],short_2025=y25[5],n_2026=y26[0],win_2026=y26[1],ev_2026=y26[2],pf_2026=y26[3],long_2026=y26[4],short_2026=y26[5]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r28_all.csv',index=False)
 q=r[r.phase==0].copy();p=r[r.phase==5][['imb_thr','hold_min','ev_2025','ev_2026']].rename(columns={'ev_2025':'placebo_ev_2025','ev_2026':'placebo_ev_2026'});q=q.merge(p,on=['imb_thr','hold_min']);q['delta25']=q.ev_2025-q.placebo_ev_2025;q['delta26']=q.ev_2026-q.placebo_ev_2026
 robust=q[(q.n_2025>=50)&(q.n_2026>=30)&(q.ev_2025>0)&(q.ev_2026>0)&(q.delta25>0)&(q.delta26>0)&(q.pf_2025>1)&(q.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);robust.to_csv(OUT/'r28_robust.csv',index=False)
 print('TRUE QUARTER-HOUR VS +5M PLACEBO');print(q.to_string(index=False));print('\nROBUST');print(robust.to_string(index=False) if len(robust) else 'NONE')
if __name__=='__main__':main()
