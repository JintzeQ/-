#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2024-01','2026-07',freq='M')]
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('val_cache');OUT=Path('val_output');OUT.mkdir(exist_ok=True)
ENTRY_N=48;EXIT_N=12;BUFFER=.001;MAXHOLD=168

def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 r=requests.get(BASE.format(s=s,m=m),timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p

def read(s,m):
 p=dl(s,m)
 if p is None:return None
 with zipfile.ZipFile(p) as z:
  n=[x for x in z.namelist() if x.endswith('.csv')][0]
  d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
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
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s
 print(s,len(d),d.timestamp.min(),d.timestamp.max(),flush=True);return d

def simulate(d):
 op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();ts=d.timestamp.to_numpy();ph=d.high.shift(1).rolling(ENTRY_N,min_periods=ENTRY_N).max().to_numpy();pl=d.low.shift(1).rolling(ENTRY_N,min_periods=ENTRY_N).min().to_numpy();tl=d.low.shift(1).rolling(EXIT_N,min_periods=EXIT_N).min().to_numpy();th=d.high.shift(1).rolling(EXIT_N,min_periods=EXIT_N).max().to_numpy();out=[];i=max(ENTRY_N,EXIT_N)
 while i<len(d)-2:
  side=1 if cl[i]>ph[i]*(1+BUFFER) else (-1 if cl[i]<pl[i]*(1-BUFFER) else 0)
  if side==0:i+=1;continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).total_seconds()!=3600:i+=1;continue
  entry=op[e];x=e;reason='MAX'
  for j in range(e+1,min(e+MAXHOLD,len(d)-1)):
   if (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=3600:x=j-1;reason='GAP';break
   if side==1 and cl[j]<tl[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   if side==-1 and cl[j]>th[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   x=j
  if x>=len(d):x=len(d)-1
  exitp=op[x] if reason=='TRAIL' and x>e else cl[x]
  gross=side*(exitp/entry-1)
  out.append(dict(symbol=str(d.symbol.iloc[e]),entry_time=pd.Timestamp(ts[e]),exit_time=pd.Timestamp(ts[x]),side=side,gross=gross,hold_h=x-e,reason=reason));i=max(x+1,e+1)
 return pd.DataFrame(out)

def stats(z,fee):
 if len(z)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,maxls=np.nan,medhold=np.nan)
 a=z.gross.to_numpy()-fee;pos=a[a>0].sum();neg=-a[a<0].sum();pf=pos/neg if neg else np.inf;best=cur=0
 for v in a:
  if v<=0:cur+=1;best=max(best,cur)
  else:cur=0
 return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),maxls=best,medhold=float(z.hold_h.median()))

def main():
 tr=[]
 for s in SYMBOLS:
  d=load(s)
  if d is not None:
   x=simulate(d)
   if len(x):tr.append(x)
 t=pd.concat(tr,ignore_index=True).sort_values('entry_time').reset_index(drop=True);t['year']=t.entry_time.dt.year;t['month']=t.entry_time.dt.to_period('M').astype(str);t.to_csv(OUT/'trades.csv',index=False)
 rows=[]
 for fee in [.001,.0015,.002,.003]:
  for y in [2024,2025,2026]:
   q=t[t.year==y];s=stats(q,fee);rows.append(dict(scope='year',key=str(y),fee=fee,**s))
  for sym,q in t.groupby('symbol'):
   s=stats(q,fee);rows.append(dict(scope='symbol_all',key=sym,fee=fee,**s))
  for m,q in t.groupby('month'):
   s=stats(q,fee);rows.append(dict(scope='month',key=m,fee=fee,**s))
 r=pd.DataFrame(rows);r.to_csv(OUT/'validation_stats.csv',index=False)
 print('\nYEAR COST STRESS');print(r[r.scope.eq('year')].to_string(index=False))
 print('\nSYMBOL @10bps');print(r[(r.scope=='symbol_all')&(r.fee==.001)].sort_values('ev',ascending=False).to_string(index=False))
 m=r[(r.scope=='month')&(r.fee==.001)];print('\nMONTHS +:',int((m.ev>0).sum()),'/',len(m),'median bps',float(m.ev.median()*10000));print(m[['key','n','ev','pf']].to_string(index=False))
if __name__=='__main__':main()
