import io, os, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #6: perp premium-dislocation mean reversion.
# Frozen before outcomes. Uses Binance Vision USD-M monthly 5m futures klines + premiumIndexKlines.
# Two independent historical blocks: 2021 and 2022. BOTH must pass to justify tick execution.
SYMS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','FILUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
BLOCKS={'BLOCK_A_2021':('2021-01','2021-12'),'BLOCK_B_2022':('2022-01','2022-12')}
OUT='crowding_unwind_output'; CACHE='strategy6_cache'; os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)
KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
PBASE='https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/{s}/5m/{s}-5m-{m}.zip'
UA={'User-Agent':'Mozilla/5.0'}

# Frozen signal.
PREM_ABS_DEV_BP=5.0
PREM_Z=3.0
RET15_ABS_BP=20.0
VOLX=1.5
ROLL=72          # 6h on 5m bars
MINROLL=36
DEOVERLAP=6      # 30m
HORIZONS=[(5,1),(15,3),(30,6)]
FEE_RT_BP=10.0

# Frozen per-block gate.
MIN_EVENTS=200
MIN_SYMBOLS=10
MIN_MEAN15=20.0
MIN_MEDIAN15=5.0
MIN_POS_MONTH_FRAC=0.60
MAX_TOP_SYMBOL_SHARE=0.20
MIN_FEEONLY_MEAN=10.0

def months(a,b):
    return pd.period_range(a,b,freq='M').astype(str).tolist()

def download(url,path,min_size=200):
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

def path(kind,s,m):return f'{CACHE}/{kind}-{s}-{m}.zip'

def prefetch():
    allm=sorted(set(sum([months(a,b) for a,b in BLOCKS.values()],[])))
    jobs=[]
    for s in SYMS:
        for m in allm:
            jobs += [('k',s,m,KBASE.format(s=s,m=m)),('p',s,m,PBASE.format(s=s,m=m))]
    miss=[]
    def one(j):
        kind,s,m,u=j; ok,msg=download(u,path(kind,s,m)); return kind,s,m,ok,msg
    with ThreadPoolExecutor(max_workers=24) as ex:
        fs=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fs),1):
            kind,s,m,ok,msg=f.result()
            if not ok:miss.append((kind,s,m,msg))
            if n%100==0:print('archives',n,'/',len(jobs),'missing',len(miss),flush=True)
    print('archive coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)
    pd.DataFrame(miss,columns=['kind','symbol','month','reason']).to_csv(f'{OUT}/strategy6_missing.csv',index=False)
    return miss

def read_zip(kind,s,m):
    p=path(kind,s,m)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),header=None)
        x=x.iloc[:,:12]; x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq','ignore']
        for c in ['ts','c','qv']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c'])
        if len(x) and x.ts.median()>1e14:x['ts']=np.floor(x.ts/1000.0)
        x['ts']=x.ts.astype('int64')
        if kind=='k':
            x['qv']=pd.to_numeric(x.qv,errors='coerce')
            return x[['ts','c','qv']].dropna()
        return x[['ts','c']].rename(columns={'c':'premium'})
    except Exception as e:
        print('BAD',kind,s,m,repr(e),flush=True); return None

def load_symbol_block(s,a,b):
    ks=[]; ps=[]
    for m in months(a,b):
        k=read_zip('k',s,m); p=read_zip('p',s,m)
        if k is not None and len(k):ks.append(k)
        if p is not None and len(p):ps.append(p)
    if not ks or not ps:return None
    k=pd.concat(ks,ignore_index=True).drop_duplicates('ts').sort_values('ts')
    p=pd.concat(ps,ignore_index=True).drop_duplicates('ts').sort_values('ts')
    d=pd.merge(k,p,on='ts',how='inner').sort_values('ts').reset_index(drop=True)
    if len(d)<ROLL+20:return None
    d['prem_bp']=d.premium*1e4
    d['prem_med']=d.prem_bp.rolling(ROLL,min_periods=MINROLL).median().shift(1)
    d['prem_std']=d.prem_bp.rolling(ROLL,min_periods=MINROLL).std(ddof=0).shift(1)
    d['prem_dev_bp']=d.prem_bp-d.prem_med
    d['prem_z']=d.prem_dev_bp/(d.prem_std+1e-9)
    d['r15_bp']=np.log(d.c/d.c.shift(3))*1e4
    d['volmed']=d.qv.rolling(ROLL,min_periods=MINROLL).median().shift(1)
    d['volx']=d.qv/(d.volmed+1e-12)
    return d

