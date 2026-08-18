#!/usr/bin/env python3
from __future__ import annotations
import argparse, io, json, math, time, zipfile
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import requests

BASE_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
COLS = ["agg_trade_id","price","quantity","first_trade_id","last_trade_id","transact_time","is_buyer_maker"]

def daterange(start: str, end: str):
    d=date.fromisoformat(start); endd=date.fromisoformat(end)
    while d<=endd:
        yield d.isoformat(); d += timedelta(days=1)

def norm_bool(s):
    if s.dtype==bool: return s
    return s.astype(str).str.lower().isin(["true","1","t"])

def download(symbol, day, cache):
    cache.mkdir(parents=True, exist_ok=True)
    p=cache/f"{symbol}-aggTrades-{day}.zip"
    if p.exists() and p.stat().st_size>1000: return p
    url=BASE_URL.format(symbol=symbol,day=day)
    for k in range(4):
        try:
            print("download",url,flush=True)
            r=requests.get(url,timeout=120); r.raise_for_status(); p.write_bytes(r.content); return p
        except Exception:
            if k==3: raise
            time.sleep(2*(k+1))

def aggregate_day(symbol, day, cache, bin_ms=500, chunksize=1_000_000):
    p=download(symbol,day,cache); parts=[]
    with zipfile.ZipFile(p) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(names)!=1: raise RuntimeError(f"Expected one CSV in {p}, got {names}")
        raw=z.open(names[0]); first=raw.readline().decode('utf-8',errors='ignore').strip(); raw.seek(0)
        has_header=not first.split(',')[0].strip().lstrip('-').isdigit()
        reader=pd.read_csv(raw,names=None if has_header else COLS,header=0 if has_header else None,chunksize=chunksize,low_memory=False)
        for chunk in reader:
            if has_header:
                ren={}
                for c in chunk.columns:
                    lc=str(c).strip().lower()
                    if lc in {'agg_trade_id','aggtradeid','a'}: ren[c]='agg_trade_id'
                    elif lc in {'price','p'}: ren[c]='price'
                    elif lc in {'quantity','qty','q'}: ren[c]='quantity'
                    elif lc in {'first_trade_id','firsttradeid','f'}: ren[c]='first_trade_id'
                    elif lc in {'last_trade_id','lasttradeid','l'}: ren[c]='last_trade_id'
                    elif lc in {'transact_time','timestamp','time','t'}: ren[c]='transact_time'
                    elif lc in {'is_buyer_maker','isbuyermaker','m'}: ren[c]='is_buyer_maker'
                chunk=chunk.rename(columns=ren)
            missing=[c for c in COLS if c not in chunk.columns]
            if missing: raise RuntimeError(f"Missing columns {missing}; got {list(chunk.columns)}")
            px=pd.to_numeric(chunk.price,errors='coerce'); qty=pd.to_numeric(chunk.quantity,errors='coerce'); ts=pd.to_numeric(chunk.transact_time,errors='coerce'); bm=norm_bool(chunk.is_buyer_maker)
            ok=px.notna()&qty.notna()&ts.notna(); px=px[ok].astype(float); qty=qty[ok].astype(float); ts=ts[ok].astype('int64'); bm=bm[ok]
            if ts.median()>1e14: ts=ts//1000
            sign=np.where(bm.to_numpy(),-1.0,1.0); b=(ts.to_numpy()//bin_ms)*bin_ms
            a=pd.DataFrame({'bin_ms':b,'price':px.to_numpy(),'signed_notional':px.to_numpy()*qty.to_numpy()*sign,'total_notional':px.to_numpy()*qty.to_numpy(),'signed_count':sign,'trade_count':1.0})
            parts.append(a.groupby('bin_ms',sort=False).agg(last_price=('price','last'),signed_notional=('signed_notional','sum'),total_notional=('total_notional','sum'),signed_count=('signed_count','sum'),trade_count=('trade_count','sum')))
    d=pd.concat(parts)
    return d.groupby(level=0).agg(last_price=('last_price','last'),signed_notional=('signed_notional','sum'),total_notional=('total_notional','sum'),signed_count=('signed_count','sum'),trade_count=('trade_count','sum')).sort_index()

def grid(df,bin_ms):
    idx=np.arange(int(df.index.min()),int(df.index.max())+bin_ms,bin_ms,dtype=np.int64); x=df.reindex(idx); x.last_price=x.last_price.ffill()
    for c in ['signed_notional','total_notional','signed_count','trade_count']: x[c]=x[c].fillna(0.0)
    return x

def features(x,bin_ms):
    x=x.copy(); bps=int(round(1000/bin_ms)); w1,w5,w30=bps,5*bps,30*bps; eps=1e-12
    x['rbar']=np.log(x.last_price).diff(); x['ret5_bps']=np.log(x.last_price/x.last_price.shift(w5))*1e4
    for name,w in [('1s',w1),('5s',w5)]:
        sn=x.signed_notional.rolling(w,min_periods=w).sum(); tn=x.total_notional.rolling(w,min_periods=w).sum(); sc=x.signed_count.rolling(w,min_periods=w).sum(); tc=x.trade_count.rolling(w,min_periods=w).sum()
        x[f'fi_{name}']=sn/(tn+eps); x[f'count_imb_{name}']=sc/(tc+eps); x[f'notional_{name}']=tn
    x['vol30_bps']=x.rbar.rolling(w30,min_periods=w30).std()*1e4; s=np.sign(x.fi_1s); x['same_sign_3']=s.eq(s.shift(1))&s.eq(s.shift(2))&s.ne(0)
    return x

def thresholds(train):
    t=train.dropna(subset=['fi_5s','fi_1s','ret5_bps','notional_5s']); q95=float(t.fi_5s.abs().quantile(.95)); q75=float(t.fi_1s.abs().quantile(.75)); med=float(t.notional_5s.quantile(.5))
    cand=t[(t.fi_5s.abs()>=q95)&(t.notional_5s>=med)]; low=float((cand if len(cand)>=100 else t).ret5_bps.abs().quantile(.25))
    return {'abs_fi5_q95':q95,'abs_fi1_q75':q75,'notional5_median':med,'abs_ret5_lowimpact_q25':low}

def signals(x,th):
    s=pd.DataFrame(index=x.index); d5=np.sign(x.fi_5s).fillna(0); high=x.fi_5s.abs()>=th['abs_fi5_q95']; s['flow_cont']=np.where(high,d5,0)
    absorb=high&(x.notional_5s>=th['notional5_median'])&(x.ret5_bps.abs()<=th['abs_ret5_lowimpact_q25']); s['absorption_rev']=np.where(absorb,-d5,0)
    d1=np.sign(x.fi_1s).fillna(0); pers=x.same_sign_3.fillna(False)&(x.fi_1s.abs()>=th['abs_fi1_q75'])&(x.fi_1s.shift(1).abs()>=th['abs_fi1_q75'])&(x.fi_1s.shift(2).abs()>=th['abs_fi1_q75']); s['flow_persistence']=np.where(pers,d1,0)
    return s

def events(price,sig,hb,latency=1):
    p=price.to_numpy(float); s=sig.to_numpy(float); out=[]; i=0
    while i<len(p):
        if s[i]==0 or not np.isfinite(s[i]): i+=1; continue
        ent=i+latency; ex=ent+hb
        if ex>=len(p): break
        out.append((i,int(np.sign(s[i])),s[i]*math.log(p[ex]/p[ent])*1e4)); i=ex+1
    return out

def summarize(name,ev,costs=(0.,.5,1.,2.,5.)):
    g=np.array([z[2] for z in ev],float); rows=[]
    if not len(g):
        return [dict(strategy=name,per_side_cost_bps=c,n=0,mean_gross_bps=np.nan,mean_net_bps=np.nan,win_rate=np.nan,total_net_bps=np.nan,t_stat_gross=np.nan,breakeven_per_side_bps=np.nan) for c in costs]
    sd=g.std(ddof=1) if len(g)>1 else np.nan; t=g.mean()/(sd/math.sqrt(len(g))) if np.isfinite(sd) and sd>0 else np.nan
    for c in costs:
        net=g-2*c; rows.append(dict(strategy=name,per_side_cost_bps=c,n=len(g),mean_gross_bps=g.mean(),median_gross_bps=np.median(g),mean_net_bps=net.mean(),win_rate=(net>0).mean(),total_net_bps=net.sum(),t_stat_gross=t,breakeven_per_side_bps=g.mean()/2))
    return rows

def footprint(x,th,bin_ms):
    f=x.fi_1s.dropna(); s0=np.sign(f); s1=np.sign(f.shift(-1)); strong=f.abs()>=th['abs_fi1_q75']; mask=strong&s0.ne(0)&s1.ne(0); allm=s0.ne(0)&s1.ne(0)
    rows=[{'metric':'strong_flow_next_same_sign_prob','value':float((s0[mask]==s1[mask]).mean())},{'metric':'all_flow_next_same_sign_prob','value':float((s0[allm]==s1[allm]).mean())}]
    for sec in [.5,1,2.5,5,10]: rows.append({'metric':f'fi1_autocorr_{sec:g}s','value':float(f.autocorr(max(1,int(round(sec*1000/bin_ms)))) )})
    return pd.DataFrame(rows)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--symbol',default='BTCUSDT'); ap.add_argument('--start',default='2026-08-10'); ap.add_argument('--end',default='2026-08-16'); ap.add_argument('--train-end',default='2026-08-13'); ap.add_argument('--bin-ms',type=int,default=500); ap.add_argument('--out',default='microstructure_output'); a=ap.parse_args(); cache=Path('cache/microstructure')
    frames=[]
    for day in daterange(a.start,a.end): d=aggregate_day(a.symbol,day,cache,a.bin_ms); frames.append(d); print(day,len(d),flush=True)
    x=features(grid(pd.concat(frames).sort_index(),a.bin_ms),a.bin_ms); x['day']=pd.Series(pd.to_datetime(x.index,unit='ms',utc=True).date,index=x.index).astype(str); train=x[x.day<=a.train_end]; test=x[x.day>a.train_end]; th=thresholds(train); sig=signals(test,th)
    out=Path(a.out); out.mkdir(exist_ok=True); (out/'thresholds.json').write_text(json.dumps(th,indent=2))
    bps=int(round(1000/a.bin_ms)); metrics=[]; erows=[]
    for h in [1,5,30]:
        for strat in sig.columns:
            ev=events(test.last_price,sig[strat],h*bps,1); metrics += summarize(f'{strat}_{h}s',ev)
            for idx,direction,gross in ev: erows.append({'strategy':strat,'horizon_s':h,'row':idx,'direction':direction,'gross_bps':gross})
    m=pd.DataFrame(metrics); m.to_csv(out/'metrics.csv',index=False); e=pd.DataFrame(erows)
    if len(e):
        tidx=test.index.to_numpy(); e['timestamp_ms']=e.row.map(lambda i:int(tidx[int(i)])); e['day']=pd.to_datetime(e.timestamp_ms,unit='ms',utc=True).dt.strftime('%Y-%m-%d'); e.to_csv(out/'events.csv',index=False)
        e.groupby(['strategy','horizon_s','day']).agg(n=('gross_bps','size'),mean_gross_bps=('gross_bps','mean'),win_rate_gross=('gross_bps',lambda z:float((z>0).mean()))).reset_index().to_csv(out/'daily_oos.csv',index=False)
    fp=footprint(test,th,a.bin_ms); fp.to_csv(out/'footprint.csv',index=False)
    zero=m[m.per_side_cost_bps==0].sort_values('mean_gross_bps',ascending=False); one=m[m.per_side_cost_bps==1].sort_values('mean_net_bps',ascending=False)
    lines=['# BTCUSDT microstructure pilot','',f'- Data: {a.start}..{a.end}; train through {a.train_end}; remaining days OOS',f'- Grid: {a.bin_ms} ms; entry latency proxy: {a.bin_ms} ms','- Price proxy: last trade price; no historical L2 queue reconstruction.','','## OOS gross','', '| strategy | n | mean gross bp | t-stat | break-even cost/side bp |','|---|---:|---:|---:|---:|']
    for _,r in zero.iterrows(): lines.append(f'| {r.strategy} | {int(r.n)} | {r.mean_gross_bps:.4f} | {r.t_stat_gross:.2f} | {r.breakeven_per_side_bps:.4f} |')
    lines += ['','## OOS at 1 bp/side','','| strategy | n | mean net bp | win rate | total net bp |','|---|---:|---:|---:|---:|']
    for _,r in one.iterrows(): lines.append(f'| {r.strategy} | {int(r.n)} | {r.mean_net_bps:.4f} | {r.win_rate:.3f} | {r.total_net_bps:.1f} |')
    lines += ['','## Flow footprint','']
    for _,r in fp.iterrows(): lines.append(f'- {r.metric}: {r.value:.6f}')
    lines += ['','Positive gross edge is not deployable unless break-even per-side cost exceeds realistic all-in execution cost. Footprint persistence does not identify a specific bot.']
    (out/'summary.md').write_text('\n'.join(lines)); print('\n'.join(lines),flush=True)
if __name__=='__main__': main()
