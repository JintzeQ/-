import io, os, time, zipfile, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #5 frozen research specification. This revision changes DATA PLUMBING ONLY.
# Alpha rule, dates, universe, thresholds, horizons, de-overlap and gate are unchanged.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
START=pd.Timestamp('2024-01-01',tz='UTC'); END=pd.Timestamp('2025-01-01',tz='UTC')
MONTHS=pd.period_range('2024-01','2024-12',freq='M').astype(str).tolist()
OUT='crowding_unwind_output'; CACHE='crowding_unwind_cache'; os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)
KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
MBASE='https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip'
UA={'User-Agent':'Mozilla/5.0'}

# Frozen thresholds / horizon / gate.
OI_THR=.08; PRICE_THR=.02; VOLX_THR=2.0; DEOVERLAP_MS=60*60*1000
HORIZONS=[(15,3),(30,6),(60,12)]

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

def prefetch_klines():
    jobs=[(s,m) for s in ALTS for m in MONTHS]; miss=[]
    def one(sm):
        s,m=sm; ok,msg=download(KBASE.format(s=s,m=m),kpath(s,m),500); return s,m,ok,msg
    with ThreadPoolExecutor(max_workers=20) as ex:
        fs=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fs),1):
            s,m,ok,msg=f.result()
            if not ok:miss.append((s,m,msg))
            if n%50==0:print('kline archives',n,'/',len(jobs),'missing',len(miss),flush=True)
    print('kline archive coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)
    pd.DataFrame(miss,columns=['symbol','month','reason']).to_csv(f'{OUT}/kline_missing.csv',index=False)
    return miss

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

def load_kline(s):
    parts=[]
    for m in MONTHS:
        x=read_kzip(s,m)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    d=pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    lo=int(START.timestamp()*1000); hi=int(END.timestamp()*1000)
    d=d[(d.ts>=lo)&(d.ts<hi)].reset_index(drop=True)
    d['r1h']=np.log(d.c/d.c.shift(12))
    d['volmed']=d.qv.rolling(288,min_periods=144).median().shift(1)
    d['volx']=d.qv/(d.volmed+1e-12)
    return d

def candidate_rows(k):
    return k[(k.r1h.abs()>=PRICE_THR)&(k.volx>=VOLX_THR)].copy()

def needed_metric_days(s,k):
    c=candidate_rows(k); out=set()
    for ts in c.ts.astype('int64'):
        for t in (ts,ts-60*60*1000):
            out.add((s,pd.to_datetime(t,unit='ms',utc=True).strftime('%Y-%m-%d')))
    return out

def prefetch_metrics(req):
    req=sorted(req); miss=[]
    def one(sd):
        s,d=sd; ok,msg=download(MBASE.format(s=s,d=d),mpath(s,d),200); return s,d,ok,msg
    with ThreadPoolExecutor(max_workers=32) as ex:
        fs=[ex.submit(one,j) for j in req]
        for n,f in enumerate(as_completed(fs),1):
            s,d,ok,msg=f.result()
            if not ok:miss.append((s,d,msg))
            if n%200==0:print('metric archives',n,'/',len(req),'missing',len(miss),flush=True)
    print('metric archive coverage',len(req)-len(miss),'/',len(req),'missing',len(miss),flush=True)
    pd.DataFrame(miss,columns=['symbol','day','reason']).to_csv(f'{OUT}/metrics_missing.csv',index=False)
    return miss

def read_metric_day(s,d):
    p=mpath(s,d)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name))
        need=['create_time','sum_open_interest_value']
        if not all(c in x.columns for c in need):raise ValueError(f'columns={list(x.columns)}')
        x['dt']=pd.to_datetime(x.create_time,utc=True,errors='coerce')
        # Explicit millisecond conversion: pandas 3 may store parsed datetimes at microsecond resolution.
        naive=x['dt'].dt.tz_convert(None)
        x['ts']=naive.astype('datetime64[ms]').astype('int64')
        x['oi']=pd.to_numeric(x.sum_open_interest_value,errors='coerce')
        return x[['ts','oi']].dropna().astype({'ts':'int64'}).drop_duplicates('ts').sort_values('ts')
    except Exception as e:
        print('BAD_METRIC',s,d,repr(e),flush=True); return None

def load_metrics_for_symbol(s,days):
    parts=[]
    for d in sorted(days):
        x=read_metric_day(s,d)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    return pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)

def nearest_oi(m,targets):
    if m is None or not len(m):return np.full(len(targets),np.nan)
    a=pd.DataFrame({'target':np.asarray(targets,dtype='int64')})
    a['_ord']=np.arange(len(a)); a=a.sort_values('target')
    b=m.rename(columns={'ts':'mts'}).sort_values('mts')
    z=pd.merge_asof(a,b,left_on='target',right_on='mts',direction='nearest',tolerance=60000)
    return z.sort_values('_ord').oi.to_numpy()

