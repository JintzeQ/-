#!/usr/bin/env python3
from __future__ import annotations
import io,itertools,time,zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT']
MONTHS=['2025-02','2025-05','2025-08','2025-11','2026-02','2026-05','2026-07']
FEE=0.001; COOLDOWN=5
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore']
CACHE=Path('edge_cache'); OUT=Path('edge_output'); OUT.mkdir(exist_ok=True)
EXIT_GRID=[(0.003,0.003,5),(0.003,0.003,10),(0.004,0.003,10),(0.004,0.003,20),(0.005,0.003,10),(0.005,0.003,20),(0.005,0.004,20),(0.007,0.004,20)]
EVENTS=[(2.0,1.5),(3.0,2.0),(4.0,2.5)]

def download(s,m):
    d=CACHE/s; d.mkdir(parents=True,exist_ok=True); p=d/f'{s}-{m}.zip'
    if p.exists() and p.stat().st_size>1000:return p
    url=BASE.format(s=s,m=m)
    for k in range(4):
        try:
            r=requests.get(url,timeout=90)
            if r.status_code==404:return None
            r.raise_for_status(); p.write_bytes(r.content); return p
        except Exception:
            if k==3: raise
            time.sleep(2*(k+1))

def read_zip(p,s):
    with zipfile.ZipFile(p) as z:
        n=[x for x in z.namelist() if x.endswith('.csv')][0]
        d=pd.read_csv(io.BytesIO(z.read(n)),header=None,names=COLS,low_memory=False)
    ot=pd.to_numeric(d.open_time,errors='coerce'); d=d[ot.notna()].copy()
    for c in COLS:d[c]=pd.to_numeric(d[c],errors='coerce')
    unit='us' if float(d.open_time.median())>1e14 else 'ms'
    d['timestamp']=pd.to_datetime(d.open_time,unit=unit,utc=True); d['symbol']=s
    return d[['timestamp','symbol','open','high','low','close','quote_volume','taker_quote']].dropna()

