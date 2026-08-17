#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,calendar
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT','LINKUSDT','DOTUSDT']
MONTHS=[str(p) for p in pd.period_range('2021-01','2026-07',freq='M')]
KPERP='https://data.binance.vision/data/futures/um/monthly/klines/{s}/8h/{s}-8h-{m}.zip'
KSPOT='https://data.binance.vision/data/spot/monthly/klines/{s}/8h/{s}-8h-{m}.zip'
FUND='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r26_cache');OUT=Path('r26_output');OUT.mkdir(exist_ok=True)

def dl(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 for _ in range(3):
  try:
   r=requests.get(url,timeout=90)
   if r.status_code==404:return None
   r.raise_for_status();p.write_bytes(r.content);return p
  except Exception: pass
 return None

def read_k(s,m,kind):
 p=dl((KPERP if kind=='perp' else KSPOT).format(s=s,m=m),CACHE/kind/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:
   n=[x for x in z.namelist() if x.endswith('.csv')][0]
   d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
  ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d['open_time']=pd.to_numeric(d.open_time,errors='coerce');d['open']=pd.to_numeric(d.open,errors='coerce')
  unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('8h')
  return d[['ts','open']].dropna().drop_duplicates('ts')
 except Exception:return None

def read_f(s,m):
 p=dl(FUND.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:
   n=[x for x in z.namelist() if x.endswith('.csv')][0]
   d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
  ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce')
  # Keep exact funding timestamps; do NOT bucket to 8h.
  d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True)
  return d[['ts','rate']].dropna().sort_values('ts')
 except Exception:return None

def load(s):
 ps=[];ss=[];fs=[]
 for m in MONTHS:
  a=read_k(s,m,'perp');b=read_k(s,m,'spot');c=read_f(s,m)
  if a is not None:ps.append(a)
  if b is not None:ss.append(b)
  if c is not None:fs.append(c)
 if not ps or not ss or not fs:return None
 p=pd.concat(ps).drop_duplicates('ts').sort_values('ts').set_index('ts')['open'];q=pd.concat(ss).drop_duplicates('ts').sort_values('ts').set_index('ts')['open'];f=pd.concat(fs).sort_values('ts').drop_duplicates(['ts','rate'])
 return p,q,f

def anchors(year):
 # Non-overlapping calendar-quarter-like 90d starts. Last 2026 entry only if full 90d exit exists in data.
 return [pd.Timestamp(year=year,month=m,day=1,tz='UTC') for m in (1,4,7,10)]

def nearest_open(series,t):
 # require exact 8h open at anchor/exit; this avoids hidden execution timing choices
 return float(series.loc[t]) if t in series.index else None

def make_trades(s,pack):
 p,q,f=pack;tr=[];max_ts=min(p.index.max(),q.index.max())
 for y in range(2021,2027):
  for t0 in anchors(y):
   t1=t0+pd.Timedelta(days=90)
   if t1>max_ts:continue
   po0=nearest_open(p,t0);so0=nearest_open(q,t0);po1=nearest_open(p,t1);so1=nearest_open(q,t1)
   if None in (po0,so0,po1,so1):continue
   ff=f[(f.ts>t0)&(f.ts<t1)].rate.astype(float)
   basis=(so1/so0-1)-(po1/po0-1)
   fund=float(ff.sum())
   gross=float(basis+fund)
   tr.append(dict(symbol=s,entry=t0,exit=t1,year=y,basis=basis,funding=fund,gross=gross,n_funding=len(ff)))
 return tr

def metrics(d,cost):
 if len(d)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,pos_symbols=0,med_funding_events=np.nan)
 x=d.gross.to_numpy()-cost
 pos=x[x>0].sum();neg=-x[x<0].sum();pf=pos/neg if neg>0 else np.inf
 by=d.assign(net=x).groupby('symbol').net.mean()
 return dict(n=len(x),win=float((x>0).mean()),ev=float(x.mean()),pf=float(pf),pos_symbols=int((by>0).sum()),med_funding_events=float(d.n_funding.median()))

def main():
 trades=[]
 for s in SYMBOLS:
  pack=load(s)
  if pack is None:continue
  z=make_trades(s,pack);trades+=z;print(s,'trades',len(z),flush=True)
 d=pd.DataFrame(trades);d.to_csv(OUT/'r26_trades_gross.csv',index=False)
 rows=[]
 for cost in (.002,.003,.004):
  for y in range(2021,2027):
   m=metrics(d[d.year==y],cost);rows.append(dict(cost=cost,year=y,**m))
  m=metrics(d,cost);rows.append(dict(cost=cost,year='ALL',**m))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r26_year_cost.csv',index=False)
 print('\nYEAR / COST STRESS');print(r.to_string(index=False))
 print('\nSYMBOL @20bps')
 ss=[]
 for s,g in d.groupby('symbol'):
  m=metrics(g,.002);ss.append(dict(symbol=s,**m,years_pos=sum((g.assign(net=g.gross-.002).groupby('year').net.mean()>0))))
 print(pd.DataFrame(ss).sort_values('ev',ascending=False).to_string(index=False))
 print('\nQUARTER-ANCHOR @20bps')
 dd=d.copy();dd['quarter']=dd.entry.dt.to_period('Q').astype(str);qq=[]
 for k,g in dd.groupby('quarter'):
  m=metrics(g,.002);qq.append(dict(quarter=k,**m))
 print(pd.DataFrame(qq).to_string(index=False))
if __name__=='__main__':main()
