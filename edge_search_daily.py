#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2020-01','2026-07',freq='M')]
FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('daily_cache');OUT=Path('daily_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=60)
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
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s;d['year']=d.timestamp.dt.year.astype(np.int16);print(s,len(d),d.timestamp.min(),flush=True);return d
def sim_ma(d,L,slope_days,long_short):
 sma=d.close.shift(1).rolling(L,min_periods=L).mean();slope=sma/sma.shift(slope_days)-1 if slope_days>0 else pd.Series(0.0,index=d.index);condL=(d.close>sma)&(slope>=0);condS=(d.close<sma)&(slope<=0) if long_short else pd.Series(False,index=d.index)
 op=d.open.to_numpy();cl=d.close.to_numpy();ts=d.timestamp.to_numpy();yr=d.year.to_numpy();out=[];side=0;entry=0.;entry_y=None;entry_i=None
 for i in range(max(L+slope_days,1),len(d)-1):
  desired=1 if bool(condL.iloc[i]) else (-1 if bool(condS.iloc[i]) else 0)
  if desired==side:continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).days!=1:continue
  px=op[e]
  if side!=0:
   gross=side*(px/entry-1);out.append((int(entry_y),str(d.symbol.iloc[e]),gross-FEE,e-entry_i,side))
  side=desired
  if side!=0:entry=px;entry_y=yr[e];entry_i=e
  else:entry=0.;entry_y=None;entry_i=None
 if side!=0 and entry_i is not None:
  px=cl[-1];gross=side*(px/entry-1);out.append((int(entry_y),str(d.symbol.iloc[-1]),gross-FEE,len(d)-1-entry_i,side))
 return out
def sim_donch(d,en,xn,long_short):
 ph=d.high.shift(1).rolling(en,min_periods=en).max().to_numpy();pl=d.low.shift(1).rolling(en,min_periods=en).min().to_numpy();tl=d.low.shift(1).rolling(xn,min_periods=xn).min().to_numpy();th=d.high.shift(1).rolling(xn,min_periods=xn).max().to_numpy();op=d.open.to_numpy();cl=d.close.to_numpy();ts=d.timestamp.to_numpy();yr=d.year.to_numpy();out=[];side=0;entry=0.;entry_y=None;entry_i=None
 for i in range(max(en,xn),len(d)-1):
  desired=side
  if side==0:
   if cl[i]>ph[i]:desired=1
   elif long_short and cl[i]<pl[i]:desired=-1
  elif side==1 and cl[i]<tl[i]:desired=0
  elif side==-1 and cl[i]>th[i]:desired=0
  if desired==side:continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).days!=1:continue
  px=op[e]
  if side!=0:
   gross=side*(px/entry-1);out.append((int(entry_y),str(d.symbol.iloc[e]),gross-FEE,e-entry_i,side))
  side=desired
  if side!=0:entry=px;entry_y=yr[e];entry_i=e
  else:entry=0.;entry_y=None;entry_i=None
 if side!=0 and entry_i is not None:
  gross=side*(cl[-1]/entry-1);out.append((int(entry_y),str(d.symbol.iloc[-1]),gross-FEE,len(d)-1-entry_i,side))
 return out
def met(z):
 a=np.array([q[2] for q in z],float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan,0,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for _,s,p,*_ in z:ss.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.median([q[3] for q in z]))
def eval_trades(tr):
 train=[q for q in tr if q[0] in (2021,2022,2023)];v24=[q for q in tr if q[0]==2024];v25=[q for q in tr if q[0]==2025];v26=[q for q in tr if q[0]==2026];return met(train),met(v24),met(v25),met(v26)
def main():
 data={s:load(s) for s in SYMBOLS};data={s:d for s,d in data.items() if d is not None};rows=[]
 configs=[]
 for L,sl,ls in itertools.product([20,50,100,200],[0,10,20],[False,True]):configs.append(('MA',L,sl,np.nan,ls))
 for en,xn,ls in itertools.product([20,55,100],[10,20,50],[False,True]):
  if xn<en:configs.append(('DONCH',en,np.nan,xn,ls))
 for model,p1,p2,p3,ls in configs:
  tr=[]
  for d in data.values():
   tr += sim_ma(d,int(p1),int(p2),ls) if model=='MA' else sim_donch(d,int(p1),int(p3),ls)
  a,b,c,e=eval_trades(tr)
  rows.append(dict(model=model,p1=p1,p2=p2,p3=p3,long_short=ls,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],medhold_train=a[5],n_2024=b[0],win_2024=b[1],ev_2024=b[2],pf_2024=b[3],pos_symbols_2024=b[4],n_2025=c[0],win_2025=c[1],ev_2025=c[2],pf_2025=c[3],pos_symbols_2025=c[4],n_2026=e[0],win_2026=e[1],ev_2026=e[2],pf_2026=e[3],pos_symbols_2026=e[4]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'daily_all.csv',index=False)
 sel=r[(r.n_train>=80)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);sel.to_csv(OUT/'daily_train_positive.csv',index=False)
 rob=sel[(sel.n_2024>=30)&(sel.ev_2024>0)&(sel.pf_2024>1)&(sel.n_2025>=30)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=15)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025','ev_2024'],ascending=False);rob.to_csv(OUT/'daily_robust.csv',index=False)
 print('BEST TRAIN');print(r.sort_values('ev_train',ascending=False).head(25).to_string(index=False));print('\nROBUST 2024/25/26');print(rob.head(30).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
