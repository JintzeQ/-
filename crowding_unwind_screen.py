import os, io, zipfile, time, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import requests
import numpy as np
import pandas as pd

# Strategy #5A: Premium / OI / Positioning Crowding -> Unwind
# New alpha family. 2022 is intentionally used because this repo had no prior 2021/2022 research.
# Stage-1 = gross-alpha discovery only; no executable spread/slippage/impact modeling yet.
# Primary horizon and OOS gates are frozen before looking at OOS.

SYMBOLS = [
    'BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','SOLUSDT','AVAXUSDT',
    'LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT'
]
MONTHS = pd.period_range('2022-01','2022-12',freq='M').astype(str).tolist()
DATES = pd.date_range('2022-01-01','2022-12-31',freq='D',tz='UTC')
TRAIN_END = pd.Timestamp('2022-07-01',tz='UTC')
VAL_END = pd.Timestamp('2022-10-01',tz='UTC')
OOS_END = pd.Timestamp('2023-01-01',tz='UTC')

# Frozen economic structure.
TOP_POS_RATIO = 1.15
GLOBAL_RATIO = 1.05
TAKER_RATIO = 1.10
DEOVERLAP_BARS = 12  # 60m on 5m bars
PRIMARY_H = 12       # 60m
HORIZONS = {15:3, 30:6, 60:12, 120:24}
ROLL_Z = 12*24*7     # 7d past-only normalization
ROLL_MIN = 12*24*2   # need at least 2d history

# Coarse TRAIN-only threshold grid. Validation/OOS do not select thresholds.
PREMIUM_Z_GRID = [1.5, 2.0, 2.5]
OI_GROWTH_PCT_GRID = [0.0, 0.5, 1.0]
EXTENSION_BP_GRID = [25.0, 50.0]
MIN_TRAIN_EVENTS = 100
MIN_TRAIN_SYMBOLS = 8

# Frozen OOS pass gate.
OOS_MEAN_GATE_BP = 20.0
OOS_MEDIAN_GATE_BP = 10.0
OOS_MIN_EVENTS = 100
OOS_MIN_SYMBOLS = 8
MIN_METRICS_DAY_COVERAGE = 0.95

OUT = 'crowding_unwind_output'
CACHE = 'crowding_unwind_cache'
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

BASE = 'https://data.binance.vision/data/futures/um'
KLINE_URL = BASE + '/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
PREMIUM_URL = BASE + '/monthly/premiumIndexKlines/{s}/5m/{s}-5m-{m}.zip'
METRICS_URL = BASE + '/daily/metrics/{s}/{s}-metrics-{d}.zip'
UA = {'User-Agent':'Mozilla/5.0'}


def get_url(url, path, min_size=100):
    if os.path.exists(path) and os.path.getsize(path) >= min_size:
        return True, 'cache'
    msg = 'failed'
    for k in range(3):
        try:
            r = requests.get(url, timeout=45, headers=UA)
            if r.status_code == 200 and len(r.content) >= min_size:
                with open(path,'wb') as f:
                    f.write(r.content)
                return True, 'download'
            if r.status_code == 404:
                return False, '404'
            msg = f'http{r.status_code}'
        except Exception as exc:
            msg = str(exc)
        time.sleep(0.3*(k+1))
    return False, msg


def kpath(s,m): return f'{CACHE}/kline-{s}-{m}.zip'
def ppath(s,m): return f'{CACHE}/premium-{s}-{m}.zip'
def mpath(s,d): return f'{CACHE}/metrics-{s}-{d}.zip'


