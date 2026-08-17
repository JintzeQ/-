#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2020-01','2026-07',freq='M')]
FEE=.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/4h/{s}-4h-{m}.zip'
COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('rp_cache');OUT=Path('rp_output');OUT.mkdir(exist_ok=True)
def dl(s,m):
 p=CACHE/s/f'{m}.zip'
 if p.exists() and p.stat().st_size>300:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(BASE.format(s=s,m=m),timeout=60)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def read(s,m):
 p=dl(s,m)
 if p is None:return None
 with zipfile.ZipFile(p) as z:
  n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COL,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in ['open_time','open','high','low','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);return d[['timestamp','open','high','low','close']].dropna()
def load(s):
 fs=[]
 for m in MONTHS:
  x=read(s,m)
  if x is not None:fs.append(x)
 if not fs:return None
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s
 # Build completed daily closes. Regime for day D uses daily close through D-1 only.
 daily=d.set_index('timestamp').close.resample('1D').last().dropna().to_frame('dc')
 daily['sma100']=daily.dc.shift(1).rolling(100,min_periods=100).mean();daily['slope10']=daily.sma100/daily.sma100.shift(10)-1
 daily['regime']=np.where((daily.dc>daily.sma100)&(daily.slope10>=0),1,np.where((daily.dc<daily.sma100)&(daily.slope10<=0),-1,0))
 # Shift regime one day so no current-day close leak into intraday bars.
 daily['use_regime']=daily.regime.shift(1)
 mp=d.timestamp.dt.floor('D').map(daily.use_regime);d['regime']=pd.to_numeric(mp,errors='coerce').fillna(0).astype(np.int8)
 for span in [6,12]:d[f'ema{span}']=d.close.ewm(span=span,adjust=False).mean()
 d['year']=d.timestamp.dt.year.astype(np.int16);print(s,len(d),flush=True);return d
def simulate(d,ema_span,tp,sl,maxbars):
 ema=d[f'ema{ema_span}'].to_numpy();op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();rg=d.regime.to_numpy();yr=d.year.to_numpy();ts=d.timestamp.to_numpy();out=[];nxt=1
 for i in range(1,len(d)-1):
  if i<nxt or rg[i]==0:continue
  side=0
  # Pullback/reclaim: previous bar on wrong side of EMA, current closes back through EMA in regime direction.
  if rg[i]==1 and cl[i-1]<=ema[i-1] and cl[i]>ema[i] and cl[i]>op[i]:side=1
  elif rg[i]==-1 and cl[i-1]>=ema[i-1] and cl[i]<ema[i] and cl[i]<op[i]:side=-1
  if side==0:continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).total_seconds()!=14400:continue
  entry=op[e];tpp=entry*(1+side*tp);slp=entry*(1-side*sl);end=min(e+maxbars-1,len(d)-1);px=cl[end];x=end;reason='TIME'
  for j in range(e,end+1):
   if j>e and (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=14400:x=j-1;px=cl[x];reason='GAP';break
   ht=(hi[j]>=tpp) if side==1 else (lo[j]<=tpp);hs=(lo[j]<=slp) if side==1 else (hi[j]>=slp)
   if hs:px=slp;x=j;reason='SL';break
   if ht:px=tpp;x=j;reason='TP';break
   # regime exit only at new day when daily regime has flipped/flat
   if rg[j]!=side:px=cl[j];x=j;reason='REGIME';break
  net=side*(px/entry-1)-FEE;out.append((int(yr[e]),str(d.symbol.iloc[e]),float(net),reason,x-e+1,side));nxt=x+1
 return out
def met(z):
 a=np.array([q[2] for q in z],float)
 if not len(a):return 0,np.nan,np.nan,np.nan,0,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;ss={}
 for _,s,p,*_ in z:ss.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in ss.values()),float(np.median([q[4] for q in z])),float(np.mean([q[3]=='TP' for q in z]))
def main():
 data={s:load(s) for s in SYMS};data={s:d for s,d in data.items() if d is not None};rows=[]
 for ema,tp,sl,mh in itertools.product([6,12],[.015,.02,.03,.04],[.01,.015,.02],[6,12,18,36]):
  tr=[]
  for d in data.values():tr+=simulate(d,ema,tp,sl,mh)
  a=met([q for q in tr if q[0] in (2021,2022,2023)]);b=met([q for q in tr if q[0]==2024]);c=met([q for q in tr if q[0]==2025]);e=met([q for q in tr if q[0]==2026])
  rows.append(dict(ema=ema,tp=tp,sl=sl,maxbars=mh,n_train=a[0],win_train=a[1],ev_train=a[2],pf_train=a[3],pos_symbols_train=a[4],medhold_train=a[5],tp_rate_train=a[6],n_2024=b[0],win_2024=b[1],ev_2024=b[2],pf_2024=b[3],n_2025=c[0],win_2025=c[1],ev_2025=c[2],pf_2025=c[3],n_2026=e[0],win_2026=e[1],ev_2026=e[2],pf_2026=e[3]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'rp_all.csv',index=False);sel=r[(r.n_train>=150)&(r.ev_train>0)&(r.pf_train>1)].sort_values(['win_train','ev_train'],ascending=False);sel.to_csv(OUT/'rp_train_positive.csv',index=False);rob=sel[(sel.n_2024>=60)&(sel.ev_2024>0)&(sel.pf_2024>1)&(sel.n_2025>=60)&(sel.ev_2025>0)&(sel.pf_2025>1)&(sel.n_2026>=30)&(sel.ev_2026>0)&(sel.pf_2026>1)].sort_values(['win_2026','ev_2026'],ascending=False);rob.to_csv(OUT/'rp_robust.csv',index=False)
 print('BEST TRAIN HIGH-WIN');print(sel.head(25).to_string(index=False) if len(sel) else 'NONE');print('\nROBUST');print(rob.head(30).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
