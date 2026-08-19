import os, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #5B: long-only validation derived from #5A long-side diagnostic.
# IMPORTANT: the actual #5A long side is price DOWN + OI DOWN (same sign), then LONG reversal.
# No 2024 data are used here. Two predeclared independent blocks are evaluated separately.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
BLOCKS={
 'HIST_A_2023H2':('2023-06-01','2024-01-01'),
 'HOLDOUT_B_2025':('2025-01-01','2026-01-01'),
}
OI_THR=.08; PRICE_THR=.02; VOLX_THR=2.0; DEOVERLAP_MS=60*60*1000
PRIMARY_H=30; HORIZONS=[(15,3),(30,6),(60,12)]
# Predeclared per-block gate.
MIN_EVENTS=150; MIN_SYMBOLS=10; MIN_MEAN=25.0; MIN_MEDIAN=10.0; MIN_ROBUST=0.0
MIN_POS_MONTH_FRAC=.60; MAX_TOP_SYMBOL_SHARE=.20; MIN_FEEONLY_MEAN=10.0; ROUNDTRIP_FEE_BP=10.0
OUT='crowding_unwind_output'; CACHE='crowding_unwind_cache'; os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)
KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
MBASE='https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip'
UA={'User-Agent':'Mozilla/5.0'}

def months_between(a,b):
    return pd.period_range(pd.Timestamp(a).to_period('M'),(pd.Timestamp(b)-pd.Timedelta(days=1)).to_period('M'),freq='M').astype(str).tolist()

def download(url,path,min_size=100):
    if os.path.exists(path) and os.path.getsize(path)>=min_size:return True,'cache'
    msg='failed'
    for k in range(4):
        try:
            r=requests.get(url,headers=UA,timeout=45)
            if r.status_code==200 and len(r.content)>=min_size:
                with open(path,'wb') as f:f.write(r.content)
                return True,'download'
            if r.status_code==404:return False,'404'
            msg=f'http{r.status_code}'
        except Exception as e:msg=repr(e)
        time.sleep(.5*(k+1))
    return False,msg

def kpath(s,m):return f'{CACHE}/k-{s}-{m}.zip'
def mpath(s,d):return f'{CACHE}/m-{s}-{d}.zip'

ALL_MONTHS=sorted(set(sum([months_between(a,b) for a,b in BLOCKS.values()],[])))

def prefetch_klines():
    jobs=[(s,m) for s in ALTS for m in ALL_MONTHS]; miss=[]
    def one(sm):
        s,m=sm; ok,msg=download(KBASE.format(s=s,m=m),kpath(s,m),500); return s,m,ok,msg
    with ThreadPoolExecutor(max_workers=24) as ex:
        fs=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fs),1):
            s,m,ok,msg=f.result()
            if not ok:miss.append((s,m,msg))
            if n%100==0:print('kline',n,'/',len(jobs),'missing',len(miss),flush=True)
    pd.DataFrame(miss,columns=['symbol','month','reason']).to_csv(f'{OUT}/kline_missing.csv',index=False)
    print('kline coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)

def read_kzip(s,m):
    p=kpath(s,m)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),header=None)
        x=x.iloc[:,:12]; x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq','ignore']
        for c in ['ts','c','qv']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c','qv'])
        if len(x) and x.ts.median()>1e14:x['ts']=np.floor(x.ts/1000.0)
        x['ts']=x.ts.astype('int64')
        return x[['ts','c','qv']]
    except Exception as e:
        print('BAD_KLINE',s,m,repr(e),flush=True); return None

def load_block_kline(s,a,b):
    parts=[]
    for m in months_between(a,b):
        x=read_kzip(s,m)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    d=pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    lo=int(pd.Timestamp(a,tz='UTC').timestamp()*1000); hi=int(pd.Timestamp(b,tz='UTC').timestamp()*1000)
    d=d[(d.ts>=lo)&(d.ts<hi)].reset_index(drop=True)
    if len(d)<300:return None
    d['r1h']=np.log(d.c/d.c.shift(12)); d['volmed']=d.qv.rolling(288,min_periods=144).median().shift(1); d['volx']=d.qv/(d.volmed+1e-12)
    return d

def candidate_rows(k):
    # #5B long-only = negative price shock; OI condition checked after metrics join.
    return k[(k.r1h<=-PRICE_THR)&(k.volx>=VOLX_THR)].copy()

def needed_metric_days(s,k):
    out=set()
    for ts in candidate_rows(k).ts.astype('int64'):
        for t in (ts,ts-60*60*1000):out.add((s,pd.to_datetime(t,unit='ms',utc=True).strftime('%Y-%m-%d')))
    return out

def prefetch_metrics(req):
    req=sorted(req); miss=[]
    def one(sd):
        s,d=sd; ok,msg=download(MBASE.format(s=s,d=d),mpath(s,d),200); return s,d,ok,msg
    with ThreadPoolExecutor(max_workers=36) as ex:
        fs=[ex.submit(one,j) for j in req]
        for n,f in enumerate(as_completed(fs),1):
            s,d,ok,msg=f.result()
            if not ok:miss.append((s,d,msg))
            if n%500==0:print('metrics',n,'/',len(req),'missing',len(miss),flush=True)
    pd.DataFrame(miss,columns=['symbol','day','reason']).to_csv(f'{OUT}/metrics_missing.csv',index=False)
    print('metrics coverage',len(req)-len(miss),'/',len(req),'missing',len(miss),flush=True)

