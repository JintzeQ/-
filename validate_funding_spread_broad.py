#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','NEARUSDT','ATOMUSDT','ETCUSDT','UNIUSDT','AAVEUSDT','FILUSDT','APTUSDT','ARBUSDT','OPUSDT','INJUSDT','PEPEUSDT','WIFUSDT','SEIUSDT','TIAUSDT']
MONTHS=[str(p) for p in pd.period_range('2023-01','2026-07',freq='M')]
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('vfb_cache');OUT=Path('vfb_output');OUT.mkdir(exist_ok=True);COST=.002
CONFIGS=[('A_LAST_SPREAD1BP_14D','LAST',.0001,336),('B_AVG3_RANK_7D','AVG3',0.0,168)]
def get(url,p):
 if p.exists() and p.stat().st_size>100:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=60)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p
def load(s):
 ps=[];fs=[]
 for m in MONTHS:
  p=get(K.format(s=s,m=m),CACHE/'px'/s/f'{m}.zip')
  if p:
   with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COL,low_memory=False)
   ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce');d.quote_volume=pd.to_numeric(d.quote_volume,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);ps.append(d[['timestamp','open','quote_volume']])
  q=get(F.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
  if q:
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)));x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True).dt.floor('s');x['rate']=pd.to_numeric(x.last_funding_rate,errors='coerce');fs.append(x[['timestamp','rate']].dropna())
 if not ps or not fs:return None
 px=pd.concat(ps).drop_duplicates('timestamp').set_index('timestamp').sort_index();px['qv24']=px.quote_volume.shift(1).rolling(24,min_periods=24).sum();fr=pd.concat(fs).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);fr['avg3']=fr.rate.rolling(3,min_periods=3).mean();print(s,len(px),len(fr),flush=True);return px,fr
def run(data,signal,ms,hold):
 times=sorted(set().union(*[set(fr.timestamp) for _,fr in data.values()]));look={s:fr.set_index('timestamp') for s,(_,fr) in data.items()};col='rate' if signal=='LAST' else 'avg3';out=[];nextfree=pd.Timestamp('1900-01-01',tz='UTC')
 for t in times:
  if t<nextfree:continue
  rows=[]
  for s,(px,fr0) in data.items():
   fr=look[s]
   if t not in fr.index:continue
   v=fr.at[t,col];v=v.iloc[-1] if isinstance(v,pd.Series) else v
   # liquidity known from prior 24h at funding timestamp or floored hour
   th=t.floor('h')
   if th not in px.index:continue
   qv=px.at[th,'qv24']
   if isinstance(qv,pd.Series):qv=qv.iloc[-1]
   if np.isfinite(v) and np.isfinite(qv):rows.append((s,float(v),float(qv)))
  if len(rows)<8:continue
  rows=sorted(rows,key=lambda x:x[2],reverse=True)[:15]
  vals={s:v for s,v,_ in rows};hi=max(vals,key=vals.get);lo=min(vals,key=vals.get);spread=vals[hi]-vals[lo]
  if spread<ms:continue
  entry=t.ceil('h')+pd.Timedelta(hours=1);exit=entry+pd.Timedelta(hours=hold);pH=data[hi][0];pL=data[lo][0]
  if any(x not in p.index for p in (pH,pL) for x in (entry,exit)):continue
  price=float(pL.at[exit,'open']/pL.at[entry,'open']-1)-float(pH.at[exit,'open']/pH.at[entry,'open']-1);frH=data[hi][1];frL=data[lo][1];fund=float(frH.loc[(frH.timestamp>entry)&(frH.timestamp<=exit),'rate'].sum()-frL.loc[(frL.timestamp>entry)&(frL.timestamp<=exit),'rate'].sum());net=price+fund-COST;out.append((entry.year,net,price,fund,hi,lo,spread));nextfree=exit
 return out
def met(z):
 a=np.array([q[1] for q in z],float)
 if not len(a):return 0,np.nan,np.nan,np.nan
 pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;return len(a),float((a>0).mean()),float(a.mean()),float(pf)
def main():
 data={s:x for s in SYMS if (x:=load(s)) is not None};print('loaded symbols',len(data),list(data));rows=[]
 for name,sig,ms,h in CONFIGS:
  tr=run(data,sig,ms,h);print('\n',name,'N',len(tr))
  vals=[]
  for y in [2023,2024,2025,2026]:vals+=list(met([q for q in tr if q[0]==y]))
  rows.append(dict(config=name,n_2023=vals[0],win_2023=vals[1],ev_2023=vals[2],pf_2023=vals[3],n_2024=vals[4],win_2024=vals[5],ev_2024=vals[6],pf_2024=vals[7],n_2025=vals[8],win_2025=vals[9],ev_2025=vals[10],pf_2025=vals[11],n_2026=vals[12],win_2026=vals[13],ev_2026=vals[14],pf_2026=vals[15]))
 r=pd.DataFrame(rows);r.to_csv(OUT/'broad_results.csv',index=False);print(r.to_string(index=False))
if __name__=='__main__':main()
