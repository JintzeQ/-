#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
ALTS=['ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];ALL=['BTCUSDT']+ALTS
MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')];COST=.002
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r11_cache');OUT=Path('r11_output');OUT.mkdir(exist_ok=True)
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
 for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open','close']].dropna()
def load(s):
 fs=[x for m in MONTHS if (x:=read(s,m)) is not None];return pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').set_index('timestamp')
def build(alt,raw):
 b=raw['BTCUSDT'].rename(columns={'open':'bo','close':'bc'});a=raw[alt].rename(columns={'open':'ao','close':'ac'});d=b.join(a,how='inner').sort_index();rb=np.log(d.bc).diff();ra=np.log(d.ac).diff();beta=ra.shift(1).rolling(168,min_periods=120).cov(rb.shift(1))/rb.shift(1).rolling(168,min_periods=120).var();d['beta']=beta
 # 24h residual return using contemporaneous fixed beta estimate from prior data.
 d['r24a']=np.log(d.ac/d.ac.shift(24));d['r24b']=np.log(d.bc/d.bc.shift(24));d['resid']=d.r24a-d.beta*d.r24b
 mu=d.resid.shift(1).rolling(720,min_periods=360).mean();sd=d.resid.shift(1).rolling(720,min_periods=360).std();d['z']=(d.resid-mu)/sd.replace(0,np.nan);d['year']=d.index.year;return d
def simulate(d,zentry,zexit,maxh):
 out=[];i=0;n=len(d);idx=d.index
 while i<n-2:
  z=d.z.iloc[i]
  if not np.isfinite(z) or abs(z)<zentry:i+=1;continue
  side=-1 if z>0 else 1 # long alt/short beta BTC if residual cheap; opposite if rich
  beta=float(d.beta.iloc[i]);e=i+1
  if (idx[e]-idx[i]).total_seconds()!=3600:i+=1;continue
  ao=float(d.ao.iloc[e]);bo=float(d.bo.iloc[e]);x=e;reason='MAX'
  for j in range(e+1,min(e+maxh,n-1)):
   if (idx[j]-idx[j-1]).total_seconds()!=3600:x=j-1;reason='GAP';break
   zj=d.z.iloc[j]
   if np.isfinite(zj) and abs(zj)<=zexit:
    x=j+1 if j+1<n else j;reason='MEAN';break
   x=j
  if x>=n:x=n-1
  # if exit signal on close, execute next open; otherwise final close approximated by next available close
  if reason=='MEAN':ae=float(d.ao.iloc[x]);be=float(d.bo.iloc[x])
  else:ae=float(d.ac.iloc[x]);be=float(d.bc.iloc[x])
  rel=(ae/ao-1)-beta*(be/bo-1);net=side*rel-COST;out.append((int(d.year.iloc[e]),float(net),side,reason,x-e,beta));i=max(x+1,e+1)
 return out
def met(a):
 if not a:return 0,np.nan,np.nan,np.nan,np.nan
 x=np.array([q[1] for q in a]);pf=x[x>0].sum()/(-x[x<0].sum()) if np.any(x<0) else np.inf;return len(x),float((x>0).mean()),float(x.mean()),float(pf),float(np.median([q[4] for q in a]))
def main():
 raw={s:load(s) for s in ALL};ds={a:build(a,raw) for a in ALTS};rows=[]
 for ze,zx,mh in itertools.product([2.0,2.5,3.0],[0.25,0.5,1.0],[24,72,168]):
  by={2025:[],2026:[]};sym={2025:{},2026:{}}
  for a,d in ds.items():
   tr=simulate(d,ze,zx,mh)
   for y in [2025,2026]:sym[y][a]=[q for q in tr if q[0]==y];by[y]+=sym[y][a]
  a=met(by[2025]);b=met(by[2026]);ps25=sum(len(v)>0 and np.mean([q[1] for q in v])>0 for v in sym[2025].values());ps26=sum(len(v)>0 and np.mean([q[1] for q in v])>0 for v in sym[2026].values())
  rows.append(dict(zentry=ze,zexit=zx,maxhold_h=mh,n_2025=a[0],win_2025=a[1],ev_2025=a[2],pf_2025=a[3],median_hold_2025=a[4],pos_symbols_2025=ps25,n_2026=b[0],win_2026=b[1],ev_2026=b[2],pf_2026=b[3],median_hold_2026=b[4],pos_symbols_2026=ps26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r11_all.csv',index=False);tr=r[(r.n_2025>=100)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r11_train_positive.csv',index=False);rob=tr[(tr.n_2026>=75)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r11_robust.csv',index=False);print('BEST');print(r.sort_values('ev_2025',ascending=False).to_string(index=False));print('\nROBUST');print(rob.to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
