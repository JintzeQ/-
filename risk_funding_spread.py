#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];MONTHS=[str(p) for p in pd.period_range('2021-01','2026-07',freq='M')]
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('rfs_cache');OUT=Path('rfs_output');OUT.mkdir(exist_ok=True);COST=.002
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
   ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);ps.append(d[['timestamp','open']])
  q=get(F.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
  if q:
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)));x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True).dt.floor('s');x['rate']=pd.to_numeric(x.last_funding_rate,errors='coerce');fs.append(x[['timestamp','rate']].dropna())
 if not ps or not fs:return None
 px=pd.concat(ps).drop_duplicates('timestamp').set_index('timestamp').sort_index().open;fr=pd.concat(fs).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);fr['avg3']=fr.rate.rolling(3,min_periods=3).mean();return px,fr
def generate(data,signal,minspread,hold):
 times=sorted(set().union(*[set(fr.timestamp) for _,fr in data.values()]));look={s:fr.set_index('timestamp') for s,(_,fr) in data.items()};col='rate' if signal=='LAST' else 'avg3';out=[];nextfree=pd.Timestamp('1900-01-01',tz='UTC')
 for t in times:
  if t<nextfree:continue
  vals={}
  for s in data:
   fr=look[s]
   if t in fr.index:
    v=fr.at[t,col];v=v.iloc[-1] if isinstance(v,pd.Series) else v
    if np.isfinite(v):vals[s]=float(v)
  if len(vals)<6:continue
  hi=max(vals,key=vals.get);lo=min(vals,key=vals.get);spread=vals[hi]-vals[lo]
  if spread<minspread:continue
  entry=t.ceil('h')+pd.Timedelta(hours=1);exit=entry+pd.Timedelta(hours=hold);pL=data[lo][0];pH=data[hi][0]
  if any(x not in p.index for p in (pL,pH) for x in (entry,exit)):continue
  idx=pL.loc[entry:exit].index.intersection(pH.loc[entry:exit].index)
  if len(idx)<hold:continue
  eL=float(pL.at[entry]);eH=float(pH.at[entry]);frL=data[lo][1];frH=data[hi][1];path=[]
  for x in idx:
   price=float(pL.at[x]/eL-1)-float(pH.at[x]/eH-1)
   funding=float(frH.loc[(frH.timestamp>entry)&(frH.timestamp<=x),'rate'].sum()-frL.loc[(frL.timestamp>entry)&(frL.timestamp<=x),'rate'].sum())
   path.append(price+funding-COST)
  arr=np.asarray(path,float);out.append(dict(config=signal,entry=entry,exit=exit,year=entry.year,long_symbol=lo,short_symbol=hi,spread=spread,terminal=float(arr[-1]),mae=float(arr.min()),mfe=float(arr.max())))
  nextfree=exit
 return pd.DataFrame(out)
def summarize(t,name):
 rows=[]
 for y in range(2021,2027):
  z=t[t.year==y]
  if len(z): rows.append(dict(config=name,year=y,n=len(z),win=float((z.terminal>0).mean()),ev=float(z.terminal.mean()),median_mae=float(z.mae.median()),p05_mae=float(z.mae.quantile(.05)),worst_mae=float(z.mae.min()),median_mfe=float(z.mfe.median())))
 return rows
def leverage_stats(t,name):
 rows=[]
 for lev in [.25,.5,1.0,1.5,2.0]:
  rets=lev*t.terminal.to_numpy(float);eq=np.cumprod(1+rets);peak=np.maximum.accumulate(eq);dd=eq/peak-1;ruin=bool(np.any(1+rets<=0));rows.append(dict(config=name,one_leg_leverage=lev,gross_exposure=2*lev,n=len(rets),total_multiple=float(eq[-1]) if len(eq) else np.nan,max_trade_loss=float(rets.min()) if len(rets) else np.nan,max_close_dd=float(dd.min()) if len(dd) else np.nan,ruin=ruin))
 return rows
def main():
 data={s:x for s in SYMS if (x:=load(s)) is not None};ys=[];ls=[]
 for name,sig,ms,h in CONFIGS:
  t=generate(data,sig,ms,h);t['name']=name;t.to_csv(OUT/f'{name}_risk_trades.csv',index=False);ys+=summarize(t,name);ls+=leverage_stats(t,name)
 y=pd.DataFrame(ys);l=pd.DataFrame(ls);y.to_csv(OUT/'yearly_risk.csv',index=False);l.to_csv(OUT/'leverage_stress.csv',index=False);print('YEARLY RISK');print(y.to_string(index=False));print('\nLEVERAGE STRESS');print(l.to_string(index=False))
if __name__=='__main__':main()
