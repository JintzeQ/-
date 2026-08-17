#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile,time
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTC','ETH','SOL'];MONTHS=[str(p) for p in pd.period_range('2024-01','2026-07',freq='M')]
BKL='https://data.binance.vision/data/futures/um/monthly/klines/{s}USDT/8h/{s}USDT-8h-{m}.zip';BFR='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}USDT/{s}USDT-fundingRate-{m}.zip';COLS=['ot','o','h','l','c','v','ct','qv','n','tb','tq','x'];OUT=Path('r29_output');OUT.mkdir(exist_ok=True);CACHE=Path('r29_cache')
START=pd.Timestamp('2024-01-01',tz='UTC');END=pd.Timestamp('2026-08-01',tz='UTC')
def get(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=90)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def binance(s):
 ks=[];fs=[]
 for m in MONTHS:
  p=get(BKL.format(s=s,m=m),CACHE/'bk'/s/f'{m}.zip')
  if p:
   with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
   ot=pd.to_numeric(d.ot,errors='coerce');d=d[ot.notna()].copy();d['ot']=pd.to_numeric(d.ot,errors='coerce');d['o']=pd.to_numeric(d.o,errors='coerce');unit='us' if float(d.ot.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.ot,unit=unit,utc=True).dt.floor('8h');ks.append(d[['ts','o']].dropna())
  p=get(BFR.format(s=s,m=m),CACHE/'bf'/s/f'{m}.zip')
  if p:
   with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];f=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['time','interval','rate'],low_memory=False)
   t=pd.to_numeric(f.time,errors='coerce');f=f[t.notna()].copy();f['time']=pd.to_numeric(f.time,errors='coerce');f['rate']=pd.to_numeric(f.rate,errors='coerce');f['ts']=pd.to_datetime(f.time,unit='ms',utc=True);fs.append(f[['ts','rate']].dropna())
 return pd.concat(ks).drop_duplicates('ts').sort_values('ts'),pd.concat(fs).sort_values('ts')
def hl_post(body):
 for k in range(5):
  try:
   r=requests.post('https://api.hyperliquid.xyz/info',json=body,timeout=60);r.raise_for_status();return r.json()
  except Exception as e:
   if k==4:raise
   time.sleep(1.5*(k+1))
def hyper(s):
 a=int(START.timestamp()*1000);b=int(END.timestamp()*1000)
 c=hl_post({'type':'candleSnapshot','req':{'coin':s,'interval':'8h','startTime':a,'endTime':b}})
 hc=pd.DataFrame(c);hc['ts']=pd.to_datetime(pd.to_numeric(hc['t']),unit='ms',utc=True).dt.floor('8h');hc['o']=pd.to_numeric(hc['o']);hc=hc[['ts','o']].drop_duplicates('ts').sort_values('ts')
 rows=[];cur=a
 while cur<b:
  z=hl_post({'type':'fundingHistory','coin':s,'startTime':cur,'endTime':b})
  if not z:break
  rows.extend(z);last=max(int(x['time']) for x in z)
  if last<cur:break
  cur=last+1
  if len(z)<400:break
  time.sleep(.15)
 hf=pd.DataFrame(rows);hf['ts']=pd.to_datetime(pd.to_numeric(hf.time),unit='ms',utc=True);hf['rate']=pd.to_numeric(hf.fundingRate);hf=hf[['ts','rate']].drop_duplicates().sort_values('ts')
 print(s,'HL candles',len(hc),'fund',len(hf),hc.ts.min(),hc.ts.max(),flush=True);return hc,hf
def trades(s,bk,bf,hk,hf):
 out=[]
 for y in (2024,2025,2026):
  for m in (1,4,7,10):
   t0=pd.Timestamp(year=y,month=m,day=1,tz='UTC');t1=t0+pd.Timedelta(days=90)
   if t1>min(bk.ts.max(),hk.ts.max()):continue
   bo0=bk.loc[bk.ts==t0,'o'];bo1=bk.loc[bk.ts==t1,'o'];ho0=hk.loc[hk.ts==t0,'o'];ho1=hk.loc[hk.ts==t1,'o']
   if min(len(bo0),len(bo1),len(ho0),len(ho1))==0:continue
   price=float(bo1.iloc[0]/bo0.iloc[0]-1-(ho1.iloc[0]/ho0.iloc[0]-1))
   bfund=float(bf[(bf.ts>t0)&(bf.ts<t1)].rate.sum());hfund=float(hf[(hf.ts>t0)&(hf.ts<t1)].rate.sum());gross=price+hfund-bfund
   out.append(dict(symbol=s,entry=t0,year=y,price=price,hl_funding=hfund,bin_funding=bfund,funding_spread=hfund-bfund,gross=gross))
 return out
def met(g,cost):
 if len(g)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan)
 a=g.gross.to_numpy()-cost;pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf))
def main():
 tr=[]
 for s in SYMS:
  bk,bf=binance(s);hk,hf=hyper(s);tr+=trades(s,bk,bf,hk,hf)
 d=pd.DataFrame(tr);d.to_csv(OUT/'trades.csv',index=False);rows=[]
 for cost in (.002,.003,.004):
  for y in (2024,2025,2026):rows.append(dict(cost=cost,year=y,**met(d[d.year==y],cost)))
  rows.append(dict(cost=cost,year='ALL',**met(d,cost)))
 r=pd.DataFrame(rows);r.to_csv(OUT/'metrics.csv',index=False);print(r.to_string(index=False));print('\nRAW');print(d.groupby('year')[['gross','price','funding_spread']].mean().to_string())
if __name__=='__main__':main()
