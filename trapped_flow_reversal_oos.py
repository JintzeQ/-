import io, os, zipfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import numpy as np
import pandas as pd

# Strategy #4A: Trapped-Flow Reversal
# Discovered only after Strategy #3 showed sign inversion in 2026H1.
# Therefore this script uses a NEW untouched holdout only: 2026-07-01 through 2026-08-18.
# Rule is frozen from #3's strict continuation-looking state; only the trade direction is reversed.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','SEIUSDT','TIAUSDT','WIFUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
BENCH=['BTCUSDT','ETHUSDT']
WARMUP_START='2026-06-30'
HOLDOUT_START='2026-07-01'
HOLDOUT_END='2026-08-18'
DATES=pd.date_range(WARMUP_START,HOLDOUT_END,freq='D').strftime('%Y-%m-%d').tolist()
HORIZONS=[1,3,5,10,15]
SHOCK_BP=100.0
VOLX_THR=3.0
FLOW_PREV_THR=0.35
FLOW_NOW_THR=0.25
RESID1_EXTEND_BP=10.0
TAKER_FEE_SIDE_BP=5.0
OUT='trapped_flow_reversal_output'; os.makedirs(OUT,exist_ok=True)
CACHE='trapped_flow_reversal_cache'; os.makedirs(CACHE,exist_ok=True)
BASE='https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip'

def cache_path(s,d): return f'{CACHE}/{s}-1m-{d}.zip'

def download_one(s,d):
    p=cache_path(s,d)
    if os.path.exists(p) and os.path.getsize(p)>500:
        return s,d,True,'cache'
    u=BASE.format(s=s,d=d)
    for k in range(3):
        try:
            r=requests.get(u,timeout=30)
            if r.status_code==200 and len(r.content)>500:
                open(p,'wb').write(r.content)
                return s,d,True,'download'
            if r.status_code==404:
                return s,d,False,'404'
        except Exception as exc:
            if k==2:return s,d,False,str(exc)
        time.sleep(0.5*(k+1))
    return s,d,False,'failed'

def prefetch():
    jobs=[(s,d) for s in (BENCH+ALTS) for d in DATES]
    ok=0; miss=0
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut=[ex.submit(download_one,s,d) for s,d in jobs]
        for n,f in enumerate(as_completed(fut),1):
            s,d,good,msg=f.result(); ok+=int(good); miss+=int(not good)
            if n%150==0: print('download progress',n,'/',len(jobs),'ok',ok,'miss',miss,flush=True)
    print('download done ok',ok,'miss',miss,'total',len(jobs),flush=True)