def events_for(s,d):
    aligned=np.sign(d.r15_bp)==np.sign(d.prem_dev_bp)
    sig=(d.prem_dev_bp.abs()>=PREM_ABS_DEV_BP)&(d.prem_z.abs()>=PREM_Z)&(d.r15_bp.abs()>=RET15_ABS_BP)&(d.volx>=VOLX)&aligned
    idx=np.where(sig.to_numpy())[0]
    keep=[]; last=-999999
    for i in idx:
        if i-last>=DEOVERLAP:keep.append(i); last=i
    out=[]
    for i in keep:
        if i+6>=len(d):continue
        side=-1 if d.prem_dev_bp.iloc[i]>0 else 1
        row={'symbol':s,'ts':int(d.ts.iloc[i]),'side':side,'prem_dev_bp':float(d.prem_dev_bp.iloc[i]),'prem_z':float(d.prem_z.iloc[i]),'r15_bp':float(d.r15_bp.iloc[i]),'volx':float(d.volx.iloc[i])}
        for h,n in HORIZONS:row[f'gross_{h}m_bp']=side*np.log(float(d.c.iloc[i+n])/float(d.c.iloc[i]))*1e4
        out.append(row)
    return pd.DataFrame(out)

def robust(x):
    x=np.sort(np.asarray(x,float)); cut=max(1,int(np.ceil(len(x)*.05)))
    return float(np.mean(x[:-cut])) if len(x)>cut else np.nan

def summarize(block,e):
    if e.empty:return {'block':block,'events':0,'symbols':0,'mean15_bp':np.nan,'median15_bp':np.nan,'win15':np.nan,'remove_best5_mean_bp':np.nan,'positive_month_frac':np.nan,'top_symbol_share':np.nan,'feeonly_mean_bp':np.nan,'long_mean15':np.nan,'short_mean15':np.nan,'pass':False}
    e=e.copy(); e['month']=pd.to_datetime(e.ts,unit='ms',utc=True).dt.strftime('%Y-%m')
    x=e.gross_15m_bp
    mon=e.groupby('month').gross_15m_bp.mean(); posfrac=float((mon>=0).mean())
    counts=e.symbol.value_counts(); top=float(counts.iloc[0]/len(e))
    longx=e.loc[e.side==1,'gross_15m_bp']; shortx=e.loc[e.side==-1,'gross_15m_bp']
    lm=float(longx.mean()) if len(longx) else np.nan; sm=float(shortx.mean()) if len(shortx) else np.nan
    fee=float(x.mean()-FEE_RT_BP)
    passed=bool(len(e)>=MIN_EVENTS and e.symbol.nunique()>=MIN_SYMBOLS and x.mean()>=MIN_MEAN15 and x.median()>MIN_MEDIAN15 and robust(x)>0 and posfrac>=MIN_POS_MONTH_FRAC and top<=MAX_TOP_SYMBOL_SHARE and fee>=MIN_FEEONLY_MEAN and np.isfinite(lm) and np.isfinite(sm) and lm>=0 and sm>=0)
    return {'block':block,'events':len(e),'symbols':e.symbol.nunique(),'mean15_bp':x.mean(),'median15_bp':x.median(),'win15':(x>0).mean(),'p10_bp':x.quantile(.1),'p90_bp':x.quantile(.9),'remove_best5_mean_bp':robust(x),'positive_month_frac':posfrac,'top_symbol_share':top,'feeonly_mean_bp':fee,'long_mean15':lm,'short_mean15':sm,'pass':passed}

prefetch()
all_events=[]; rows=[]; month_rows=[]; side_rows=[]
for block,(a,b) in BLOCKS.items():
    parts=[]
    for n,s in enumerate(SYMS,1):
        d=load_symbol_block(s,a,b)
        x=pd.DataFrame() if d is None else events_for(s,d)
        print(block,n,'/',len(SYMS),s,'events',len(x),flush=True)
        if len(x):parts.append(x)
    e=pd.concat(parts,ignore_index=True).sort_values('ts') if parts else pd.DataFrame()
    if len(e):
        e['block']=block; all_events.append(e)
        tmp=e.copy(); tmp['month']=pd.to_datetime(tmp.ts,unit='ms',utc=True).dt.strftime('%Y-%m')
        mm=tmp.groupby('month').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean15=('gross_15m_bp','mean'),median15=('gross_15m_bp','median')).reset_index(); mm['block']=block; month_rows.append(mm)
        ss=tmp.groupby('side').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean15=('gross_15m_bp','mean'),median15=('gross_15m_bp','median')).reset_index(); ss['block']=block; side_rows.append(ss)
    rows.append(summarize(block,e))

summary=pd.DataFrame(rows); summary.to_csv(f'{OUT}/strategy6_summary.csv',index=False)
if all_events:pd.concat(all_events,ignore_index=True).to_csv(f'{OUT}/strategy6_events.csv',index=False)
if month_rows:pd.concat(month_rows,ignore_index=True).to_csv(f'{OUT}/strategy6_months.csv',index=False)
if side_rows:pd.concat(side_rows,ignore_index=True).to_csv(f'{OUT}/strategy6_sides.csv',index=False)
overall=bool(len(summary)==2 and summary['pass'].all())
print('\n# Strategy #6 Premium Dislocation Mean Reversion\n'); print(summary.to_markdown(index=False,floatfmt='.3f')); print('\nOVERALL:', 'PASS_TO_TICK' if overall else 'REJECT_OR_REDESIGN')
open(f'{OUT}/strategy6_verdict.txt','w').write(('PASS_TO_TICK' if overall else 'REJECT_OR_REDESIGN')+'\n')
