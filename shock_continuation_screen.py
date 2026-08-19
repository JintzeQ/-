import io, zipfile, requests, os
import numpy as np
import pandas as pd

# Strategy #3: Shock Continuation / Delayed Momentum
# Primary decision horizon is fixed at 5 minutes before seeing OOS.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','SEIUSDT','TIAUSDT','WIFUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
MONTHS=pd.period_range('2025-01','2026-06',freq='M').astype(str).tolist()
HORIZONS=[1,2,3,5,10,15]
SHOCKS=[50,75,100]
VOLS=[2.0,3.0]
OUT='shock_continuation_output'; os.makedirs(OUT,exist_ok=True)
CACHE='shock_continuation_cache'; os.makedirs(CACHE,exist_ok=True)
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'

def get_zip(s,m):
    p=f'{CACHE}/{s}-1m-{m}.zip'
    if os.path.exists(p) and os.path.getsize(p)>1000:
        return open(p,'rb').read()
    u=BASE.format(s=s,m=m)
    r=requests.get(u,timeout=60)
    if r.status_code!=200:return None
    open(p,'wb').write(r.content)
    return r.content

def dl(s,m):
    raw=get_zip(s,m)
    if raw is None:return None
    try:
        z=zipfile.ZipFile(io.BytesIO(raw)); f=[x for x in z.namelist() if x.endswith('.csv')][0]
        x=pd.read_csv(z.open(f),header=None).iloc[:,:11]
        x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
        for c in ['ts','c','qv','tbq']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c','qv','tbq'])
        x['ts']=x.ts.astype('int64'); x['month']=m
        return x[['ts','c','qv','tbq','month']]
    except Exception as exc:
        print('bad',s,m,exc,flush=True); return None

def load(s):
    a=[]
    for m in MONTHS:
        x=dl(s,m)
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

print('loading benchmark BTC/ETH',flush=True)
b=load('BTCUSDT'); e=load('ETHUSDT')
if b is None or e is None:raise RuntimeError('missing BTC/ETH benchmark')
bench=b[['ts','r1','r3']].rename(columns={'r1':'btc1','r3':'btc3'}).merge(
    e[['ts','r1','r3']].rename(columns={'r1':'eth1','r3':'eth3'}),on='ts',how='inner')

events=[]
for k,s in enumerate(ALTS,1):
    print('symbol',k,len(ALTS),s,flush=True)
    x=load(s)
    if x is None:continue
    x=x.merge(bench,on='ts',how='left')
    x['resid1']=x.r1-0.5*x.btc1-0.5*x.eth1
    x['resid3']=x.r3-0.5*x.btc3-0.5*x.eth3
    for h in HORIZONS:x[f'f{h}']=np.log(x.c.shift(-h)/x.c)*1e4
    for shock in SHOCKS:
      for vx in VOLS:
        absr=x.resid3.abs()
        sign=np.sign(x.resid3)
        # Continuation state: shock is idiosyncratic, volume is abnormal, prior and current
        # aggressor flow remain aligned, and the latest 1m residual is still extending.
        cond=(absr>=shock)&(x.volx>=vx)&((sign*x.flow_prev3)>=0.35)&((sign*x.flow)>=0.25)&((sign*x.resid1)>=10)
        idx=np.flatnonzero(cond.fillna(False).to_numpy())
        keep=[]; last=-10**9
        for i in idx:
            if i-last>=5:keep.append(i);last=i
        for i in keep:
            sd=1 if x.resid3.iloc[i]>0 else -1
            d={'symbol':s,'ts':int(x.ts.iloc[i]),'month':x.month.iloc[i],'shock_bp':shock,'volx_thr':vx,'side':sd,
               'resid1':float(x.resid1.iloc[i]),'resid3':float(x.resid3.iloc[i]),'volx':float(x.volx.iloc[i]),
               'flow_prev3':float(x.flow_prev3.iloc[i]),'flow_now':float(x.flow.iloc[i])}
            for h in HORIZONS:d[f'r{h}_bp']=sd*float(x[f'f{h}'].iloc[i])
            events.append(d)
E=pd.DataFrame(events)
if E.empty:raise RuntimeError('no continuation events')
E['date']=pd.to_datetime(E.ts,unit='ms',utc=True)
E['split']=np.where(E.date<'2025-07-01','train',np.where(E.date<'2026-01-01','validation','oos'))
E.to_csv(f'{OUT}/events.csv',index=False)

rows=[]
for (shock,vx,split),g in E.groupby(['shock_bp','volx_thr','split']):
    d={'shock_bp':shock,'volx_thr':vx,'split':split,'events':len(g),'symbols':g.symbol.nunique(),'months':g.month.nunique()}
    for h in HORIZONS:
        v=g[f'r{h}_bp'].dropna(); d[f'mean{h}']=v.mean();d[f'median{h}']=v.median();d[f'win{h}']=(v>0).mean();d[f'p10_{h}']=v.quantile(.1)
    rows.append(d)
R=pd.DataFrame(rows);R.to_csv(f'{OUT}/summary.csv',index=False)
# Primary target fixed at 5m; threshold selected only on train with minimum breadth/count.
tr=R[(R.split=='train')&(R.events>=100)&(R.symbols>=10)].sort_values('mean5',ascending=False)
choice=None if tr.empty else tr.iloc[0]
lines=['# Strategy #3 Shock Continuation / Delayed Momentum','',
       f'Universe: {len(ALTS)} alt USD-M perpetuals + BTC/ETH benchmarks; {MONTHS[0]}..{MONTHS[-1]}.',
       'Residual = alt return - 0.5 BTC - 0.5 ETH.',
       'Continuation requires |3m residual| threshold, quote-volume shock, prior/current aggressor-flow persistence in shock direction, and latest 1m residual still extending >=10bp.',
       'Events de-overlapped 5m/symbol. Train=2025H1, Validation=2025H2, frozen OOS=2026H1.',
       'Primary horizon fixed at 5m. This is gross-alpha discovery, before spread/slippage/impact.','']
if choice is None:
    lines.append('No train cell met >=100 events and >=10 symbols.')
else:
    sh=float(choice.shock_bp);vx=float(choice.volx_thr)
    chosen=R[(R.shock_bp==sh)&(R.volx_thr==vx)].copy()
    lines += [f'Train-selected threshold: shock={sh:.0f}bp, volume>={vx:.1f}x','',chosen.to_markdown(index=False,floatfmt='.3f')]
    ce=E[(E.shock_bp==sh)&(E.volx_thr==vx)].copy()
    byside=ce.groupby(['split','side']).agg(events=('symbol','size'),symbols=('symbol','nunique'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index()
    byside.to_csv(f'{OUT}/chosen_by_side.csv',index=False)
    o=chosen[chosen.split=='oos']
    if len(o):
        z=o.iloc[0]
        verdict='PASS_TO_TICK' if (z.mean5>=20 and z.median5>10 and z.events>=100 and z.symbols>=10) else 'REJECT_OR_REDESIGN'
        lines += ['',f'Verdict: **{verdict}** (gate: OOS mean5>=20bp, median5>10bp, >=100 events, >=10 symbols).',
                  f'Optimistic taker/taker fee-only proxy at 5m: {z.mean5-10:.3f}bp/event before spread/slippage/impact.']
open(f'{OUT}/summary.md','w').write('\n'.join(lines))
print('\n'.join(lines),flush=True)
