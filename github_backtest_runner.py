#!/usr/bin/env python3
from __future__ import annotations
import io, itertools, time, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT"]
MONTHS = [str(p) for p in pd.period_range("2025-01","2026-07",freq="M")]
FEE = 0.001
COOLDOWN = 5
MAX_ABS_RET3 = 0.01
EVENTS = [(2.0,1.5),(3.0,2.0),(4.0,2.5)]
TP_GRID = [0.002,0.003,0.004,0.005]
STOP_GRID = [3,5,10,15]
BASE = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip"
COLS=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
CACHE=Path("cache")
OUT=Path("output"); OUT.mkdir(exist_ok=True)

def download(s,m):
    d=CACHE/s; d.mkdir(parents=True,exist_ok=True)
    p=d/f"{s}-1m-{m}.zip"
    if p.exists() and p.stat().st_size>1000: return p
    url=BASE.format(s=s,m=m)
    for k in range(4):
        try:
            r=requests.get(url,timeout=90); r.raise_for_status(); p.write_bytes(r.content); return p
        except Exception:
            if k==3: raise
            time.sleep(2*(k+1))

def read_zip(p,s):
    with zipfile.ZipFile(p) as z:
        name=[n for n in z.namelist() if n.endswith('.csv')][0]
        df=pd.read_csv(io.BytesIO(z.read(name)),header=None,names=COLS,low_memory=False)
    ot=pd.to_numeric(df.open_time,errors='coerce'); df=df[ot.notna()].copy()
    for c in COLS: df[c]=pd.to_numeric(df[c],errors='coerce')
    med=float(df.open_time.median()); unit='us' if med>1e14 else 'ms'
    df['timestamp']=pd.to_datetime(df.open_time,unit=unit,utc=True)
    df['symbol']=s
    return df[['timestamp','symbol','open','high','low','close','quote_volume','taker_quote']].dropna()

