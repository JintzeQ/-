#!/usr/bin/env python3
from __future__ import annotations
import io,itertools,time,zipfile
from pathlib import Path
import numpy as np, pandas as pd, requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07']
FEE=0.001; COOLDOWN=10
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'; COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r2_cache'); OUT=Path('r2_output'); OUT.mkdir(exist_ok=True)
EXIT=[(0.004,0.003,15),(0.005,0.003,20),(0.005,0.004,30),(0.007,0.004,30),(0.007,0.005,45),(0.010,0.005,60),(0.010,0.007,60)]

def dl(s,m):
 d=CACHE/s; d.mkdir(parents=True,exist_ok=True); p=d/f'{s}-{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 r=requests.get(BASE.format(s=s,m=m),timeout=90)
 if r.status_code==404:return None
 r.raise_for_status(); p.write_bytes(r.content); return p

def read(p,s):
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0]; d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce'); d=d[ot.notna()].copy()
 for c in COLS:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms'; d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True); d['symbol']=s
 return d[['timestamp','symbol','open','high','low','close','quote_volume','taker_quote']].dropna()

def load(s):
 fs=[]
 for m in MONTHS:
  p=dl(s,m)
  if p is not None:fs.append(read(p,s))
 d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
 d['ret1']=d.close/d.open-1; d['ret5']=d.close/d.close.shift(5)-1
 med5=d.ret5.abs().shift(5).rolling(360,min_periods=360).median(); d['move5']=d.ret5.abs()/med5.replace(0,np.nan)
 q5=d.quote_volume.rolling(5,min_periods=5).sum(); qbase=q5.shift(5).rolling(72,min_periods=72).median(); d['v5']=q5/qbase.replace(0,np.nan)
 b5=d.taker_quote.rolling(5,min_periods=5).sum(); d['bs5']=b5/q5.replace(0,np.nan)
 rng=(d.high-d.low).replace(0,np.nan); d['clv']=(d.close-d.low)/rng
 d['year']=d.timestamp.dt.year.astype(np.int16)
 print(s,len(d),flush=True); return d

def sig(d,score,minret,vshock,flow,confirm):
 r5=d.ret5.to_numpy(); m5=d.move5.to_numpy(); v5=d.v5.to_numpy(); bs=d.bs5.to_numpy(); r1=d.ret1.to_numpy(); clv=d.clv.to_numpy()
 base=(m5>=score)&(np.abs(r5)>=minret)&(v5>=vshock)
 dr=np.zeros(len(d),np.int8)
 # impulse down + extreme sell flow, but current 1m has turned up and closed high in its range
 dr[base&(r5<0)&(bs<=1-flow)&(r1>0)&(clv>=confirm)]=1
 dr[base&(r5>0)&(bs>=flow)&(r1<0)&(clv<=1-confirm)]=-1
 return np.flatnonzero(dr!=0),dr

def sim(d,idx,dr,tp,sl,stop):
 op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();yr=d.year.to_numpy();sy=d.symbol.to_numpy();out=[];nxt=0
 for i in idx:
  e=i+1
  if e>=len(d) or e<nxt:continue
  side=int(dr[i]);entry=op[e];end=min(e+stop-1,len(d)-1);px=cl[end];x=end
  tpp=entry*(1+side*tp);slp=entry*(1-side*sl)
  for j in range(e,end+1):
   htp=(hi[j]>=tpp) if side==1 else (lo[j]<=tpp);hsl=(lo[j]<=slp) if side==1 else (hi[j]>=slp)
   if hsl:px=slp;x=j;break
   if htp:px=tpp;x=j;break
  out.append((int(yr[e]),str(sy[e]),side*(px/entry-1)-FEE));nxt=x+1+COOLDOWN
 return out

def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[2] for r in z],float)
 if len(a)==0:return (0,np.nan,np.nan,np.nan,0)
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf
 se={}
 for _,s,p in z:se.setdefault(s,[]).append(p)
 return len(a),float((a>0).mean()),float(a.mean()),float(pf),sum(np.mean(v)>0 for v in se.values())

def main():
 data={s:load(s) for s in SYMBOLS};acc={}
 for score,minret,vshock,flow,confirm in itertools.product([3,5,8],[0.005,0.008,0.012],[2,3,4],[0.60,0.70],[0.60,0.75]):
  kb=(score,minret,vshock,flow,confirm)
  for s,d in data.items():
   idx,dr=sig(d,*kb)
   for tp,sl,stop in EXIT:
    k=kb+(tp,sl,stop);acc.setdefault(k,[]).extend(sim(d,idx,dr,tp,sl,stop))
 rows=[]
 for k,tr in acc.items():
  score,minret,vshock,flow,confirm,tp,sl,stop=k;n25,w25,e25,p25,s25=met(tr,2025);n26,w26,e26,p26,s26=met(tr,2026)
  rows.append(dict(score=score,minret=minret,vshock=vshock,flow=flow,confirm=confirm,tp=tp,sl=sl,stop=stop,n_2025=n25,win_2025=w25,ev_2025=e25,pf_2025=p25,pos_symbols_2025=s25,n_2026=n26,win_2026=w26,ev_2026=e26,pf_2026=p26,pos_symbols_2026=s26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r2_all.csv',index=False)
 train=r[(r.n_2025>=80)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);train.to_csv(OUT/'r2_train_positive.csv',index=False)
 robust=train[(train.n_2026>=60)&(train.ev_2026>0)&(train.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);robust.to_csv(OUT/'r2_robust.csv',index=False)
 print('BEST 2025');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(robust.head(20).to_string(index=False) if len(robust) else 'NONE')
if __name__=='__main__':main()
