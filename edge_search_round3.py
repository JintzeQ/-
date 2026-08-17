#!/usr/bin/env python3
from __future__ import annotations
import io,time,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07']
FEE=0.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r3_cache');OUT=Path('r3_output');OUT.mkdir(exist_ok=True)

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
 return d[['timestamp','symbol','open','high','low','close','quote_volume','taker_quote']].dropna()

def load(s):
 fs=[]
 for m in MONTHS:
  p=dl(s,m)
  if p is not None:fs.append(read(p,s))
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
 qmed=d.quote_volume.shift(1).rolling(60,min_periods=60).median();d['vshock']=d.quote_volume/qmed.replace(0,np.nan)
 d['bs']=d.taker_quote/d.quote_volume.replace(0,np.nan);d['body']=d.close/d.open-1;d['year']=d.timestamp.dt.year.astype(np.int16)
 d['quarter']=(d.timestamp.dt.minute%15==0)
 print(s,len(d),flush=True);return d

def trades_for(d,flow,vshock,body_confirm,hold):
 bs=d.bs.to_numpy();vs=d.vshock.to_numpy();body=d.body.to_numpy();q=d.quarter.to_numpy();op=d.open.to_numpy();cl=d.close.to_numpy();yr=d.year.to_numpy();sy=d.symbol.to_numpy();out=[]
 dr=np.zeros(len(d),np.int8);base=q&(vs>=vshock)
 if body_confirm:
  dr[base&(bs>=flow)&(body>0)]=1;dr[base&(bs<=1-flow)&(body<0)]=-1
 else:
  dr[base&(bs>=flow)]=1;dr[base&(bs<=1-flow)]=-1
 idx=np.flatnonzero(dr!=0)
 for i in idx:
  e=i+1;x=e+hold-1
  if x>=len(d):continue
  # sampled months have gaps: require exact elapsed time, otherwise skip cross-gap trade
  if (d.timestamp.iloc[x]-d.timestamp.iloc[e]).total_seconds() != (hold-1)*60:continue
  entry=op[e];exitp=cl[x];net=dr[i]*(exitp/entry-1)-FEE
  out.append((int(yr[e]),str(sy[e]),float(net)))
 return out

def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[2] for r in z],float)
 if len(a)==0:return (0,np.nan,np.nan,np.nan,0)
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;se={}
 for _,s,p in z:se.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in se.values())

def main():
 data={s:load(s) for s in SYMBOLS};rows=[]
 for flow,vshock,bc,hold in itertools.product([0.55,0.60,0.65,0.70],[1.0,1.5,2.0],[False,True],[240,480,720]):
  tr=[]
  for d in data.values():tr.extend(trades_for(d,flow,vshock,bc,hold))
  n25,w25,e25,p25,s25=met(tr,2025);n26,w26,e26,p26,s26=met(tr,2026)
  rows.append(dict(flow=flow,vshock=vshock,body_confirm=bc,hold_min=hold,n_2025=n25,win_2025=w25,ev_2025=e25,pf_2025=p25,pos_symbols_2025=s25,n_2026=n26,win_2026=w26,ev_2026=e26,pf_2026=p26,pos_symbols_2026=s26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r3_all.csv',index=False)
 train=r[(r.n_2025>=200)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);train.to_csv(OUT/'r3_train_positive.csv',index=False)
 robust=train[(train.n_2026>=150)&(train.ev_2026>0)&(train.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);robust.to_csv(OUT/'r3_robust.csv',index=False)
 print('BEST 2025');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(robust.head(20).to_string(index=False) if len(robust) else 'NONE')
if __name__=='__main__':main()