def parse_day(s,d):
    p=cache_path(s,d)
    if not os.path.exists(p): return None
    try:
        raw=open(p,'rb').read(); z=zipfile.ZipFile(io.BytesIO(raw))
        names=[x for x in z.namelist() if x.endswith('.csv')]
        if not names:return None
        x=pd.read_csv(z.open(names[0]),header=None).iloc[:,:11]
        x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
        for c in ['ts','c','qv','tbq']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c','qv','tbq'])
        ts=x.ts.astype('int64')
        # Robust to ms/us timestamp archives.
        if len(ts) and float(ts.median())>1e14: ts=(ts//1000)
        x['ts']=ts.astype('int64'); x['day']=d
        return x[['ts','c','qv','tbq','day']]
    except Exception as exc:
        print('bad file',s,d,exc,flush=True); return None

def load(s):
    a=[]
    for d in DATES:
        x=parse_day(s,d)
        if x is not None and len(x):a.append(x)
    if not a:return None
    x=pd.concat(a,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['r1']=np.log(x.c/x.c.shift(1))*1e4
    x['r3']=np.log(x.c/x.c.shift(3))*1e4
    x['flow']=(2*x.tbq-x.qv)/(x.qv+1e-12)
    x['flow_prev3']=x.flow.rolling(3,min_periods=2).mean().shift(1)
    x['volbase']=x.qv.rolling(120,min_periods=60).median().shift(1)
    x['volx']=x.qv/(x.volbase+1e-12)
    return x

prefetch()
print('loading BTC/ETH benchmarks',flush=True)
b=load('BTCUSDT'); e=load('ETHUSDT')
if b is None or e is None: raise RuntimeError('missing benchmark data')
bench=b[['ts','r1','r3']].rename(columns={'r1':'btc1','r3':'btc3'}).merge(
      e[['ts','r1','r3']].rename(columns={'r1':'eth1','r3':'eth3'}),on='ts',how='inner')
start_ms=int(pd.Timestamp(HOLDOUT_START,tz='UTC').timestamp()*1000)
end_ms=int((pd.Timestamp(HOLDOUT_END,tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)

events=[]
for k,s in enumerate(ALTS,1):
    print('symbol',k,len(ALTS),s,flush=True)
    x=load(s)
    if x is None:continue
    x=x.merge(bench,on='ts',how='left')
    x['resid1']=x.r1-0.5*x.btc1-0.5*x.eth1
    x['resid3']=x.r3-0.5*x.btc3-0.5*x.eth3
    for h in HORIZONS:x[f'f{h}']=np.log(x.c.shift(-h)/x.c)*1e4
    shock_sign=np.sign(x.resid3)
    cond=(x.resid3.abs()>=SHOCK_BP)&(x.volx>=VOLX_THR)&((shock_sign*x.flow_prev3)>=FLOW_PREV_THR)&((shock_sign*x.flow)>=FLOW_NOW_THR)&((shock_sign*x.resid1)>=RESID1_EXTEND_BP)&(x.ts>=start_ms)&(x.ts<end_ms)
    idx=np.flatnonzero(cond.fillna(False).to_numpy())
    # Freeze same anti-overlap rule as #3: one signal per symbol per 5 minutes.
    keep=[]; last=-10**9
    for i in idx:
        if i-last>=5: keep.append(i); last=i
    for i in keep:
        ss=1 if x.resid3.iloc[i]>0 else -1
        trade_side=-ss  # reverse the continuation-looking shock
        d={'symbol':s,'ts':int(x.ts.iloc[i]),'day':x.day.iloc[i],'shock_sign':ss,'trade_side':trade_side,
           'resid1':float(x.resid1.iloc[i]),'resid3':float(x.resid3.iloc[i]),'volx':float(x.volx.iloc[i]),
           'flow_prev3':float(x.flow_prev3.iloc[i]),'flow_now':float(x.flow.iloc[i])}
        for h in HORIZONS:d[f'r{h}_bp']=trade_side*float(x[f'f{h}'].iloc[i])
        events.append(d)
E=pd.DataFrame(events)
if E.empty: raise RuntimeError('no #4 holdout events')
E['datetime']=pd.to_datetime(E.ts,unit='ms',utc=True)
E=E.sort_values(['ts','symbol']).reset_index(drop=True)
E.to_csv(f'{OUT}/events.csv',index=False)

summary={'holdout_start':HOLDOUT_START,'holdout_end':HOLDOUT_END,'events':len(E),'symbols':E.symbol.nunique(),'days':E.day.nunique(),
         'long_events':int((E.trade_side==1).sum()),'short_events':int((E.trade_side==-1).sum())}
for h in HORIZONS:
    v=E[f'r{h}_bp'].dropna();summary[f'mean{h}']=float(v.mean());summary[f'median{h}']=float(v.median());summary[f'win{h}']=float((v>0).mean());summary[f'p10_{h}']=float(v.quantile(.1));summary[f'p90_{h}']=float(v.quantile(.9))
summary['fee_only_net5']=summary['mean5']-2*TAKER_FEE_SIDE_BP
summary['gate_pass']=bool(summary['mean5']>=20 and summary['median5']>10 and summary['events']>=100 and summary['symbols']>=10)
pd.DataFrame([summary]).to_csv(f'{OUT}/summary.csv',index=False)

bys=E.groupby('trade_side').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index()
bys.to_csv(f'{OUT}/by_side.csv',index=False)
bysym=E.groupby('symbol').agg(events=('symbol','size'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index().sort_values('events',ascending=False)
bysym.to_csv(f'{OUT}/by_symbol.csv',index=False)
byday=E.groupby('day').agg(events=('symbol','size'),mean5=('r5_bp','mean'),median5=('r5_bp','median')).reset_index().sort_values('day')
byday.to_csv(f'{OUT}/by_day.csv',index=False)
summary['top_symbol_share']=float(bysym.events.max()/len(E))
summary['top_day_share']=float(byday.events.max()/len(E))
pd.DataFrame([summary]).to_csv(f'{OUT}/summary.csv',index=False)

lines=['# Strategy #4A Trapped-Flow Reversal — untouched holdout','',
       f'Holdout: {HOLDOUT_START} through {HOLDOUT_END} UTC; daily Binance USD-M 1m klines.',
       f'Universe: {len(ALTS)} alts; BTC/ETH only as residual benchmarks.',
       'Frozen rule from #3 discovery: |3m residual|>=100bp, quote volume>=3x trailing 120m median, prior 3m flow aligned >=0.35, current 1m flow aligned >=0.25, latest 1m residual still extends >=10bp; trade in the OPPOSITE direction.',
       'Primary horizon fixed at 5m before this holdout. Events de-overlapped 5m/symbol.','',
       f"Events: {summary['events']} across {summary['symbols']} symbols and {summary['days']} UTC days.",
       f"5m gross mean {summary['mean5']:.3f}bp; median {summary['median5']:.3f}bp; win {summary['win5']:.3%}; p10 {summary['p10_5']:.3f}bp; p90 {summary['p90_5']:.3f}bp.",
       f"Fee-only taker/taker proxy: {summary['fee_only_net5']:.3f}bp/event before spread/slippage/impact.",
       f"Long events {summary['long_events']}; short events {summary['short_events']}; top-symbol share {summary['top_symbol_share']:.2%}; top-day share {summary['top_day_share']:.2%}.",
       '', '## Side diagnostic','',bys.to_markdown(index=False,floatfmt='.3f'),'',
       f"Verdict: **{'PASS_TO_TICK' if summary['gate_pass'] else 'REJECT'}** (predeclared gate: mean5>=20bp, median5>10bp, >=100 events, >=10 symbols)."]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