def build_symbol_events(s,k,metric_days):
    c=candidate_rows(k).copy().sort_values('ts').reset_index()
    if c.empty:return pd.DataFrame(),{'symbol':s,'pricevol_candidates':0,'oi_matched':0,'raw_signals':0,'events':0}
    m=load_metrics_for_symbol(s,metric_days)
    targets=c.ts.to_numpy(dtype='int64')
    now=nearest_oi(m,targets); prev=nearest_oi(m,targets-60*60*1000)
    c['oi_now']=now; c['oi_prev']=prev; c['oi1h']=c.oi_now/(c.oi_prev+1e-12)-1
    matched=np.isfinite(c.oi1h)
    aligned=np.sign(c.r1h)==np.sign(c.oi1h)
    sig=c[matched & (c.oi1h.abs()>=OI_THR) & aligned].copy().sort_values('ts')
    keep=[]; last=-10**18
    for r in sig.itertuples():
        if int(r.ts)-last>=DEOVERLAP_MS:
            keep.append(r.Index); last=int(r.ts)
    out=[]
    for ci in keep:
        r=c.loc[ci]; i=int(r['index'])
        if i+12>=len(k):continue
        side=-1 if r.r1h>0 else 1
        row={'symbol':s,'ts':int(r.ts),'side':side,'oi1h':float(r.oi1h),'r1h':float(r.r1h),'volx':float(r.volx)}
        for h,n in HORIZONS:row[f'gross_{h}m_bp']=side*np.log(float(k.c.iloc[i+n])/float(k.c.iloc[i]))*1e4
        out.append(row)
    diag={'symbol':s,'pricevol_candidates':len(c),'oi_matched':int(matched.sum()),'raw_signals':len(sig),'events':len(out)}
    return pd.DataFrame(out),diag

def robust(x):
    x=np.sort(np.asarray(x,float)); cut=max(1,int(np.ceil(len(x)*.05)))
    return float(np.mean(x[:-cut])) if len(x)>cut else np.nan

prefetch_klines()
K={}; req=set()
for n,s in enumerate(ALTS,1):
    k=load_kline(s); K[s]=k
    if k is not None and len(k):req |= needed_metric_days(s,k)
    print('prepared',n,'/',len(ALTS),s,'bars',0 if k is None else len(k),'metric-days-so-far',len(req),flush=True)
if not req:raise RuntimeError('no price/volume candidates from kline archives')
missing=prefetch_metrics(req)
parts=[]; diags=[]
for n,s in enumerate(ALTS,1):
    k=K.get(s)
    if k is None or not len(k):continue
    days={d for ss,d in req if ss==s}
    x,diag=build_symbol_events(s,k,days); diags.append(diag)
    print('signal',n,'/',len(ALTS),s,diag,flush=True)
    if len(x):parts.append(x)
pd.DataFrame(diags).to_csv(f'{OUT}/coverage_diagnostics.csv',index=False)
if not parts:raise RuntimeError('no Strategy #5 events after valid archive ingestion')
e=pd.concat(parts,ignore_index=True).sort_values('ts').reset_index(drop=True); e.to_csv(f'{OUT}/events.csv',index=False)
rows=[]
for h,_ in HORIZONS:
    x=e[f'gross_{h}m_bp']
    rows.append({'horizon_min':h,'events':len(e),'symbols':e.symbol.nunique(),'mean_bp':x.mean(),'median_bp':x.median(),'win':(x>0).mean(),'p10_bp':x.quantile(.1),'p90_bp':x.quantile(.9),'remove_best5_mean_bp':robust(x)})
sumdf=pd.DataFrame(rows); sumdf.to_csv(f'{OUT}/summary.csv',index=False)
side=e.groupby('side').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean30=('gross_30m_bp','mean'),median30=('gross_30m_bp','median'),win30=('gross_30m_bp',lambda z:(z>0).mean())).reset_index(); side.to_csv(f'{OUT}/side.csv',index=False)
e['month']=pd.to_datetime(e.ts,unit='ms',utc=True).dt.strftime('%Y-%m'); month=e.groupby('month').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean30=('gross_30m_bp','mean'),median30=('gross_30m_bp','median')).reset_index(); month.to_csv(f'{OUT}/month.csv',index=False)
r=sumdf[sumdf.horizon_min==30].iloc[0]; sideok=(len(side)==2 and (side.mean30>=0).all())
passed=bool(r.events>=100 and r.symbols>=10 and r.mean_bp>=20 and r.median_bp>0 and r.remove_best5_mean_bp>0 and sideok)
print('\n# Strategy #5 Crowding/OI Unwind Gross Screen\n'); print(sumdf.to_markdown(index=False,floatfmt='.3f')); print('\nSides\n',side.to_markdown(index=False,floatfmt='.3f')); print('\nMonths\n',month.to_markdown(index=False,floatfmt='.3f')); print('\nMetric missing files:',len(missing)); print('Verdict:', 'PASS_TO_EXECUTION' if passed else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_EXECUTION' if passed else 'REJECT_OR_REDESIGN')+'\n')