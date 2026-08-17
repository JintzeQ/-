#!/usr/bin/env python3
from __future__ import annotations
import io,zipfile
from pathlib import Path
import numpy as np,pandas as pd,requests
SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT','LINKUSDT','DOTUSDT'];MONTHS=[str(p) for p in pd.period_range('2021-01','2026-07',freq='M')]
PERP='https://data.binance.vision/data/futures/um/monthly/klines/{s}/8h/{s}-8h-{m}.zip';SPOT='https://data.binance.vision/data/spot/monthly/klines/{s}/8h/{s}-8h-{m}.zip';FUND='https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip';COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'];CACHE=Path('r27_cache');OUT=Path('r27_output');OUT.mkdir(exist_ok=True)
COST=.002;NET_TP=.001;MAXD=90

def dl(url,p):
 if p.exists() and p.stat().st_size>200:return p
 p.parent.mkdir(parents=True,exist_ok=True)
 for _ in range(3):
  try:
   r=requests.get(url,timeout=90)
   if r.status_code==404:return None
   r.raise_for_status();p.write_bytes(r.content);return p
  except Exception: pass
 return None

def kline(s,m,kind):
 p=dl((PERP if kind=='p' else SPOT).format(s=s,m=m),CACHE/kind/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
  ot=pd.to_numeric(d.open_time,errors='coerce');d=d[ot.notna()].copy();d['open_time']=pd.to_numeric(d.open_time,errors='coerce');d['open']=pd.to_numeric(d.open,errors='coerce');unit='us' if float(d.open_time.median())>1e14 else 'ms';d['ts']=pd.to_datetime(d.open_time,unit=unit,utc=True).dt.floor('8h');return d[['ts','open']].dropna().drop_duplicates('ts')
 except Exception:return None

def funding(s,m):
 p=dl(FUND.format(s=s,m=m),CACHE/'f'/s/f'{m}.zip')
 if p is None:return None
 try:
  with zipfile.ZipFile(p) as z:n=[x for x in z.namelist() if x.endswith('.csv')][0];d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=['calc_time','funding_interval_hours','rate'],low_memory=False)
  ct=pd.to_numeric(d.calc_time,errors='coerce');d=d[ct.notna()].copy();d['calc_time']=pd.to_numeric(d.calc_time,errors='coerce');d['rate']=pd.to_numeric(d.rate,errors='coerce');d['ts']=pd.to_datetime(d.calc_time,unit='ms',utc=True);return d[['ts','rate']].dropna().sort_values('ts')
 except Exception:return None

def load(s):
 ps=[];ss=[];fs=[]
 for m in MONTHS:
  a=kline(s,m,'p');b=kline(s,m,'s');c=funding(s,m)
  if a is not None:ps.append(a)
  if b is not None:ss.append(b)
  if c is not None:fs.append(c)
 if not ps or not ss or not fs:return None
 p=pd.concat(ps).drop_duplicates('ts').sort_values('ts').rename(columns={'open':'po'});q=pd.concat(ss).drop_duplicates('ts').sort_values('ts').rename(columns={'open':'so'});f=pd.concat(fs).sort_values('ts').drop_duplicates(['ts','rate']);d=p.merge(q,on='ts').sort_values('ts').reset_index(drop=True)
 # exact-event cumulative funding; value at a bar includes events strictly after previous bars and <= current timestamp.
 fts=f.ts.view('int64').to_numpy();fr=f.rate.astype(float).to_numpy();fc=np.r_[0,np.cumsum(fr)];bts=d.ts.view('int64').to_numpy();ix=np.searchsorted(fts,bts,side='right');d['fund_cum']=fc[ix];d['symbol']=s;return d

def run(d):
 ts=d.ts.to_numpy();po=d.po.to_numpy();so=d.so.to_numpy();fc=d.fund_cum.to_numpy();out=[];i=0
 while i<len(d)-2:
  t=pd.Timestamp(ts[i])
  # only initiate at first 00:00 UTC bar of a calendar month, and only when flat
  if not (t.day==1 and t.hour==0):i+=1;continue
  e=i;entry_t=t;pe=po[e];se=so[e];f0=fc[e];x=None;reason='max90'
  for j in range(e+1,len(d)):
   tj=pd.Timestamp(ts[j]);days=(tj-entry_t).total_seconds()/86400
   if days>MAXD:break
   gross=(so[j]/se-1)-(po[j]/pe-1)+(fc[j]-f0);net=gross-COST
   if net>=NET_TP:
    x=j;reason='tp';break
   if days>=MAXD:
    x=j;break
  if x is None:
   # nearest bar not beyond 90d
   cand=np.where((pd.to_datetime(ts,utc=True)-entry_t)<=pd.Timedelta(days=MAXD))[0];cand=cand[cand>e]
   if not len(cand):break
   x=int(cand[-1])
  gross=(so[x]/se-1)-(po[x]/pe-1)+(fc[x]-f0);net=float(gross-COST);out.append(dict(symbol=d.symbol.iloc[e],entry=entry_t,exit=pd.Timestamp(ts[x]),year=entry_t.year,net=net,gross=float(gross),basis=float((so[x]/se-1)-(po[x]/pe-1)),funding=float(fc[x]-f0),hold_days=(pd.Timestamp(ts[x])-entry_t).total_seconds()/86400,reason=reason))
  i=x+1
 return out

def met(g):
 if len(g)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,median_hold=np.nan,tp_rate=np.nan,pos_symbols=0)
 a=g.net.to_numpy();pf=a[a>0].sum()/(-a[a<0].sum()) if np.any(a<0) else np.inf;by=g.groupby('symbol').net.mean();return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),median_hold=float(g.hold_days.median()),tp_rate=float((g.reason=='tp').mean()),pos_symbols=int((by>0).sum()))
def main():
 tr=[]
 for s in SYMBOLS:
  d=load(s)
  if d is None:continue
  z=run(d);tr+=z;print(s,len(z),flush=True)
 t=pd.DataFrame(tr);t.to_csv(OUT/'r27_trades.csv',index=False);rows=[]
 for y in range(2021,2027):rows.append(dict(year=y,**met(t[t.year==y])))
 rows.append(dict(year='ALL',**met(t)));r=pd.DataFrame(rows);r.to_csv(OUT/'r27_year.csv',index=False);print(r.to_string(index=False));print('\nSYMBOL');ss=[]
 for s,g in t.groupby('symbol'):ss.append(dict(symbol=s,**met(g),years_pos=int((g.groupby('year').net.mean()>0).sum())))
 print(pd.DataFrame(ss).sort_values('ev',ascending=False).to_string(index=False))
if __name__=='__main__':main()