def load_symbol(s):
    fs=[]
    for m in MONTHS:
        p=download(s,m)
        if p is not None: fs.append(read_zip(p,s))
    if not fs:return None
    d=pd.concat(fs,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    qmed=d.quote_volume.shift(1).rolling(60,min_periods=60).median(); d['vshock']=d.quote_volume/qmed.replace(0,np.nan)
    lr=np.log(d.close).diff(); rv5=np.sqrt(lr.pow(2).rolling(5,min_periods=5).mean()); rv60=np.sqrt(lr.pow(2).shift(5).rolling(60,min_periods=60).mean()); d['volshock']=rv5/rv60.replace(0,np.nan)
    buy3=d.taker_quote.rolling(3,min_periods=3).sum(); q3=d.quote_volume.rolling(3,min_periods=3).sum(); d['buyshare']=buy3/q3.replace(0,np.nan)
    d['body']=d.close/d.open-1; medbody=d.body.abs().shift(1).rolling(60,min_periods=60).median(); d['disp']=d.body.abs()/medbody.replace(0,np.nan)
    rng=(d.high-d.low).replace(0,np.nan); d['eff']=(d.close-d.open).abs()/rng
    d['upperwick']=(d.high-np.maximum(d.open,d.close))/rng; d['lowerwick']=(np.minimum(d.open,d.close)-d.low)/rng
    d['ret3']=d.close/d.close.shift(3)-1
    # compression uses only PRIOR returns, non-overlapping with baseline
    rv15p=np.sqrt(lr.pow(2).shift(1).rolling(15,min_periods=15).mean()); rv120p=np.sqrt(lr.pow(2).shift(16).rolling(120,min_periods=120).mean()); d['compression']=rv15p/rv120p.replace(0,np.nan)
    d['year']=d.timestamp.dt.year.astype(np.int16)
    print(s,len(d),d.timestamp.min(),d.timestamp.max(),flush=True)
    return d

def sig_reversal(d,evv,evvol,disp,flow,wick):
    b=d.body.to_numpy(); bs=d.buyshare.to_numpy(); di=d.disp.to_numpy(); uw=d.upperwick.to_numpy(); lw=d.lowerwick.to_numpy(); r3=np.abs(d.ret3.to_numpy())
    base=(d.vshock.to_numpy()>=evv)&(d.volshock.to_numpy()>=evvol)&(di>=disp)&(r3<=0.015)
    dr=np.zeros(len(d),np.int8)
    # fade an extreme move only after same-side aggressors show rejection wick
    dr[base&(b>0)&(bs>=flow)&(uw>=wick)]=-1
    dr[base&(b<0)&(bs<=1-flow)&(lw>=wick)]=1
    return np.flatnonzero(dr!=0),dr

def sig_compression(d,comp,vshock,disp,eff,flow):
    b=d.body.to_numpy(); bs=d.buyshare.to_numpy(); di=d.disp.to_numpy(); ef=d.eff.to_numpy(); cp=d.compression.to_numpy(); r3=np.abs(d.ret3.to_numpy())
    base=(cp<=comp)&(d.vshock.to_numpy()>=vshock)&(di>=disp)&(di<=3.5)&(ef>=eff)&(r3<=0.008)
    dr=np.zeros(len(d),np.int8)
    dr[base&(b>0)&(bs>=flow)]=1; dr[base&(b<0)&(bs<=1-flow)]=-1
    return np.flatnonzero(dr!=0),dr

def simulate(d,sig,dr,tp,sl,stop):
    op=d.open.to_numpy(); hi=d.high.to_numpy(); lo=d.low.to_numpy(); cl=d.close.to_numpy(); yr=d.year.to_numpy(); sy=d.symbol.to_numpy()
    out=[]; nxt=0
    for sidx in sig:
        e=sidx+1
        if e>=len(d) or e<nxt:continue
        side=int(dr[sidx]); entry=op[e]; end=min(e+stop-1,len(d)-1); px=cl[end]; x=end; reason='TIME'
        tpp=entry*(1+side*tp); slp=entry*(1-side*sl)
        for j in range(e,end+1):
            htp=(hi[j]>=tpp) if side==1 else (lo[j]<=tpp); hsl=(lo[j]<=slp) if side==1 else (hi[j]>=slp)
            if hsl: px=slp; x=j; reason='SL'; break
            if htp: px=tpp; x=j; reason='TP'; break
        gross=side*(px/entry-1); out.append((int(yr[e]),str(sy[e]),gross-FEE,reason))
        nxt=x+1+COOLDOWN
    return out

def metrics(rows,year):
    z=[r for r in rows if r[0]==year]; a=np.array([r[2] for r in z],float)
    if len(a)==0:return dict(n=0,win=np.nan,ev=np.nan,pf=np.nan,pos_symbols=0,maxls=np.nan)
    pos=a[a>0].sum(); neg=-a[a<0].sum(); pf=pos/neg if neg else np.inf
    best=cur=0
    for x in a:
        if x<=0:cur+=1;best=max(best,cur)
        else:cur=0
    sev={}
    for _,s,p,_ in z:sev.setdefault(s,[]).append(p)
    ps=sum(np.mean(v)>0 for v in sev.values())
    return dict(n=len(a),win=float((a>0).mean()),ev=float(a.mean()),pf=float(pf),pos_symbols=ps,maxls=best)

def main():
    data={s:load_symbol(s) for s in SYMBOLS}; data={s:d for s,d in data.items() if d is not None}
    acc={}
    # Family 1: exhaustion / rejection reversal
    for evv,evvol in EVENTS:
      for disp,flow,wick in itertools.product([1.5,2.5],[0.60,0.70],[0.15,0.30,0.45]):
        keybase=('REV',evv,evvol,disp,flow,wick,np.nan,np.nan)
        for s,d in data.items():
          sig,dr=sig_reversal(d,evv,evvol,disp,flow,wick)
          for tp,sl,stop in EXIT_GRID:
            k=keybase+(tp,sl,stop); acc.setdefault(k,[]).extend(simulate(d,sig,dr,tp,sl,stop))
    # Family 2: early breakout from prior compression
    for comp,vshock,disp,eff,flow in itertools.product([0.50,0.70,0.90],[2.0,3.0],[1.0,1.5],[0.60,0.75],[0.55,0.60]):
        keybase=('COMP',np.nan,np.nan,disp,flow,np.nan,comp,eff)
        for s,d in data.items():
          sig,dr=sig_compression(d,comp,vshock,disp,eff,flow)
          for tp,sl,stop in EXIT_GRID:
            k=keybase+(tp,sl,stop); acc.setdefault(k,[]).extend(simulate(d,sig,dr,tp,sl,stop))
    rows=[]
    for k,tr in acc.items():
      fam,evv,evvol,disp,flow,wick,comp,eff,tp,sl,stop=k
      m25=metrics(tr,2025); m26=metrics(tr,2026)
      rows.append(dict(family=fam,event_v=evv,event_vol=evvol,disp=disp,flow=flow,wick=wick,compression=comp,eff=eff,tp=tp,sl=sl,stop=stop,
                       n_2025=m25['n'],win_2025=m25['win'],ev_2025=m25['ev'],pf_2025=m25['pf'],pos_symbols_2025=m25['pos_symbols'],maxls_2025=m25['maxls'],
                       n_2026=m26['n'],win_2026=m26['win'],ev_2026=m26['ev'],pf_2026=m26['pf'],pos_symbols_2026=m26['pos_symbols'],maxls_2026=m26['maxls']))
    res=pd.DataFrame(rows); res.to_csv(OUT/'round1_all.csv',index=False)
    elig=res[(res.n_2025>=100)&(res.ev_2025>0)].sort_values(['ev_2025','win_2025'],ascending=False); elig.to_csv(OUT/'round1_train_positive.csv',index=False)
    robust=elig[(elig.n_2026>=75)&(elig.ev_2026>0)&(elig.pf_2026>1)].sort_values(['ev_2026','win_2026'],ascending=False); robust.to_csv(OUT/'round1_robust.csv',index=False)
    print('\nBEST OVERALL 2025'); print(res.sort_values('ev_2025',ascending=False).head(15).to_string(index=False))
    print('\nROBUST OOS'); print(robust.head(20).to_string(index=False) if len(robust) else 'NONE')
    print('\nBEST BY FAMILY');
    for fam in res.family.unique(): print('\n',fam); print(res[res.family==fam].sort_values('ev_2025',ascending=False).head(5).to_string(index=False))

if __name__=='__main__':main()
