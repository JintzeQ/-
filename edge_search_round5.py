#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
S='SOLUSDT';MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07'];FEE=0.001
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('r5_cache');OUT=Path('r5_output');OUT.mkdir(exist_ok=True)
def dl(m):
 CACHE.mkdir(exist_ok=True);p=CACHE/f'{S}-{m}.zip'
 if p.exists() and p.stat().st_size>1000:return p
 r=requests.get(BASE.format(s=S,m=m),timeout=90);r.raise_for_status();p.write_bytes(r.content);return p
def read_month(m):
 p=dl(m)
 with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
 ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
 for c in COLS:d[c]=pd.to_numeric(d[c],errors='coerce')
 unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True)
 d=d.set_index('timestamp')[['open','high','low','close','quote_volume']]
 x=d.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','quote_volume':'sum'}).dropna().reset_index();x['month']=m;return x
def profile(day,bins=40,value_pct=.70):
 lo=float(day.low.min());hi=float(day.high.max())
 if hi<=lo:return None
 edges=np.linspace(lo,hi,bins+1);typ=(day.high+day.low+day.close)/3;idx=np.clip(np.digitize(typ,edges)-1,0,bins-1);vol=np.zeros(bins)
 for i,v in zip(idx,day.quote_volume):vol[int(i)]+=float(v)
 poc=int(np.argmax(vol));target=value_pct*vol.sum();total=vol[poc];L=R=poc
 while total<target and (L>0 or R<bins-1):
  lv=vol[L-1] if L>0 else -1;rv=vol[R+1] if R<bins-1 else -1
  if rv>=lv and R<bins-1:R+=1;total+=vol[R]
  elif L>0:L-=1;total+=vol[L]
  else:break
 return float(edges[L]),float(edges[R+1]),float((edges[poc]+edges[poc+1])/2)
def prepare():
 fs=[read_month(m) for m in MONTHS];d=pd.concat(fs,ignore_index=True).sort_values('timestamp').reset_index(drop=True)
 d['day']=d.timestamp.dt.floor('D');d['year']=d.timestamp.dt.year.astype(np.int16);d['ret']=d.close/d.open-1
 medq=d.quote_volume.shift(1).rolling(20,min_periods=20).median();d['tape']=d.quote_volume/medq.replace(0,np.nan)
 prof={}
 for day,g in d.groupby('day'):prof[day]=profile(g)
 vals=[]
 for day in d.day:
  prev=day-pd.Timedelta(days=1);vals.append(prof.get(prev))
 d[['VAL','VAH','POC']]=pd.DataFrame(vals,index=d.index)
 return d
def simulate(d,tape_thr,min_dist,sl,maxhold):
 op=d.open.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy();cl=d.close.to_numpy();ret=d.ret.to_numpy();tape=d.tape.to_numpy();VAL=d.VAL.to_numpy();VAH=d.VAH.to_numpy();POC=d.POC.to_numpy();yr=d.year.to_numpy();ts=d.timestamp.to_numpy();out=[];nxt=0
 for i in range(len(d)-1):
  if i<nxt or not np.isfinite(VAL[i]):continue
  side=0
  # sweep outside value and close back inside, with rejection candle
  if lo[i]<=VAL[i] and cl[i]>VAL[i] and ret[i]>0 and tape[i]>=tape_thr:side=1
  elif hi[i]>=VAH[i] and cl[i]<VAH[i] and ret[i]<0 and tape[i]>=tape_thr:side=-1
  if side==0:continue
  e=i+1;entry=op[e];target=POC[i]
  if side==1 and target<=entry:continue
  if side==-1 and target>=entry:continue
  dist=side*(target/entry-1)
  if dist<min_dist:continue
  slp=entry*(1-side*sl);end=min(e+maxhold-1,len(d)-1);px=cl[end];x=end;reason='TIME'
  for j in range(e,end+1):
   # never cross sampled month/day gaps; close before gap if needed
   if j>e and (pd.Timestamp(ts[j])-pd.Timestamp(ts[j-1])).total_seconds()!=300: x=j-1;px=cl[x];reason='GAP';break
   hitT=(hi[j]>=target) if side==1 else (lo[j]<=target);hitS=(lo[j]<=slp) if side==1 else (hi[j]>=slp)
   if hitS:px=slp;x=j;reason='SL';break
   if hitT:px=target;x=j;reason='POC';break
  net=side*(px/entry-1)-FEE;out.append((int(yr[e]),net,reason,dist));nxt=x+1
 return out
def met(rows,y):
 z=[r for r in rows if r[0]==y];a=np.array([r[1] for r in z],float)
 if len(a)==0:return (0,np.nan,np.nan,np.nan,np.nan)
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf),float(np.mean([r[2]=='POC' for r in z]))
def main():
 d=prepare();rows=[]
 for tape_thr,min_dist,sl,maxhold in itertools.product([1.0,1.5,2.0],[0.003,0.005,0.008],[0.01,0.015,0.02],[24,72,144]):
  tr=simulate(d,tape_thr,min_dist,sl,maxhold);n25,w25,e25,p25,h25=met(tr,2025);n26,w26,e26,p26,h26=met(tr,2026)
  rows.append(dict(tape=tape_thr,min_target_dist=min_dist,sl=sl,maxhold_5m=maxhold,n_2025=n25,win_2025=w25,ev_2025=e25,pf_2025=p25,poc_hit_2025=h25,n_2026=n26,win_2026=w26,ev_2026=e26,pf_2026=p26,poc_hit_2026=h26))
 r=pd.DataFrame(rows);r.to_csv(OUT/'r5_all.csv',index=False);train=r[(r.n_2025>=30)&(r.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False);train.to_csv(OUT/'r5_train_positive.csv',index=False);rob=train[(train.n_2026>=20)&(train.ev_2026>0)&(train.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False);rob.to_csv(OUT/'r5_robust.csv',index=False)
 print('BEST');print(r.sort_values('ev_2025',ascending=False).head(20).to_string(index=False));print('\nROBUST');print(rob.head(20).to_string(index=False) if len(rob) else 'NONE')
if __name__=='__main__':main()
