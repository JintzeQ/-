#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/4h/{s}-4h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r16_cache');OUT=Path('r16_output');OUT.mkdir(exist_ok=True)
ENTRY=120;EXIT=60

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
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True);d=d[['ts','open','high','low','close']].dropna().sort_values('ts').drop_duplicates('ts').reset_index(drop=True);d['symbol']=s;d['year']=d.ts.dt.year.astype(np.int16)
 ret=d.close.pct_change().abs();d['er']=((d.close/d.close.shift(ENTRY)-1).abs()/ret.rolling(ENTRY,min_periods=ENTRY).sum()).replace([np.inf,-np.inf],np.nan)
 d['sma']=d.close.rolling(ENTRY,min_periods=ENTRY).mean();return d

def simulate(d,btc,self_er,mkt_er,align,mode):
 x=d.merge(btc[['ts','btc_er','btc_close','btc_sma']],on='ts',how='left');op=x.open.to_numpy();cl=x.close.to_numpy();yr=x.year.to_numpy();ts=x.ts.to_numpy();er=x.er.to_numpy();ber=x.btc_er.to_numpy();bc=x.btc_close.to_numpy();bs=x.btc_sma.to_numpy();ph=x.high.shift(1).rolling(ENTRY,min_periods=ENTRY).max().to_numpy();pl=x.low.shift(1).rolling(ENTRY,min_periods=ENTRY).min().to_numpy();tl=x.low.shift(1).rolling(EXIT,min_periods=EXIT).min().to_numpy();th=x.high.shift(1).rolling(EXIT,min_periods=EXIT).max().to_numpy();out=[];i=ENTRY
 while i<len(x)-2:
  if not np.isfinite(er[i]) or er[i]<self_er or not np.isfinite(ber[i]) or ber[i]<mkt_er:i+=1;continue
  side=1 if cl[i]>ph[i]*1.001 else (-1 if cl[i]<pl[i]*.999 else 0)
  if not side:i+=1;continue
  if mode=='long' and side<0:i+=1;continue
  if mode=='short' and side>0:i+=1;continue
  if align and ((side>0 and not (bc[i]>bs[i])) or (side<0 and not (bc[i]<bs[i]))):i+=1;continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).total_seconds()!=14400:i+=1;continue
  entry=op[e];j=e
  for k in range(e+1,min(e+1080,len(x)-1)):
   if (pd.Timestamp(ts[k])-pd.Timestamp(ts[k-1])).total_seconds()!=14400:j=k-1;break
   if (side>0 and cl[k]<tl[k]) or (side<0 and cl[k]>th[k]):j=k+1 if k+1<len(x) else k;break
   j=k
  exitp=op[j] if j>e else cl[j];out.append((int(yr[e]),str(x.symbol.iloc[e]),side*(exitp/entry-1)-FEE,j-e,side));i=max(j+1,e+1)
 return out

def met(z):
 if not z:return (0,np.nan,np.nan,np.nan,0,np.nan,np.nan)
 a=np.array([q[2] for q in z]);pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for q in z:ss.setdefault(q[1],[]).append(q[2])
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.median([q[3] for q in z])),float(np.mean([q[4] for q in z]))

def main():
 data={s:read_all(s) for s in SYMBOLS};data={s:d for s,d in data.items() if d is not None};b=data['BTCUSDT'].copy();b=b.rename(columns={'er':'btc_er','close':'btc_close','sma':'btc_sma'});rows=[]
 for se,me,align,mode in itertools.product([0,.15,.25,.35],[0,.15,.25,.35],[False,True],['both','long','short']):
  tr=[]
  for d in data.values():tr+=simulate(d,b,se,me,align,mode)
  a=met([q for q in tr if q[0] in (2023,2024)]);y23=met([q for q in tr if q[0]==2023]);y24=met([q for q in tr if q[0]==2024]);y25=met([q for q in tr if q[0]==2025]);y26=met([q for q in tr if q[0]==2026])
  rows.append(dict(self_er=se,mkt_er=me,align=align,mode=mode,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],ev_2023=y23[2],ev_2024=y24[2],n_2025=y25[0],win_2025=y25[1],ev_2025=y25[2],pf_2025=y25[3],pos_symbols_2025=y25[4],n_2026=y26[0],win_2026=y26[1],ev_2026=y26[2],pf_2026=y26[3],pos_symbols_2026=y26[4]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r16_all.csv',index=False);sel=r[(r.n_train>=80)&(r.ev_2023>0)&(r.ev_2024>0)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['ev_train','pf_train'],ascending=False);rob=sel[(sel.n_2025>=30)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=20)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['ev_2026','ev_2025'],ascending=False);rob.to_csv(OUT/'r16_robust.csv',index=False);print('BEST TRAIN');print(sel.head(25).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')

def read_all(s):
 fs=[]
 for m in MONTHS:
  d=read(s,m)
  if d is not None:fs.append(d[['ts','open','high','low','close']])
 if not fs:return None
 d=pd.concat(fs,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True);d['symbol']=s;d['year']=d.ts.dt.year.astype(np.int16);ret=d.close.pct_change().abs();d['er']=((d.close/d.close.shift(ENTRY)-1).abs()/ret.rolling(ENTRY,min_periods=ENTRY).sum()).replace([np.inf,-np.inf],np.nan);d['sma']=d.close.rolling(ENTRY,min_periods=ENTRY).mean();return d
if __name__=='__main__':main()
