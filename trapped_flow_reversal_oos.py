import io, os, zipfile
import requests
import numpy as np
import pandas as pd

# Strategy #4B historical blind holdout.
# This entire block was chosen BEFORE looking at any #4 performance on it.
# It fills the unused gap after #1's March-2024 tests and before #2/#3's 2025+ research.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','SEIUSDT','TIAUSDT','WIFUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
BENCH=['BTCUSDT','ETHUSDT']
MONTHS=pd.period_range('2024-04','2024-12',freq='M').astype(str).tolist()
SHOCK_BP=100.0
VOLX_THR=3.0
FLOW_PREV_THR=0.35
FLOW_NOW_THR=0.25
RESID1_EXTEND_BP=10.0
HORIZON=5
TAKER_FEE_SIDE_BP=5.0
OUT='trapped_flow_reversal_output'; os.makedirs(OUT,exist_ok=True)
CACHE='trapped_flow_reversal_hist_cache'; os.makedirs(CACHE,exist_ok=True)
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'

def get_zip(s,m):
    p=f'{CACHE}/{s}-1m-{m}.zip'
    if os.path.exists(p) and os.path.getsize(p)>1000:
        return open(p,'rb').read(), 'cache'
    r=requests.get(BASE.format(s=s,m=m),timeout=60)
    if r.status_code!=200 or len(r.content)<=1000:
        return None, f'http_{r.status_code}'
    open(p,'wb').write(r.content)
    return r.content, 'download'

def dl(s,m):
    raw,status=get_zip(s,m)
    if raw is None:return None,status
    try:
        z=zipfile.ZipFile(io.BytesIO(raw)); names=[x for x in z.namelist() if x.endswith('.csv')]
        if not names:return None,'no_csv'
        x=pd.read_csv(z.open(names[0]),header=None).iloc[:,:11]
        x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
        for c in ['ts','c','qv','tbq']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c','qv','tbq'])
        ts=x.ts.astype('int64')
        if len(ts) and float(ts.median())>1e14:ts=ts//1000
        x['ts']=ts.astype('int64');x['month']=m
        return x[['ts','c','qv','tbq','month']],'ok'
    except Exception as exc:return None,f'bad:{exc}'

