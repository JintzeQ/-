#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2020-01','2026-07',freq='M')]
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{m}.zip'
F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('vmf_cache');OUT=Path('vmf_output');OUT.mkdir(exist_ok=True);L=100;SLOPE=10

def get(url,p):
 if p.exists() and p.stat().st_size>100:return p
 p.parent.mkdir(parents=True,exist_ok=True);r=requests.get(url,timeout=60)
 if r.status_code==404:return None
 r.raise_for_status();p.write_bytes(r.content);return p

def load(s):
 ks=[];fs=[]
 for m in MONTHS:
  p=get(K.format(s=s,m=m),CACHE/'px'/s/f'{m}.zip')
  if p:
   with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COL,low_memory=False)
   ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d.open_time=pd.to_numeric(d.open_time,errors='coerce');d.open=pd.to_numeric(d.open,errors='coerce');d.close=pd.to_numeric(d.close,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True);ks.append(d[['timestamp','open','close']])
  q=get(F.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
  if q:
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)))
   x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True);x['rate']=pd.to_numeric(x.last_funding_rate,errors='coerce');fs.append(x[['timestamp','rate']].dropna())
 d=pd.concat(ks,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True);d['symbol']=s
 fr=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True) if fs else pd.DataFrame(columns=['timestamp','rate'])
 print(s,len(d),len(fr),flush=True);return d,fr

def make_trades(s,d,fr):
 sma=d.close.shift(1).rolling(L,min_periods=L).mean();slope=sma/sma.shift(SLOPE)-1;long=(d.close>sma)&(slope>=0);short=(d.close<sma)&(slope<=0)
 op=d.open.to_numpy();cl=d.close.to_numpy();ts=d.timestamp.to_numpy();out=[];side=0;entry=0.;et=None;ei=None
 def close_trade(exit_px,exit_t):
  gross=side*(exit_px/entry-1);mask=(fr.timestamp>et)&(fr.timestamp<=exit_t);fundsum=float(fr.loc[mask,'rate'].sum());fund_pnl=-side*fundsum
  return dict(symbol=s,entry_time=et,exit_time=exit_t,side=side,gross_price=gross,funding_pnl=fund_pnl,funding_sum=fundsum,hold_days=(exit_t-et).total_seconds()/86400)
 for i in range(L+SLOPE,len(d)-1):
  desired=1 if bool(long.iloc[i]) else (-1 if bool(short.iloc[i]) else 0)
  if desired==side:continue
  e=i+1
  if (pd.Timestamp(ts[e])-pd.Timestamp(ts[i])).days!=1:continue
  px=float(op[e]);xt=pd.Timestamp(ts[e])
  if side!=0:out.append(close_trade(px,xt))
  side=desired
  if side!=0:entry=px;et=xt;ei=e
  else:entry=0.;et=None;ei=None
 if side!=0 and et is not None:out.append(close_trade(float(cl[-1]),pd.Timestamp(ts[-1])))
 return pd.DataFrame(out)

def metrics(z,fee):
 if len(z)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,price_ev=np.nan,funding_ev=np.nan,maxls=np.nan)
 a=z.gross_price.to_numpy(float)+z.funding_pnl.to_numpy(float)-fee;pos=a[a>0].sum();neg=-a[a<0].sum();pf=pos/neg if neg else np.inf;best=cur=0
 for v in a:
  if v<=0:cur+=1;best=max(best,cur)
  else:cur=0
 return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),price_ev=float(z.gross_price.mean()),funding_ev=float(z.funding_pnl.mean()),maxls=best)

def main():
 pieces=[]
 for s in SYMS:
  d,fr=load(s);t=make_trades(s,d,fr)
  if len(t):pieces.append(t)
 t=pd.concat(pieces,ignore_index=True).sort_values('entry_time').reset_index(drop=True);t['year']=t.entry_time.dt.year;t.to_csv(OUT/'trades_with_funding.csv',index=False)
 rows=[]
 for fee in [.001,.002,.003,.005]:
  for y in range(2020,2027):rows.append(dict(scope='year',key=str(y),fee=fee,**metrics(t[t.year==y],fee)))
  for sym,q in t.groupby('symbol'):rows.append(dict(scope='symbol',key=sym,fee=fee,**metrics(q,fee)))
  for side,q in t.groupby('side'):rows.append(dict(scope='side',key='LONG' if side==1 else 'SHORT',fee=fee,**metrics(q,fee)))
 r=pd.DataFrame(rows);r.to_csv(OUT/'stats_with_funding.csv',index=False);print('YEAR');print(r[r.scope.eq('year')].to_string(index=False));print('\nSIDE 10BPS');print(r[(r.scope=='side')&(r.fee==.001)].to_string(index=False));print('\nSYMBOL 10BPS');print(r[(r.scope=='symbol')&(r.fee==.001)].sort_values('ev',ascending=False).to_string(index=False))
 y10=r[(r.scope=='year')&(r.fee==.001)].set_index('key');y30=r[(r.scope=='year')&(r.fee==.003)].set_index('key');print('\nPASS_10BPS_2020_2024_2025_2026',all(y10.loc[y,'ev']>0 and y10.loc[y,'pf']>1 for y in ['2020','2024','2025','2026'] if y in y10.index));print('PASS_30BPS_2024_2025_2026',all(y30.loc[y,'ev']>0 and y30.loc[y,'pf']>1 for y in ['2024','2025','2026'] if y in y30.index))
if __name__=='__main__':main()
