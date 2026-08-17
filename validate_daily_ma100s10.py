#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2020-01','2026-07',freq='M')]
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('vma_cache');OUT=Path('vma_output');OUT.mkdir(exist_ok=True)
L=100;SLOPE=10

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
  n=[x for x in z.namelist() if x.endswith('.csv')][0]
  d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True)
 return d[['timestamp','open','close']].dropna()

def load(s):
 fs=[]
 for m in MONTHS:
  x=read(s,m)
  if x is not None:fs.append(x)
 if not fs:return None
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s
 return d

def trades(d):
 sma=d.close.shift(1).rolling(L,min_periods=L).mean();slope=sma/sma.shift(SLOPE)-1
 long=(d.close>sma)&(slope>=0);short=(d.close<sma)&(slope<=0)
 op=d.open.to_numpy();cl=d.close.to_numpy();ts=d.timestamp.to_numpy();out=[];side=0;entry=0.;ei=None
 for i in range(L+SLOPE,len(d)-1):
  desired=1 if bool(long.iloc[i]) else (-1 if bool(short.iloc[i]) else 0)
  if desired==side:continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).days!=1:continue
  px=float(op[e])
  if side!=0:
   gross=side*(px/entry-1);out.append(dict(symbol=str(d.symbol.iloc[e]),entry_time=pd.Timestamp(ts[ei]),exit_time=pd.Timestamp(ts[e]),side=side,gross=gross,hold_days=e-ei))
  side=desired
  if side!=0:entry=px;ei=e
  else:entry=0.;ei=None
 if side!=0 and ei is not None:
  gross=side*(float(cl[-1])/entry-1);out.append(dict(symbol=str(d.symbol.iloc[-1]),entry_time=pd.Timestamp(ts[ei]),exit_time=pd.Timestamp(ts[-1]),side=side,gross=gross,hold_days=len(d)-1-ei))
 return pd.DataFrame(out)

def metrics(z,fee):
 if len(z)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,avgwin=np.nan,avgloss=np.nan,maxls=np.nan,medhold=np.nan)
 a=z.gross.to_numpy(float)-fee;pos=a[a>0].sum();neg=-a[a<0].sum();pf=pos/neg if neg else np.inf
 best=cur=0
 for x in a:
  if x<=0:cur+=1;best=max(best,cur)
  else:cur=0
 return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),avgwin=float(a[a>0].mean()) if np.any(a>0) else np.nan,avgloss=float(a[a<0].mean()) if np.any(a<0) else np.nan,maxls=best,medhold=float(z.hold_days.median()))

def main():
 alltr=[]
 for s in SYMBOLS:
  d=load(s)
  if d is None:continue
  t=trades(d)
  if len(t):alltr.append(t)
 t=pd.concat(alltr,ignore_index=True).sort_values('entry_time').reset_index(drop=True);t['year']=t.entry_time.dt.year;t.to_csv(OUT/'trades.csv',index=False)
 rows=[]
 for fee in [.001,.002,.003,.005]:
  for y in range(2020,2027):
   q=t[t.year==y];rows.append(dict(scope='year',key=str(y),fee=fee,**metrics(q,fee)))
  for sym,q in t.groupby('symbol'):rows.append(dict(scope='symbol',key=sym,fee=fee,**metrics(q,fee)))
  for side,q in t.groupby('side'):rows.append(dict(scope='side',key='LONG' if side==1 else 'SHORT',fee=fee,**metrics(q,fee)))
 r=pd.DataFrame(rows);r.to_csv(OUT/'stats.csv',index=False)
 print('YEAR COST STRESS');print(r[r.scope.eq('year')].to_string(index=False))
 print('\nSYMBOL @10bps');print(r[(r.scope=='symbol')&(r.fee==.001)].sort_values('ev',ascending=False).to_string(index=False))
 print('\nSIDE @10bps');print(r[(r.scope=='side')&(r.fee==.001)].to_string(index=False))
 # strict pass: 2020, 2024, 2025, 2026 each positive at 10bps; 2024-26 each positive at 30bps
 y10=r[(r.scope=='year')&(r.fee==.001)].set_index('key');y30=r[(r.scope=='year')&(r.fee==.003)].set_index('key')
 pass10=all(y in y10.index and y10.loc[y,'ev']>0 and y10.loc[y,'pf']>1 for y in ['2020','2024','2025','2026'])
 pass30=all(y in y30.index and y30.loc[y,'ev']>0 and y30.loc[y,'pf']>1 for y in ['2024','2025','2026'])
 print('\nSTRICT_PASS_10BPS_2020_2024_2025_2026=',pass10)
 print('STRESS_PASS_30BPS_2024_2025_2026=',pass30)
if __name__=='__main__':main()