coverage=[]
def load(s):
    a=[]
    for m in MONTHS:
        x,status=dl(s,m);coverage.append({'symbol':s,'month':m,'status':status,'rows':0 if x is None else len(x)})
        if x is not None and len(x):a.append(x)
    if not a:return None
    x=pd.concat(a,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['r1']=np.log(x.c/x.c.shift(1))*1e4
    x['r3']=np.log(x.c/x.c.shift(3))*1e4
    x['flow']=(2*x.tbq-x.qv)/(x.qv+1e-12)
    x['flow_prev3']=x.flow.rolling(3,min_periods=2).mean().shift(1)
    x['volbase']=x.qv.rolling(120,min_periods=60).median().shift(1)
    x['volx']=x.qv/(x.volbase+1e-12)
    x['f5']=np.log(x.c.shift(-HORIZON)/x.c)*1e4
    return x

print('Historical blind block:',MONTHS[0],'through',MONTHS[-1],flush=True)
print('Frozen rule: 100bp shock / 3x volume / persistent aligned flow / reverse / 5m',flush=True)
print('loading BTC/ETH',flush=True)
b=load('BTCUSDT');e=load('ETHUSDT')
if b is None or e is None:raise RuntimeError('missing BTC/ETH')
bench=b[['ts','r1','r3']].rename(columns={'r1':'btc1','r3':'btc3'}).merge(e[['ts','r1','r3']].rename(columns={'r1':'eth1','r3':'eth3'}),on='ts',how='inner')

events=[]
for k,s in enumerate(ALTS,1):
    print('symbol',k,len(ALTS),s,flush=True)
    x=load(s)
    if x is None:continue
    x=x.merge(bench,on='ts',how='left')
    x['resid1']=x.r1-0.5*x.btc1-0.5*x.eth1
    x['resid3']=x.r3-0.5*x.btc3-0.5*x.eth3
    ss=np.sign(x.resid3)
    cond=(x.resid3.abs()>=SHOCK_BP)&(x.volx>=VOLX_THR)&((ss*x.flow_prev3)>=FLOW_PREV_THR)&((ss*x.flow)>=FLOW_NOW_THR)&((ss*x.resid1)>=RESID1_EXTEND_BP)
    idx=np.flatnonzero(cond.fillna(False).to_numpy())
    keep=[];last=-10**9
    for i in idx:
        if i-last>=5:keep.append(i);last=i
    for i in keep:
        shock_side=1 if x.resid3.iloc[i]>0 else -1
        trade_side=-shock_side
        events.append({'symbol':s,'ts':int(x.ts.iloc[i]),'month':x.month.iloc[i],'shock_side':shock_side,'trade_side':trade_side,
                       'resid1':float(x.resid1.iloc[i]),'resid3':float(x.resid3.iloc[i]),'volx':float(x.volx.iloc[i]),
                       'flow_prev3':float(x.flow_prev3.iloc[i]),'flow_now':float(x.flow.iloc[i]),
                       'r5_bp':trade_side*float(x.f5.iloc[i])})

C=pd.DataFrame(coverage);C.to_csv(f'{OUT}/coverage.csv',index=False)
missing=C[C.status!='ok']
print('coverage files',len(C),'missing_or_bad',len(missing),flush=True)
if len(missing):print(missing.to_string(index=False),flush=True)
E=pd.DataFrame(events)
if E.empty:raise RuntimeError('no events')
E['datetime']=pd.to_datetime(E.ts,unit='ms',utc=True);E.to_csv(f'{OUT}/events.csv',index=False)
v=E.r5_bp.dropna()
summary={'period_start':'2024-04-01','period_end':'2024-12-31','events':len(v),'symbols':E.loc[v.index,'symbol'].nunique(),'months':E.loc[v.index,'month'].nunique(),
         'mean5':float(v.mean()),'median5':float(v.median()),'win5':float((v>0).mean()),'p10_5':float(v.quantile(.1)),'p90_5':float(v.quantile(.9)),
         'fee_only_net5':float(v.mean()-2*TAKER_FEE_SIDE_BP),'coverage_missing_files':int(len(missing))}
summary['gate_pass']=bool(summary['mean5']>=20 and summary['median5']>10 and summary['events']>=100 and summary['symbols']>=10)
byside=E.groupby('trade_side').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index()
bymonth=E.groupby('month').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index()
bysymbol=E.groupby('symbol').agg(events=('symbol','size'),mean5=('r5_bp','mean'),median5=('r5_bp','median'),win5=('r5_bp',lambda z:(z>0).mean())).reset_index().sort_values('events',ascending=False)
byside.to_csv(f'{OUT}/by_side.csv',index=False);bymonth.to_csv(f'{OUT}/by_month.csv',index=False);bysymbol.to_csv(f'{OUT}/by_symbol.csv',index=False)
summary['top_symbol_share']=float(bysymbol.events.max()/len(E));summary['top_month_share']=float(bymonth.events.max()/len(E))
pd.DataFrame([summary]).to_csv(f'{OUT}/summary.csv',index=False)
lines=['# Strategy #4 Historical Blind Holdout','',
       'Predeclared untouched block: 2024-04-01 through 2024-12-31 UTC. No month selection after observing results.',
       'Frozen #4 rule unchanged: |3m residual|>=100bp, volume>=3x trailing 120m median, prior flow aligned>=0.35, current flow aligned>=0.25, latest 1m residual extension>=10bp, then reverse shock. Primary horizon=5m.',
       f"Coverage missing/bad monthly files: {summary['coverage_missing_files']}.",
       f"Events {summary['events']} / symbols {summary['symbols']} / months {summary['months']}.",
       f"5m gross mean {summary['mean5']:.3f}bp; median {summary['median5']:.3f}bp; win {summary['win5']:.2%}; p10 {summary['p10_5']:.3f}; p90 {summary['p90_5']:.3f}.",
       f"Fee-only taker/taker proxy {summary['fee_only_net5']:.3f}bp/event before spread/slippage/impact.",
       f"Top-symbol share {summary['top_symbol_share']:.2%}; top-month share {summary['top_month_share']:.2%}.",
       '', '## Side diagnostic','',byside.to_markdown(index=False,floatfmt='.3f'),'',
       '## Month diagnostic','',bymonth.to_markdown(index=False,floatfmt='.3f'),'',
       f"Verdict: **{'PASS_TO_TICK' if summary['gate_pass'] and summary['coverage_missing_files']==0 else 'REJECT_OR_HOLD'}** (gate mean5>=20bp, median5>10bp, >=100 events, >=10 symbols; complete coverage preferred)."]
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines),flush=True)
