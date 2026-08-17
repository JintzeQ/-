#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=[str(p) for p in pd.period_range('2020-01','2026-07',freq='M')]
K='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{m}.zip';F='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip'
COL=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('vdp_cache');OUT=Path('vdp_output');OUT.mkdir(exist_ok=True)
ROUNDTRIP=.001;HALF=ROUNDTRIP/2;L=100;SLOPE=10

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
   ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy()
   for c in ['open_time','open','close']:d[c]=pd.to_numeric(d[c],errors='coerce')
   unit='us' if float(d.open_time.median())>1e14 else 'ms';d['date']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('D');ks.append(d[['date','open','close']])
  q=get(F.format(s=s,m=m),CACHE/'fund'/s/f'{m}.zip')
  if q:
   with zipfile.ZipFile(q) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];x=pd.read_csv(io.BytesIO(z.read(n)))
   x['timestamp']=pd.to_datetime(pd.to_numeric(x.calc_time),unit='ms',utc=True);x['rate']=pd.to_numeric(x.last_funding_rate,errors='coerce');x=x.dropna(subset=['timestamp','rate']);x['date']=x.timestamp.dt.floor('D');fs.append(x[['date','rate']])
 if not ks:return None
 d=pd.concat(ks,ignore_index=True).drop_duplicates('date').sort_values('date').set_index('date');fund=pd.concat(fs,ignore_index=True).groupby('date').rate.sum() if fs else pd.Series(dtype=float)
 d['funding']=fund.reindex(d.index).fillna(0.0)
 sma=d.close.shift(1).rolling(L,min_periods=L).mean();slope=sma/sma.shift(SLOPE)-1
 # signal observed at current close; executable position starts at next day's open
 signal=((d.close>sma)&(slope>=0)).astype(float)
 d['pos']=signal.shift(1).fillna(0.0)
 # next open/open return for position held from today's open to next day's open
 d['next_open']=d.open.shift(-1);d['asset_ret']=d.next_open/d.open-1
 # funding for this calendar day paid by long position; approximation aligned with day held
 d['held_funding']=-d.funding
 print(s,len(d),d.index.min(),d.index.max(),flush=True);return d[['open','asset_ret','held_funding','pos']]

def build_panel(data):
 dates=sorted(set().union(*[set(d.index) for d in data.values()]));idx=pd.DatetimeIndex(dates);ret=pd.DataFrame(index=idx,columns=SYMS,dtype=float);fund=pd.DataFrame(index=idx,columns=SYMS,dtype=float);pos=pd.DataFrame(index=idx,columns=SYMS,dtype=float)
 for s,d in data.items():
  ret[s]=d.asset_ret.reindex(idx);fund[s]=d.held_funding.reindex(idx).fillna(0);pos[s]=d.pos.reindex(idx).fillna(0)
 return ret,fund,pos

def simulate(ret,fund,pos,mode,gross):
 # target weights determined at today's open from signal known yesterday.
 if mode=='FIXED':
  target=pos*(gross/len(SYMS))
 else:
  n=pos.sum(axis=1).replace(0,np.nan);target=pos.div(n,axis=0).fillna(0)*gross
 # no trading before a symbol has valid return data; missing return => zero target.
 target=target.where(ret.notna(),0.0)
 prev=target.shift(1).fillna(0)
 turnover=(target-prev).abs().sum(axis=1)
 fee=HALF*turnover # entering and exiting each pay 5bps; round trip 10bps
 price=(target*ret.fillna(0)).sum(axis=1)
 fpnl=(target*fund.fillna(0)).sum(axis=1)
 port=price+fpnl-fee
 # ignore last date with no forward returns across all names
 valid=ret.notna().any(axis=1);port=port[valid];target=target.loc[port.index]
 return pd.DataFrame({'ret':port,'price':price.loc[port.index],'funding':fpnl.loc[port.index],'fee':fee.loc[port.index],'gross_actual':target.sum(axis=1),'active':(target>0).sum(axis=1)})

def stats(x):
 r=x.ret.to_numpy(float);eq=np.cumprod(1+r);peak=np.maximum.accumulate(eq);dd=eq/peak-1
 years=max((x.index[-1]-x.index[0]).days/365.25,1/365);cagr=eq[-1]**(1/years)-1 if eq[-1]>0 else -1
 annvol=np.std(r,ddof=1)*np.sqrt(365) if len(r)>1 else np.nan;sh=(np.mean(r)*365/annvol) if annvol and annvol>0 else np.nan
 return dict(days=len(r),total_multiple=float(eq[-1]),cagr=float(cagr),annvol=float(annvol),sharpe=float(sh),maxdd=float(dd.min()),daily_win=float((r>0).mean()),avg_daily=float(r.mean()),avg_gross=float(x.gross_actual.mean()),avg_active=float(x.active.mean()),funding_ann=float(x.funding.mean()*365),fee_ann=float(x.fee.mean()*365))

def main():
 data={s:d for s in SYMS if (d:=load(s)) is not None};ret,fund,pos=build_panel(data);rows=[];curves=[]
 for mode in ['FIXED','ACTIVE_EQUAL']:
  for gross in [.5,1.0,1.5,2.0,3.0]:
   x=simulate(ret,fund,pos,mode,gross);x['mode']=mode;x['target_gross']=gross;x['equity']=(1+x.ret).cumprod();curves.append(x.reset_index(names='date'))
   for scope,y in [('ALL',None),(2020,2020),(2021,2021),(2022,2022),(2023,2023),(2024,2024),(2025,2025),(2026,2026)]:
    q=x if y is None else x[x.index.year==y]
    if len(q):rows.append(dict(mode=mode,target_gross=gross,scope=str(scope),**stats(q)))
 r=pd.DataFrame(rows);r.to_csv(OUT/'portfolio_stats.csv',index=False);pd.concat(curves,ignore_index=True).to_csv(OUT/'portfolio_curves.csv',index=False)
 print('ALL');print(r[r.scope.eq('ALL')].to_string(index=False));print('\nYEARS @1X');print(r[(r.target_gross==1)&(r.scope!='ALL')].to_string(index=False));print('\n2024-26 LEVERAGE');print(r[(r.scope.isin(['2024','2025','2026']))].to_string(index=False))
if __name__=='__main__':main()
