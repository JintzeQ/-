#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07'];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r6_cache');OUT=Path('r6_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 d=CACHE/s;d.mkdir(parents=True,exist_ok=True);p=d/f'{s}-{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 r=requests.get(BASE.format(s=s,m=m),timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 with zipfile.ZipFile(dl(s,m)) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);d['symbol']=s;return d[['timestamp','symbol','open','close']].dropna()
def build(s):
 d=pd.concat([read(s,m) for m in MONTHS],ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
 d['year']=d.timestamp.dt.year.astype(np.int16)
 for lb in [60,240,720]:
  ok=(d.timestamp-d.timestamp.shift(lb)).dt.total_seconds().eq(lb*60);d[f'r{lb}']=(d.close/d.close.shift(lb)-1).where(ok)
 for h in [240,480,1440]:
  ok=(d.timestamp.shift(-h)-d.timestamp).dt.total_seconds().eq(h*60);d[f'f{h}']=(d.close.shift(-h)/d.open.shift(-1)-1).where(ok)
 return d[(d.timestamp.dt.minute==0)&(d.timestamp.dt.hour%4==0)].copy()
def met(a):
 a=np.asarray(a,float)
 if len(a)==0:return 0,np.nan,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf)
def main():
 d=pd.concat([build(s) for s in SYMBOLS],ignore_index=True);rows=[]
 for lb,k,minspread,hold in itertools.product([60,240,720],[1,2],[.005,.01,.02],[240,480,1440]):
  trades={2025:[],2026:[]};possym={2025:{s:[] for s in SYMBOLS},2026:{s:[] for s in SYMBOLS}}
  for t,g in d.groupby('timestamp'):
   g=g.dropna(subset=[f'r{lb}',f'f{hold}'])
   if len(g)<8:continue
   vals=g[f'r{lb}'];spread=float(vals.max()-vals.min())
   if spread<minspread:continue
   losers=g.nsmallest(k,f'r{lb}');winners=g.nlargest(k,f'r{lb}')
   for _,r in losers.iterrows():
    y=int(r.year);p=float(r[f'f{hold}']-FEE);trades[y].append(p);possym[y][r.symbol].append(p)
   for _,r in winners.iterrows():
    y=int(r.year);p=float(-r[f'f{hold}']-FEE);trades[y].append(p);possym[y][r.symbol].append(p)
  a=met(trades[2025]);b=met(trades[2026]);ps25=sum(len(v)>0 and np.mean(v)>0 for v in possym[2025].values());ps26=sum(len(v)>0 and np.mean(v)>0 for v in possym[2026].values())
  rows.append(dict(lookback_min=lb,k=k,min_cross_spread=minspread,hold_min=hold,n_2025=a[0],win_2025=a[1],ev_2025=a[2],pf_2025=a[3],pos_symbols_2025=ps25,n_2026=b[0],win_2026=b[1],ev_2026=b[2],pf_2026=b[3],pos_symbols_2026=ps26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r6_all.csv',index=False);tr=r[(r.n_2025>=200)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r6_train_positive.csv',index=False);rob=tr[(tr.n_2026>=150)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r6_robust.csv',index=False)
 print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