def load_symbol(s):
    frames=[]
    for i,m in enumerate(MONTHS,1):
        print(f"[{s}] {m} {i}/{len(MONTHS)}",flush=True)
        frames.append(read_zip(download(s,m),s))
    d=pd.concat(frames,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    qmed=d.quote_volume.shift(1).rolling(60,min_periods=60).median()
    d['vshock']=d.quote_volume/qmed.replace(0,np.nan)
    lr=np.log(d.close).diff()
    rv5=np.sqrt(lr.pow(2).rolling(5,min_periods=5).mean())
    rv60=np.sqrt(lr.pow(2).shift(5).rolling(60,min_periods=60).mean())
    d['volshock']=rv5/rv60.replace(0,np.nan)
    buy3=d.taker_quote.rolling(3,min_periods=3).sum(); q3=d.quote_volume.rolling(3,min_periods=3).sum()
    d['buyshare']=buy3/q3.replace(0,np.nan)
    d['body']=d.close/d.open-1
    medbody=d.body.abs().shift(1).rolling(60,min_periods=60).median()
    d['disp']=d.body.abs()/medbody.replace(0,np.nan)
    rng=(d.high-d.low).replace(0,np.nan)
    d['eff']=(d.close-d.open).abs()/rng
    d['ret3']=d.close/d.close.shift(3)-1
    d['year']=d.timestamp.dt.year.astype(np.int16)
    print(f"[{s}] rows={len(d):,} {d.timestamp.iloc[0]} -> {d.timestamp.iloc[-1]}",flush=True)
    return d

def signal_variants(d,event_v,event_vol):
    event=(d.vshock.to_numpy()>=event_v)&(d.volshock.to_numpy()>=event_vol)&(np.abs(d.ret3.to_numpy())<=MAX_ABS_RET3)
    bs=d.buyshare.to_numpy(); body=d.body.to_numpy(); disp=d.disp.to_numpy(); eff=d.eff.to_numpy(); pdir=np.sign(body).astype(np.int8)
    for f in (0.60,0.65,0.70):
        dr=np.zeros(len(d),np.int8); dr[bs>=f]=1; dr[bs<=1-f]=-1
        yield ('A_flow',f,np.nan,np.nan, np.flatnonzero(event&(dr!=0)),dr)
    for x in (1.5,2.0,2.5):
        valid=event&(disp>=x)&(pdir!=0)
        yield ('B_disp',np.nan,x,np.nan,np.flatnonzero(valid),pdir)
    for x,e in itertools.product((1.5,2.0,2.5),(0.60,0.70,0.80)):
        valid=event&(disp>=x)&(eff>=e)&(pdir!=0)
        yield ('C_disp_eff',np.nan,x,e,np.flatnonzero(valid),pdir)
    for x,e,f in itertools.product((1.5,2.0,2.5),(0.60,0.70,0.80),(0.55,0.60,0.65)):
        valid=event&(disp>=x)&(eff>=e)&(((pdir==1)&(bs>=f))|((pdir==-1)&(bs<=1-f)))
        yield ('D_disp_eff_flow',f,x,e,np.flatnonzero(valid),pdir)

def simulate(d,sig,dr,tp,stop):
    op=d.open.to_numpy(); hi=d.high.to_numpy(); lo=d.low.to_numpy(); cl=d.close.to_numpy(); years=d.year.to_numpy()
    pnl_by_year={2025:[],2026:[]}; reasons={2025:[0,0,0],2026:[0,0,0]}
    next_allowed=0
    for sidx in sig:
        eidx=sidx+1
        if eidx>=len(d) or eidx<next_allowed: continue
        side=int(dr[sidx]); entry=op[eidx]
        if side==0 or not np.isfinite(entry) or entry<=0: continue
        end=min(eidx+stop-1,len(d)-1); px=cl[end]; reason=2; xidx=end
        if side==1: tpp=entry*(1+tp); slp=entry*(1-tp)
        else: tpp=entry*(1-tp); slp=entry*(1+tp)
        for j in range(eidx,end+1):
            if side==1: htp=hi[j]>=tpp; hsl=lo[j]<=slp
            else: htp=lo[j]<=tpp; hsl=hi[j]>=slp
            if hsl:
                px=slp; reason=1; xidx=j; break
            if htp:
                px=tpp; reason=0; xidx=j; break
        net=side*(px/entry-1)-FEE
        y=int(years[eidx])
        if y in pnl_by_year:
            pnl_by_year[y].append(net); reasons[y][reason]+=1
        next_allowed=xidx+1+COOLDOWN
    return pnl_by_year,reasons

def summarize(vals,reasons,prefix):
    a=np.asarray(vals,dtype=float)
    if len(a)==0:
        return {f'n_{prefix}':0,f'win_{prefix}':np.nan,f'ev_{prefix}':np.nan,f'pf_{prefix}':np.nan,f'maxls_{prefix}':np.nan,f'tp_rate_{prefix}':np.nan}
    pos=a[a>0].sum(); neg=-a[a<0].sum(); pf=pos/neg if neg>0 else np.inf
    best=cur=0
    for x in a:
        if x<=0: cur+=1; best=max(best,cur)
        else: cur=0
    return {f'n_{prefix}':len(a),f'win_{prefix}':float((a>0).mean()),f'ev_{prefix}':float(a.mean()),f'pf_{prefix}':float(pf),f'maxls_{prefix}':best,f'tp_rate_{prefix}':reasons[0]/len(a)}

def main():
    acc={}
    for sym in SYMBOLS:
        d=load_symbol(sym)
        for evv,evvol in EVENTS:
            for model,flow,disp,eff,sig,dr in signal_variants(d,evv,evvol):
                print(f"[{sym}] {evv}/{evvol} {model} flow={flow} disp={disp} eff={eff} signals={len(sig)}",flush=True)
                for tp,stop in itertools.product(TP_GRID,STOP_GRID):
                    key=(evv,evvol,model,flow,disp,eff,tp,stop)
                    acc.setdefault(key,{2025:([],[0,0,0]),2026:([],[0,0,0])})
                    py,rs=simulate(d,sig,dr,tp,stop)
                    for y in (2025,2026):
                        acc[key][y][0].extend(py[y])
                        acc[key][y][1][0]+=rs[y][0]; acc[key][y][1][1]+=rs[y][1]; acc[key][y][1][2]+=rs[y][2]
        del d
    rows=[]
    for key,v in acc.items():
        evv,evvol,model,flow,disp,eff,tp,stop=key
        row={'event_vshock':evv,'event_volshock':evvol,'model':model,'flow':flow,'disp':disp,'eff':eff,'tp_sl':tp,'time_stop':stop}
        row.update(summarize(*v[2025],'2025')); row.update(summarize(*v[2026],'2026'))
        rows.append(row)
    res=pd.DataFrame(rows)
    res.to_csv(OUT/'all_results.csv',index=False)
    elig=res[(res.n_2025>=50)&(res.ev_2025>0)].copy()
    elig=elig.sort_values(['win_2025','ev_2025','n_2025'],ascending=[False,False,False])
    elig.to_csv(OUT/'train_positive_ranked.csv',index=False)
    robust=elig[(elig.n_2026>=30)&(elig.ev_2026>0)].copy().sort_values(['win_2026','ev_2026'],ascending=[False,False])
    robust.to_csv(OUT/'oos_positive.csv',index=False)
    best=[]
    for m in res.model.unique():
        x=elig[elig.model==m]
        if len(x): best.append(x.iloc[0])
    pd.DataFrame(best).to_csv(OUT/'best_by_model.csv',index=False)
    print("\n=== BEST BY MODEL (chosen on 2025 only) ===")
    if best:
        b=pd.DataFrame(best)
        print(b[['model','event_vshock','event_volshock','flow','disp','eff','tp_sl','time_stop','n_2025','win_2025','ev_2025','pf_2025','n_2026','win_2026','ev_2026','pf_2026']].to_string(index=False))
    print("\n=== TOP ROBUST POSITIVE ===")
    if len(robust): print(robust.head(20).to_string(index=False))
    else: print('NONE')

if __name__=='__main__': main()