def read_metric_day(s,d):
    p=mpath(s,d)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name))
        if 'create_time' not in x.columns or 'sum_open_interest_value' not in x.columns:return None
        dt=pd.to_datetime(x.create_time,utc=True,errors='coerce')
        x['ts']=dt.to_numpy(dtype='datetime64[ms]').astype('int64')
        x['oi']=pd.to_numeric(x.sum_open_interest_value,errors='coerce')
        return x[['ts','oi']].dropna().astype({'ts':'int64'}).drop_duplicates('ts').sort_values('ts')
    except Exception as e:
        print('BAD_METRIC',s,d,repr(e),flush=True); return None

def load_metrics(s,days):
    parts=[]
    for d in sorted(days):
        x=read_metric_day(s,d)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    return pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)

def nearest_oi(m,targets):
    if m is None or not len(m):return np.full(len(targets),np.nan)
    a=pd.DataFrame({'target':np.asarray(targets,dtype='int64')}); a['_ord']=np.arange(len(a)); a=a.sort_values('target')
    b=m.rename(columns={'ts':'mts'}).sort_values('mts')
    z=pd.merge_asof(a,b,left_on='target',right_on='mts',direction='nearest',tolerance=60000)
    return z.sort_values('_ord').oi.to_numpy()

def build_events(block,s,k,days):
    c=candidate_rows(k).copy().sort_values('ts').reset_index()
    if c.empty:return pd.DataFrame()
    m=load_metrics(s,days); now=nearest_oi(m,c.ts.values); prev=nearest_oi(m,c.ts.values-3600000)
    c['oi1h']=now/(prev+1e-12)-1
    # Preserve actual #5A long diagnostic: price <0 and OI <0 with |OI|>=8%.
    sig=c[np.isfinite(c.oi1h)&(c.oi1h<=-OI_THR)].sort_values('ts')
    keep=[]; last=-10**18
    for r in sig.itertuples():
        if int(r.ts)-last>=DEOVERLAP_MS:keep.append(r.Index); last=int(r.ts)
    out=[]
    for ci in keep:
        r=c.loc[ci]; i=int(r['index'])
        if i+12>=len(k):continue
        row={'block':block,'symbol':s,'ts':int(r.ts),'side':1,'oi1h':float(r.oi1h),'r1h':float(r.r1h),'volx':float(r.volx)}
        for h,n in HORIZONS:row[f'gross_{h}m_bp']=np.log(float(k.c.iloc[i+n])/float(k.c.iloc[i]))*1e4
        out.append(row)
    return pd.DataFrame(out)

def robust(x):
    x=np.sort(np.asarray(x,float)); cut=max(1,int(np.ceil(len(x)*.05))); return float(np.mean(x[:-cut])) if len(x)>cut else np.nan

prefetch_klines()
K={}; req=set()
for block,(a,b) in BLOCKS.items():
    K[block]={}
    for s in ALTS:
        k=load_block_kline(s,a,b); K[block][s]=k
        if k is not None:req |= needed_metric_days(s,k)
print('metric days required',len(req),flush=True)
prefetch_metrics(req)

all_events=[]
for block,(a,b) in BLOCKS.items():
    for n,s in enumerate(ALTS,1):
        k=K[block].get(s)
        if k is None:continue
        days={d for ss,d in req if ss==s and a<=d<b}
        e=build_events(block,s,k,days)
        print(block,n,'/',len(ALTS),s,'events',len(e),flush=True)
        if len(e):all_events.append(e)
if not all_events:raise RuntimeError('no #5B events')
e=pd.concat(all_events,ignore_index=True).sort_values(['block','ts']); e.to_csv(f'{OUT}/events.csv',index=False)

summary=[]; month_rows=[]; symbol_rows=[]
for block in BLOCKS:
    z=e[e.block==block].copy()
    if z.empty:continue
    z['month']=pd.to_datetime(z.ts,unit='ms',utc=True).dt.strftime('%Y-%m')
    m=z.groupby('month').gross_30m_bp.agg(['size','mean','median']).reset_index(); m['block']=block; month_rows.append(m)
    s=z.groupby('symbol').gross_30m_bp.agg(['size','mean','median']).reset_index(); s['block']=block; symbol_rows.append(s)
    x=z.gross_30m_bp; posfrac=float((m['mean']>=0).mean()); topshare=float(s['size'].max()/len(z)); feeonly=float(x.mean()-ROUNDTRIP_FEE_BP)
    row={'block':block,'events':len(z),'symbols':z.symbol.nunique(),'mean30_bp':x.mean(),'median30_bp':x.median(),'win30':(x>0).mean(),'p10_bp':x.quantile(.1),'p90_bp':x.quantile(.9),'remove_best5_mean_bp':robust(x),'positive_month_frac':posfrac,'top_symbol_share':topshare,'feeonly_mean_bp':feeonly}
    row['pass']=bool(row['events']>=MIN_EVENTS and row['symbols']>=MIN_SYMBOLS and row['mean30_bp']>=MIN_MEAN and row['median30_bp']>MIN_MEDIAN and row['remove_best5_mean_bp']>MIN_ROBUST and row['positive_month_frac']>=MIN_POS_MONTH_FRAC and row['top_symbol_share']<=MAX_TOP_SYMBOL_SHARE and row['feeonly_mean_bp']>=MIN_FEEONLY_MEAN)
    summary.append(row)
sumdf=pd.DataFrame(summary); sumdf.to_csv(f'{OUT}/summary.csv',index=False)
pd.concat(month_rows,ignore_index=True).to_csv(f'{OUT}/month.csv',index=False); pd.concat(symbol_rows,ignore_index=True).to_csv(f'{OUT}/symbol.csv',index=False)
overall=bool(len(sumdf)==len(BLOCKS) and sumdf['pass'].all())
print('\n# Strategy #5B Long-Only Dual Holdout\n'); print(sumdf.to_markdown(index=False,floatfmt='.3f')); print('\nOVERALL:', 'PASS_TO_TICK' if overall else 'REJECT')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_TICK' if overall else 'REJECT')+'\n')