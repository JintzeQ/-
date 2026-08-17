#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
ALTS=['ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];ALL=['BTCUSDT']+ALTS
MONTHS=[str(p) for p in pd.period_range('2025-01','2026-07',freq='M')];FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r10_cache');OUT=Path('r10_output');OUT.mkdir(exist_ok=True)
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
 fs=[x for m in MONTHS if (x:=read(s,m)) is not None];d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').set_index('timestamp');return d
def main():
 raw={s:load(s) for s in ALL};btc=raw['BTCUSDT'].rename(columns={'open':'btc_open','close':'btc_close'})
 rows=[]
 for alt in ALTS:
  d=btc.join(raw[alt].rename(columns={'open':'alt_open','close':'alt_close'}),how='inner').sort_index()
  rb=np.log(d.btc_close).diff();ra=np.log(d.alt_close).diff();cov=ra.shift(1).rolling(168,min_periods=120).cov(rb.shift(1));var=rb.shift(1).rolling(168,min_periods=120).var();d['beta']=cov/var.replace(0,np.nan)
  for lb in [1,4]:
   d[f'btc{lb}']=d.btc_close/d.btc_close.shift(lb)-1;d[f'alt{lb}']=d.alt_close/d.alt_close.shift(lb)-1;d[f'resid{lb}']=d[f'alt{lb}']-d.beta*d[f'btc{lb}']
  for h in [1,4,8]:
   d[f'f{h}']=d.alt_close.shift(-h)/d.alt_open.shift(-1)-1
  d['year']=d.index.year
  d['symbol']=alt
  raw[alt]=d
 for lb,btc_move,resid_abs,hold in itertools.product([1,4],[.005,.01,.015],[.003,.005,.01],[1,4,8]):
  pnl={2025:[],2026:[]};symp={2025:{},2026:{}}
  for alt in ALTS:
   d=raw[alt]
   bm=d[f'btc{lb}'];res=d[f'resid{lb}'];f=d[f'f{hold}']
   # BTC makes a meaningful move; alt is lagging in the opposite residual direction.
   sig=(bm.abs()>=btc_move)&(res.abs()>=resid_abs)&(np.sign(res)==-np.sign(bm))&f.notna()
   side=np.sign(bm[sig]);p=(side*f[sig]-FEE)
   y=d.loc[sig,'year']
   for yy in [2025,2026]:
    a=p[y==yy].to_numpy(float);pnl[yy].extend(a);symp[yy][alt]=a
  vals=[]
  for yy in [2025,2026]:
   a=np.array(pnl[yy],float)
   if len(a):
    pos=a[a>0].sum();neg=-a[a<0].sum();pf=pos/neg if neg else np.inf;win=float((a>0).mean());ev=float(a.mean());ps=sum(len(v)>0 and np.mean(v)>0 for v in symp[yy].values())
   else:win=ev=pf=np.nan;ps=0
   vals.extend([len(a),win,ev,pf,ps])
  rows.append(dict(lookback_h=lb,btc_move=btc_move,resid_abs=resid_abs,hold_h=hold,n_2025=vals[0],win_2025=vals[1],ev_2025=vals[2],pf_2025=vals[3],pos_symbols_2025=vals[4],n_2026=vals[5],win_2026=vals[6],ev_2026=vals[7],pf_2026=vals[8],pos_symbols_2026=vals[9]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r10_all.csv',index=False);tr=r[(r.n_2025>=100)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);tr.to_csv(OUT/'r10_train_positive.csv',index=False);rob=tr[(tr.n_2026>=75)&(tr.ev_2026>0)&(tr.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r10_robust.csv',index=False);print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
