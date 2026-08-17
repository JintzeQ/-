#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2024-01','2026-07',freq='M')];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r12_cache');OUT=Path('r12_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>500:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 p=dl(s,m)
 if p is None:return None
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','high','low','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open','high','low','close']].dropna()
def load(s):
 fs=[x for m in MONTHS if (x:=read(s,m)) is not None];d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s;lr=np.log(d.close).diff();d['rv24']=np.sqrt(lr.pow(2).shift(1).rolling(24,min_periods=24).sum());d['year']=d.timestamp.dt.year.astype(np.int16);return d
def sim(d,rvmin,rvmax):
 EN=48;XN=12;BUF=.001;MH=168;op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();rv=d.rv24.to_numpy();yr=d.year.to_numpy();ts=d.timestamp.to_numpy();ph=d.high.shift(1).rolling(EN,min_periods=EN).max().to_numpy();pl=d.low.shift(1).rolling(EN,min_periods=EN).min().to_numpy();tl=d.low.shift(1).rolling(XN,min_periods=XN).min().to_numpy();th=d.high.shift(1).rolling(XN,min_periods=XN).max().to_numpy();out=[];i=EN
 while i<len(d)-2:
  if not np.isfinite(rv[i]) or rv[i]<rvmin or (np.isfinite(rvmax) and rv[i]>=rvmax):i+=1;continue
  side=1 if cl[i]>ph[i]*(1+BUF) else (-1 if cl[i]<pl[i]*(1-BUF) else 0)
  if side==0:i+=1;continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).total_seconds()!=3600:i+=1;continue
  entry=op[e];x=e;reason='MAX'
  for j in range(e+1,min(e+MH,len(d)-1)):
   if (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=3600:x=j-1;reason='GAP';break
   if side==1 and cl[j]<tl[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   if side==-1 and cl[j]>th[j]:x=j+1 if j+1<len(d) else j;reason='TRAIL';break
   x=j
  xp=op[x] if reason=='TRAIL' and x>e else cl[x];out.append((int(yr[e]),str(d.symbol.iloc[e]),side*(xp/entry-1)-FEE,rv[i]));i=max(x+1,e+1)
 return out
def met(z):
 a=np.array([q[2] for q in z],float)
 if not len(a):return 0,np.nan,np.nan,np.nan,0
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for _,s,p,_ in z:ss.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values())
def main():
 data={s:load(s) for s in SYMBOLS};rows=[]
 cfg=[(0,np.nan),(.015,np.nan),(.025,np.nan),(.035,np.nan),(.045,np.nan),(.025,.06),(.035,.07)]
 for lo,hi in cfg:
  tr=[]
  for d in data.values():tr+=sim(d,lo,hi)
  vals=[]
  for y in [2024,2025,2026]:vals+=list(met([q for q in tr if q[0]==y]))
  rows.append(dict(rvmin=lo,rvmax=hi,n_2024=vals[0],win_2024=vals[1],ev_2024=vals[2],pf_2024=vals[3],pos_symbols_2024=vals[4],n_2025=vals[5],win_2025=vals[6],ev_2025=vals[7],pf_2025=vals[8],pos_symbols_2025=vals[9],n_2026=vals[10],win_2026=vals[11],ev_2026=vals[12],pf_2026=vals[13],pos_symbols_2026=vals[14]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r12_all.csv',index=False);train=r[(r.n_2025>=300)&(r.ev_2025>0)].sort_values(['ev_2025','pf_2025'],ascending=False);rob=train[(train.n_2024>=250)&(train.ev_2024>0)&(train.n_2026>=150)&(train.ev_2026>0)&(train.pf_2024>1)&(train.pf_2026>1)].sort_values(['ev_2026','ev_2024'],ascending=False);print(r.to_string(index=False));print('\nROBUST 3Y');print(rob.to_string(index=False) if len(rob) else 'NONE');r.to_csv(OUT/'r12_all.csv',index=False);rob.to_csv(OUT/'r12_robust.csv',index=False)
if __name__=='__main__':main()
