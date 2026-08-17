#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2021-01','2026-07',freq='M')]
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip'
F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('vfs_cache');OUT=Path('vfs_output');OUT.mkdir(exist_ok=True)
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
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)))
   x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True).dt.floor('s');x['rate']=pd.to_numeric(x.last_funding_rate,errors='coerce');fs.append(x[['timestamp','rate']].dropna())
 if not ps or not fs:return None
 px=pd.concat(ps).drop_duplicates('timestamp').set_index('timestamp').sort_index().open
 fr=pd.concat(fs).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);fr['avg3']=fr.rate.rolling(3,min_periods=3).mean();print(s,len(px),len(fr),flush=True);return px,fr

def run(data,signal,minspread,hold):
 times=sorted(set().union(*[set(fr.timestamp) for _,fr in data.values()]));lookup={s:fr.set_index('timestamp') for s,(_,fr) in data.items()};col='rate' if signal=='LAST' else 'avg3';tr=[];nextfree=pd.Timestamp('1900-01-01',tz='UTC')
 for t in times:
  if t<nextfree:continue
  vals={}
  for s in data:
   fr=lookup[s]
   if t in fr.index:
    v=fr.at[t,col]
    if isinstance(v,pd.Series):v=v.iloc[-1]
    if np.isfinite(v):vals[s]=float(v)
  if len(vals)<6:continue
  hi=max(vals,key=vals.get);lo=min(vals,key=vals.get);spread=vals[hi]-vals[lo]
  if spread<minspread:continue
  entry=t.ceil('h')+pd.Timedelta(hours=1);exit=entry+pd.Timedelta(hours=hold);pxH=data[hi][0];pxL=data[lo][0]
  if any(x not in p.index for p in (pxH,pxL) for x in (entry,exit)):continue
  price=float(pxL.at[exit]/pxL.at[entry]-1)-float(pxH.at[exit]/pxH.at[entry]-1)
  frH=data[hi][1];frL=data[lo][1];fund=float(frH.loc[(frH.timestamp>entry)&(frH.timestamp<=exit),'rate'].sum()-frL.loc[(frL.timestamp>entry)&(frL.timestamp<=exit),'rate'].sum())
  tr.append(dict(entry=entry,exit=exit,year=entry.year,long_symbol=lo,short_symbol=hi,spread=spread,price=price,funding=fund,gross=price+fund));nextfree=exit
 return pd.DataFrame(tr)

def met(z,cost):
 if len(z)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,maxls=np.nan,price=np.nan,funding=np.nan)
 a=z.gross.to_numpy(float)-cost;pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;best=cur=0
 for v in a:
  if v<=0:cur+=1;best=max(best,cur)
  else:cur=0
 return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),maxls=best,price=float(z.price.mean()),funding=float(z.funding.mean()))

def main():
 data={s:x for s in SYMS if (x:=load(s)) is not None};allstats=[]
 for name,signal,ms,h in CONFIGS:
  t=run(data,signal,ms,h);t['config']=name;t.to_csv(OUT/f'{name}_trades.csv',index=False)
  print('\nCONFIG',name,'trades',len(t))
  print(t[['year','long_symbol','short_symbol','gross']].tail(10).to_string(index=False))
  for cost in [.002,.003,.005,.010]:
   for y in range(2021,2027):allstats.append(dict(config=name,scope='year',key=str(y),cost=cost,**met(t[t.year==y],cost)))
   # leave-one-symbol-out to expose dependence on one coin
   for s in data:
    q=t[(t.long_symbol!=s)&(t.short_symbol!=s)];allstats.append(dict(config=name,scope='LOO',key=s,cost=cost,**met(q,cost)))
   for leg in ['long_symbol','short_symbol']:
    for s,q in t.groupby(leg):allstats.append(dict(config=name,scope=leg,key=s,cost=cost,**met(q,cost)))
 r=pd.DataFrame(allstats);r.to_csv(OUT/'validation_stats.csv',index=False)
 print('\nYEAR COST STRESS');print(r[r.scope.eq('year')].to_string(index=False))
 print('\nLOO @20bps');print(r[(r.scope=='LOO')&(r.cost==.002)].sort_values(['config','ev']).to_string(index=False))
 for name,_,_,_ in CONFIGS:
  y20=r[(r.config==name)&(r.scope=='year')&(r.cost==.002)].set_index('key');y50=r[(r.config==name)&(r.scope=='year')&(r.cost==.005)].set_index('key')
  pass_early=all(y in y20.index and y20.loc[y,'ev']>0 and y20.loc[y,'pf']>1 for y in ['2021','2022'])
  pass_recent=all(y in y20.index and y20.loc[y,'ev']>0 and y20.loc[y,'pf']>1 for y in ['2024','2025','2026'])
  pass50=all(y in y50.index and y50.loc[y,'ev']>0 and y50.loc[y,'pf']>1 for y in ['2024','2025','2026'])
  print('\n',name,'EARLY_2021_22_PASS=',pass_early,'RECENT_2024_26_PASS=',pass_recent,'RECENT_50BPS_PASS=',pass50)
if __name__=='__main__':main()