def prefetch_monthlies():
    jobs=[]
    for s in SYMBOLS:
        for m in MONTHS:
            jobs.append(('kline',s,m,KLINE_URL.format(s=s,m=m),kpath(s,m),500))
            jobs.append(('premium',s,m,PREMIUM_URL.format(s=s,m=m),ppath(s,m),200))
    miss=[]
    def one(j):
        typ,s,m,u,p,minsz=j
        ok,msg=get_url(u,p,minsz)
        return typ,s,m,ok,msg
    with ThreadPoolExecutor(max_workers=24) as ex:
        fut=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fut),1):
            typ,s,m,ok,msg=f.result()
            if not ok: miss.append((typ,s,m,msg))
            if n%72==0:
                print('monthly download',n,'/',len(jobs),'missing',len(miss),flush=True)
    print('monthly coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)
    if miss: print('monthly missing sample',miss[:30],flush=True)
    return miss


def prefetch_metrics():
    jobs=[(s,d.strftime('%Y-%m-%d')) for s in SYMBOLS for d in DATES]
    miss=[]
    def one(sd):
        s,d=sd
        ok,msg=get_url(METRICS_URL.format(s=s,d=d),mpath(s,d),120)
        return s,d,ok,msg
    with ThreadPoolExecutor(max_workers=32) as ex:
        fut=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fut),1):
            s,d,ok,msg=f.result()
            if not ok: miss.append((s,d,msg))
            if n%500==0:
                print('metrics download',n,'/',len(jobs),'missing',len(miss),flush=True)
    print('metrics coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)
    if miss: print('metrics missing sample',miss[:40],flush=True)
    return miss


def read_zip_csv(path, header=None):
    if not os.path.exists(path): return None
    try:
        with zipfile.ZipFile(path) as z:
            names=[n for n in z.namelist() if n.endswith('.csv')]
            if not names: return None
            return pd.read_csv(z.open(names[0]), header=header)
    except Exception as exc:
        print('bad zip',path,exc,flush=True)
        return None


def normalize_ms(series):
    x=pd.to_numeric(series,errors='coerce')
    med=x.dropna().median()
    if pd.notna(med) and med>1e14:
        x=np.floor(x/1000.0)
    return x


def load_kline(s):
    parts=[]
    for m in MONTHS:
        x=read_zip_csv(kpath(s,m),header=None)
        if x is None or x.shape[1]<7: continue
        x=x.iloc[:,:7].copy(); x.columns=['ts','o','h','l','c','v','ct']
        x['ts']=normalize_ms(x.ts); x['c']=pd.to_numeric(x.c,errors='coerce')
        x=x.dropna(subset=['ts','c']); x['ts']=x.ts.astype('int64')
        parts.append(x[['ts','c']])
    if not parts:return None
    x=pd.concat(parts,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['dt']=pd.to_datetime(x.ts,unit='ms',utc=True)
    return x[['ts','dt','c']]


def load_premium(s):
    parts=[]
    for m in MONTHS:
        x=read_zip_csv(ppath(s,m),header=None)
        if x is None or x.shape[1]<5: continue
        x=x.iloc[:,:5].copy(); x.columns=['ts','o','h','l','pclose']
        x['ts']=normalize_ms(x.ts); x['pclose']=pd.to_numeric(x.pclose,errors='coerce')
        x=x.dropna(subset=['ts','pclose']); x['ts']=x.ts.astype('int64')
        parts.append(x[['ts','pclose']])
    if not parts:return None
    return pd.concat(parts,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)


def parse_metrics_file(s,d):
    p=mpath(s,d)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name))
        # Expected Binance Vision metrics schema. Also support headerless fallback.
        expected=['create_time','symbol','sum_open_interest','sum_open_interest_value',
                  'count_toptrader_long_short_ratio','sum_toptrader_long_short_ratio',
                  'count_long_short_ratio','sum_taker_long_short_vol_ratio']
        if not set(expected).issubset(set(x.columns)):
            with zipfile.ZipFile(p) as z:
                name=next(n for n in z.namelist() if n.endswith('.csv'))
                x=pd.read_csv(z.open(name),header=None)
            if x.shape[1] < 8:return None
            x=x.iloc[:,:8].copy(); x.columns=expected
        x['dt']=pd.to_datetime(x['create_time'],utc=True,errors='coerce')
        if x.dt.isna().all():
            raw=normalize_ms(x['create_time'])
            x['dt']=pd.to_datetime(raw,unit='ms',utc=True,errors='coerce')
        num=['sum_open_interest','sum_open_interest_value','count_toptrader_long_short_ratio',
             'sum_toptrader_long_short_ratio','count_long_short_ratio','sum_taker_long_short_vol_ratio']
        for c in num:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['dt']+num)
        if x.empty:return None
        x['ts']=(x.dt.astype('int64')//1_000_000).astype('int64')
        return x[['ts','dt']+num]
    except Exception as exc:
        print('bad metrics',s,d,exc,flush=True)
        return None


def load_metrics(s):
    parts=[]; okdays=0
    for d in DATES:
        ds=d.strftime('%Y-%m-%d')
        x=parse_metrics_file(s,ds)
        if x is not None and len(x):
            parts.append(x);okdays+=1
    coverage=okdays/len(DATES)
    if not parts:return None,coverage
    x=pd.concat(parts,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    return x,coverage


def past_z(x,win=ROLL_Z,minp=ROLL_MIN):
    mu=x.rolling(win,min_periods=minp).mean().shift(1)
    sd=x.rolling(win,min_periods=minp).std(ddof=0).shift(1)
    return (x-mu)/(sd.replace(0,np.nan))


def build_symbol_frame(s):
    K=load_kline(s); P=load_premium(s); M,cov=load_metrics(s)
    if K is None or P is None or M is None:
        return None,cov
    # Metrics and klines are natively 5m. Inner join ensures no stale positioning data is forward-filled.
    x=K.merge(P[['ts','pclose']],on='ts',how='inner').merge(M.drop(columns='dt'),on='ts',how='inner')
    x=x.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    if x.empty:return None,cov
    x['symbol']=s
    x['premium_bp']=x.pclose*1e4
    x['premium_z']=past_z(x.premium_bp)
    x['ret30_bp']=np.log(x.c/x.c.shift(6))*1e4
    x['oi_growth_1h_pct']=np.log(x.sum_open_interest_value/x.sum_open_interest_value.shift(12))*100
    # ratios are multiplicative, so use reciprocal symmetry for short crowding.
    x['top_pos']=x.sum_toptrader_long_short_ratio
    x['top_acct']=x.count_toptrader_long_short_ratio
    x['global_ratio']=x.count_long_short_ratio
    x['taker_ratio']=x.sum_taker_long_short_vol_ratio
    for mins,bars in HORIZONS.items():
        x[f'fwd{mins}_bp']=np.log(x.c.shift(-bars)/x.c)*1e4
    return x,cov


def event_mask(x,zthr,oithr,extbp):
    long_crowd=(
        (x.premium_z>=zthr) & (x.oi_growth_1h_pct>=oithr) & (x.ret30_bp>=extbp) &
        (x.top_pos>=TOP_POS_RATIO) & (x.global_ratio>=GLOBAL_RATIO) & (x.taker_ratio>=TAKER_RATIO)
    )
    short_crowd=(
        (x.premium_z<=-zthr) & (x.oi_growth_1h_pct>=oithr) & (x.ret30_bp<=-extbp) &
        (x.top_pos<=1/TOP_POS_RATIO) & (x.global_ratio<=1/GLOBAL_RATIO) & (x.taker_ratio<=1/TAKER_RATIO)
    )
    return long_crowd,short_crowd


def deoverlap_indices(mask):
    idx=np.flatnonzero(mask.fillna(False).to_numpy())
    keep=[];last=-10**12
    for i in idx:
        if i-last>=DEOVERLAP_BARS:
            keep.append(i);last=i
    return keep


def make_events(frames,zthr,oithr,extbp):
    rows=[]
    for s,x in frames.items():
        lc,sc=event_mask(x,zthr,oithr,extbp)
        # crowd_dir +1 = long crowd; -1 = short crowd. We FADE crowd, so trade_side=-crowd_dir.
        both=(lc|sc)
        for i in deoverlap_indices(both):
            crowd=1 if bool(lc.iloc[i]) else -1
            trade=-crowd
            r={'symbol':s,'dt':x.dt.iloc[i],'crowd_dir':crowd,'trade_side':trade,
               'premium_bp':float(x.premium_bp.iloc[i]),'premium_z':float(x.premium_z.iloc[i]),
               'oi_growth_1h_pct':float(x.oi_growth_1h_pct.iloc[i]),'ret30_bp':float(x.ret30_bp.iloc[i]),
               'top_pos':float(x.top_pos.iloc[i]),'global_ratio':float(x.global_ratio.iloc[i]),
               'taker_ratio':float(x.taker_ratio.iloc[i])}
            for mins in HORIZONS:
                v=x[f'fwd{mins}_bp'].iloc[i]
                r[f'rev{mins}_bp']=trade*float(v) if pd.notna(v) else np.nan
            rows.append(r)
    if not rows:return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(['dt','symbol']).reset_index(drop=True)


def split_name(dt):
    if dt<TRAIN_END:return 'train'
    if dt<VAL_END:return 'validation'
    if dt<OOS_END:return 'oos'
    return 'outside'


def stats(E,label,zthr,oithr,extbp):
    if E.empty:return {'split':label,'zthr':zthr,'oithr':oithr,'extbp':extbp,'events':0,'symbols':0}
    out={'split':label,'zthr':zthr,'oithr':oithr,'extbp':extbp,'events':len(E),'symbols':E.symbol.nunique()}
    for mins in HORIZONS:
        v=E[f'rev{mins}_bp'].dropna()
        out[f'mean{mins}_bp']=v.mean(); out[f'median{mins}_bp']=v.median(); out[f'win{mins}']=(v>0).mean()
        out[f'p10_{mins}_bp']=v.quantile(.1) if len(v) else np.nan
        out[f'p90_{mins}_bp']=v.quantile(.9) if len(v) else np.nan
    return out


def main():
    monthly_missing=prefetch_monthlies()
    metric_missing=prefetch_metrics()

    frames={};coverage=[]
    for n,s in enumerate(SYMBOLS,1):
        print('load symbol',n,'/',len(SYMBOLS),s,flush=True)
        x,cov=build_symbol_frame(s)
        monthly_bad=sum(1 for a,b,c,d in monthly_missing if b==s)
        usable=bool(x is not None and cov>=MIN_METRICS_DAY_COVERAGE and monthly_bad==0)
        coverage.append({'symbol':s,'metrics_day_coverage':cov,'monthly_missing':monthly_bad,
                         'joined_5m_rows':0 if x is None else len(x),'usable':usable})
        if usable:frames[s]=x
    C=pd.DataFrame(coverage);C.to_csv(f'{OUT}/coverage.csv',index=False)
    print(C.to_string(index=False),flush=True)
    print('usable symbols',len(frames),'/',len(SYMBOLS),flush=True)
    if len(frames)<8:
        raise RuntimeError('insufficient usable symbols for predeclared research gate')

    grid=[];event_cache={}
    for zthr in PREMIUM_Z_GRID:
        for oithr in OI_GROWTH_PCT_GRID:
            for extbp in EXTENSION_BP_GRID:
                E=make_events(frames,zthr,oithr,extbp)
                E['split']=E.dt.map(split_name) if len(E) else []
                event_cache[(zthr,oithr,extbp)]=E
                for sp in ['train','validation','oos']:
                    G=E[E.split==sp] if len(E) else E
                    grid.append(stats(G,sp,zthr,oithr,extbp))
    Q=pd.DataFrame(grid)
    Q.to_csv(f'{OUT}/grid_summary.csv',index=False)

    T=Q[(Q.split=='train')&(Q.events>=MIN_TRAIN_EVENTS)&(Q.symbols>=MIN_TRAIN_SYMBOLS)].copy()
    if T.empty:
        # Research failure without opening OOS for parameter selection. For reporting only, choose max-events train cell.
        fallback=Q[Q.split=='train'].sort_values(['events','symbols'],ascending=False).iloc[0]
        chosen=(float(fallback.zthr),float(fallback.oithr),float(fallback.extbp))
        selection_status='NO_TRAIN_CELL_MET_MIN_SAMPLE; reporting max-event cell only'
    else:
        T=T.sort_values(['mean60_bp','median60_bp','events'],ascending=[False,False,False])
        b=T.iloc[0];chosen=(float(b.zthr),float(b.oithr),float(b.extbp));selection_status='TRAIN_SELECTED'

    E=event_cache[chosen].copy();E.to_csv(f'{OUT}/chosen_events.csv',index=False)
    zthr,oithr,extbp=chosen
    S=pd.DataFrame([stats(E[E.split==sp],sp,zthr,oithr,extbp) for sp in ['train','validation','oos']])
    S.to_csv(f'{OUT}/chosen_summary.csv',index=False)
    O=E[E.split=='oos'].copy(); V=E[E.split=='validation'].copy()

    side=(O.groupby('trade_side').agg(events=('symbol','size'),symbols=('symbol','nunique'),
          mean60_bp=('rev60_bp','mean'),median60_bp=('rev60_bp','median'),
          win60=('rev60_bp',lambda z:(z>0).mean())).reset_index() if len(O) else pd.DataFrame())
    side.to_csv(f'{OUT}/oos_by_side.csv',index=False)
    sym=(O.groupby('symbol').agg(events=('symbol','size'),mean60_bp=('rev60_bp','mean'),median60_bp=('rev60_bp','median')).reset_index().sort_values('events',ascending=False) if len(O) else pd.DataFrame())
    sym.to_csv(f'{OUT}/oos_by_symbol.csv',index=False)

    o=stats(O,'oos',zthr,oithr,extbp); v=stats(V,'validation',zthr,oithr,extbp)
    passed=bool(selection_status=='TRAIN_SELECTED' and
                o.get('events',0)>=OOS_MIN_EVENTS and o.get('symbols',0)>=OOS_MIN_SYMBOLS and
                o.get('mean60_bp',-1e9)>=OOS_MEAN_GATE_BP and o.get('median60_bp',-1e9)>OOS_MEDIAN_GATE_BP and
                v.get('mean60_bp',-1e9)>0)

    lines=[
        '# Strategy #5A — Premium/OI/Positioning Crowding Unwind', '',
        'New alpha family: identify leveraged crowding with premium-index extremity, rising OI, aligned price extension, top/global positioning and taker-flow; trade the unwind opposite the crowded side.',
        'Stage-1 gross-alpha discovery only: close-to-close 5m klines, no spread/slippage/impact/fee deduction in event returns.',
        'Research block: 2022 only. Train=2022H1; Validation=2022Q3; Frozen OOS=2022Q4.',
        f'Universe requested: {len(SYMBOLS)} mature USD-M alts; usable after predeclared >=95% metrics-day coverage and complete monthly kline/premium coverage: {len(frames)}.',
        f'Train-only coarse grid: premium z {PREMIUM_Z_GRID}; OI 1h growth pct {OI_GROWTH_PCT_GRID}; 30m price extension bp {EXTENSION_BP_GRID}.',
        f'Fixed alignment filters: top-position ratio {TOP_POS_RATIO}, global ratio {GLOBAL_RATIO}, taker ratio {TAKER_RATIO}; 60m per-symbol de-overlap.',
        f'Primary horizon frozen at 60m. OOS gate: mean >= {OOS_MEAN_GATE_BP}bp, median > {OOS_MEDIAN_GATE_BP}bp, events >= {OOS_MIN_EVENTS}, symbols >= {OOS_MIN_SYMBOLS}, validation mean >0.', '',
        f'Selection status: **{selection_status}**.',
        f'Chosen TRAIN config: premium |z| >= {zthr:.2f}; OI 1h growth >= {oithr:.2f}%; aligned 30m extension >= {extbp:.1f}bp.', '',
        '## Chosen config summary', '', S.to_markdown(index=False,floatfmt='.3f'), '',
        '## Frozen OOS side diagnostic', '', side.to_markdown(index=False,floatfmt='.3f') if len(side) else '(none)', '',
        '## Frozen OOS top symbols by event count', '', sym.head(12).to_markdown(index=False,floatfmt='.3f') if len(sym) else '(none)', '',
        f'Verdict: **{"PASS_TO_EXECUTION" if passed else "REJECT_OR_REDESIGN"}**.',
        'Do not select long-only/short-only, a different horizon, or a favorable OOS threshold after seeing this result. If PASS, next stage is tick/BBO execution with the frozen rule and 5bp/side taker fee.'
    ]
    text='\n'.join(lines)
    open(f'{OUT}/summary.md','w').write(text)
    print(text,flush=True)

if __name__=='__main__':
    main()
