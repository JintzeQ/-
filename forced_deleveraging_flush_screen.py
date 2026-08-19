import os, io, time, zipfile, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #12: OI-Collapse Forced-Deleveraging Flush -> Passive Snapback
# Frozen before outcomes. This is a liquidation/forced-flow PROXY, not direct liquidation tape:
# require a sharp 5m decline in open interest, a large same-window price move,
# aligned aggressor flow, and elevated volume. Trade a short-horizon snapback only after
# the 5m bar and ending OI snapshot are complete. No continuous/two-sided quoting.

OUT='forced_deleveraging_output'
CACHE='forced_deleveraging_cache'
os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)

SYMS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT',
      'LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT']
BLOCKS={
    'BLOCK_A_2023_JAN_MAY':('2023-01-01','2023-05-31'),
    'BLOCK_B_2026_JAN_MAY':('2026-01-01','2026-05-31'),
}
N_DAYS=45
LATENCIES=[100,250]

# Frozen forced-flow proxy
OI_DROP=-0.008          # <= -0.8% OI contracts over 5m
RET5_BP=20.0            # >= 20bp absolute price move over same 5m bar
FLOW_ALIGN=0.60         # directional taker-flow ratio aligned with price move
QV_MULT=1.50            # 5m quote volume >= 1.5x shifted trailing 6h median
DEOVERLAP_MS=10*60*1000

# Frozen signal-directed passive execution
PASSIVE_OFFSET_BP=0.5
TRADE_THROUGH_BP=0.1
ENTRY_TIMEOUT_MS=3000
HOLD_MS=30000
EXIT_TIMEOUT_MS=1000

# Costs per completed round trip
COST_OPT=5.0
COST_CONS=8.5
COST_HARD=12.0

KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
MBASE='https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip'
ABASE='https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'
UA={'User-Agent':'Mozilla/5.0'}

def sampled_days(block,start,end):
    days=pd.date_range(start,end,freq='D',tz='UTC')
    scored=[]
    for d in days:
        s=d.strftime('%Y-%m-%d')
        h=hashlib.sha256(f'S12|{block}|{s}'.encode()).hexdigest()
        scored.append((h,s))
    return [s for _,s in sorted(scored)[:N_DAYS]]

def getzip(url,path,min_size=100):
    if os.path.exists(path) and os.path.getsize(path)>=min_size:
        return True,'cache'
    msg='failed'
    for k in range(4):
        try:
            r=requests.get(url,timeout=45,headers=UA)
            if r.status_code==200 and len(r.content)>=min_size:
                with open(path,'wb') as f:f.write(r.content)
                return True,'download'
            if r.status_code==404:return False,'404'
            msg=f'http{r.status_code}'
        except Exception as e:
            msg=repr(e)
        time.sleep(.35*(k+1))
    return False,msg

def kpath(s,m): return f'{CACHE}/k-{s}-{m}.zip'
def mpath(s,d): return f'{CACHE}/m-{s}-{d}.zip'
def apath(s,d): return f'{CACHE}/a-{s}-{d}.zip'

def months_for_block(start,end):
    st=pd.Timestamp(start)
    en=pd.Timestamp(end)
    warm=(st-pd.offsets.MonthBegin(1)).strftime('%Y-%m')
    months=[warm]+pd.period_range(st.strftime('%Y-%m'),en.strftime('%Y-%m'),freq='M').astype(str).tolist()
    return list(dict.fromkeys(months))

def read_kzip(s,m):
    p=kpath(s,m)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),header=None,low_memory=False)
        if x.shape[1]<11:return None
        x=x.iloc[:,:12]
        x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq','ignore']
        for c in ['ts','o','c','qv','tbq']:
            x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','o','c','qv','tbq'])
        if len(x) and x.ts.median()>1e14:x['ts']=np.floor(x.ts/1000.0)
        x['ts']=x.ts.astype('int64')
        return x[['ts','o','c','qv','tbq']].sort_values('ts')
    except Exception as e:
        print('BAD_KLINE',s,m,repr(e),flush=True); return None

def read_metric_day(s,d):
    p=mpath(s,d)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),low_memory=False)
        if 'create_time' not in x.columns or 'sum_open_interest' not in x.columns:
            raise ValueError(f'columns={list(x.columns)}')
        dt=pd.to_datetime(x.create_time,utc=True,errors='coerce')
        naive=dt.dt.tz_convert(None)
        x['ts']=naive.astype('datetime64[ms]').astype('int64')
        x['oi']=pd.to_numeric(x.sum_open_interest,errors='coerce')
        x=x[['ts','oi']].dropna()
        x=x[x.oi>0].drop_duplicates('ts').sort_values('ts')
        return x.astype({'ts':'int64'})
    except Exception as e:
        print('BAD_METRIC',s,d,repr(e),flush=True); return None

def read_agg(s,d):
    p=apath(s,d)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),header=None,low_memory=False)
        if x.shape[1]<7:return None
        px=pd.to_numeric(x.iloc[:,1],errors='coerce')
        qty=pd.to_numeric(x.iloc[:,2],errors='coerce')
        ts=pd.to_numeric(x.iloc[:,5],errors='coerce')
        maker=x.iloc[:,6].astype(str).str.lower().isin(['true','1'])
        y=pd.DataFrame({'ts':ts,'price':px,'qty':qty,'buyer_maker':maker}).dropna()
        y=y[(y.price>0)&(y.qty>0)]
        y['ts']=y.ts.astype('int64')
        y=y[(y.ts>1_500_000_000_000)&(y.ts<2_000_000_000_000)]
        y['aggr']=np.where(y.buyer_maker,-1,1).astype('int8')
        return y[['ts','price','aggr']].sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception as e:
        print('BAD_AGG',s,d,repr(e),flush=True); return None

def download_block_inputs(block,start,end,days):
    months=months_for_block(start,end)
    kjobs=[(s,m) for s in SYMS for m in months]
    metric_days=set()
    for d in days:
        t=pd.Timestamp(d,tz='UTC')
        metric_days.add(d)
        metric_days.add((t-pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
    mjobs=[(s,d) for s in SYMS for d in sorted(metric_days)]
    kmiss=[]; mmiss=[]
    def dk(sm):
        s,m=sm; ok,msg=getzip(KBASE.format(s=s,m=m),kpath(s,m),500); return s,m,ok,msg
    def dm(sd):
        s,d=sd; ok,msg=getzip(MBASE.format(s=s,d=d),mpath(s,d),200); return s,d,ok,msg
    with ThreadPoolExecutor(max_workers=24) as ex:
        fs=[ex.submit(dk,j) for j in kjobs]
        for n,f in enumerate(as_completed(fs),1):
            s,m,ok,msg=f.result()
            if not ok:kmiss.append((s,m,msg))
            if n%40==0 or n==len(kjobs):print(block,'klines',n,'/',len(kjobs),'missing',len(kmiss),flush=True)
    with ThreadPoolExecutor(max_workers=32) as ex:
        fs=[ex.submit(dm,j) for j in mjobs]
        for n,f in enumerate(as_completed(fs),1):
            s,d,ok,msg=f.result()
            if not ok:mmiss.append((s,d,msg))
            if n%120==0 or n==len(mjobs):print(block,'metrics',n,'/',len(mjobs),'missing',len(mmiss),flush=True)
    return months,metric_days,kjobs,mjobs,kmiss,mmiss

def load_kline_symbol(s,months):
    parts=[]
    for m in months:
        x=read_kzip(s,m)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    x=pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    x['ret_bar_bp']=np.log(x.c/x.o)*1e4
    x['flow']=(2*x.tbq-x.qv)/(x.qv+1e-12)
    x['qv_med6h']=x.qv.rolling(72,min_periods=36).median().shift(1)
    x['qv_mult']=x.qv/(x.qv_med6h+1e-12)
    return x

def load_metric_symbol(s,metric_days):
    parts=[]
    for d in sorted(metric_days):
        x=read_metric_day(s,d)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    m=pd.concat(parts,ignore_index=True).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    m['prev_ts']=m.ts.shift(1)
    m['oi_prev']=m.oi.shift(1)
    m['oi5']=m.oi/(m.oi_prev+1e-12)-1
    m['gap_ms']=m.ts-m.prev_ts
    return m

def detect_events(block,days,s,k,m):
    if k is None or m is None:return []
    selected=set(days)
    km=k.set_index('ts')
    out=[]; last=-10**18
    for r in m.itertuples():
        event_ms=int(r.ts)
        if pd.to_datetime(event_ms,unit='ms',utc=True).strftime('%Y-%m-%d') not in selected:continue
        if not np.isfinite(r.oi5) or abs(float(r.gap_ms)-300000)>60000:continue
        if float(r.oi5)>OI_DROP:continue
        bar_ts=event_ms-300000
        if bar_ts not in km.index:continue
        b=km.loc[bar_ts]
        if isinstance(b,pd.DataFrame):b=b.iloc[-1]
        ret=float(b.ret_bar_bp); flow=float(b.flow); qvm=float(b.qv_mult)
        if not np.isfinite(qvm):continue
        sign=1 if ret>0 else -1
        if abs(ret)<RET5_BP:continue
        if sign*flow<FLOW_ALIGN:continue
        if qvm<QV_MULT:continue
        if event_ms-last<DEOVERLAP_MS:continue
        last=event_ms
        out.append({
            'block':block,'symbol':s,'event_ms':event_ms,
            'shock_sign':sign,'side':-sign,'oi5':float(r.oi5),
            'ret5_bp':ret,'flow5':flow,'qv_mult':qvm,
            'day':pd.to_datetime(event_ms,unit='ms',utc=True).strftime('%Y-%m-%d')
        })
    return out

def download_event_aggs(events):
    pairs=sorted(set((e['symbol'],e['day']) for e in events))
    miss=[]
    def da(sd):
        s,d=sd; ok,msg=getzip(ABASE.format(s=s,d=d),apath(s,d),500); return s,d,ok,msg
    with ThreadPoolExecutor(max_workers=16) as ex:
        fs=[ex.submit(da,j) for j in pairs]
        for n,f in enumerate(as_completed(fs),1):
            s,d,ok,msg=f.result()
            if not ok:miss.append((s,d,msg))
            if n%50==0 or n==len(pairs):print('aggTrades',n,'/',len(pairs),'missing',len(miss),flush=True)
    return pairs,miss

def execute(raw,e,latency_ms):
    if raw is None or len(raw)<10:return None
    ts=raw.ts.to_numpy(np.int64); px=raw.price.to_numpy(float); ag=raw.aggr.to_numpy(np.int8)
    post=int(e['event_ms']+latency_ms)
    j=np.searchsorted(ts,post,'left')
    if j>=len(ts):return None
    ref=float(px[j]); side=int(e['side'])
    limit=ref*(1-side*PASSIVE_OFFSET_BP/1e4)
    end=post+ENTRY_TIMEOUT_MS
    k1=np.searchsorted(ts,end,'right')
    if k1<=j:return {'filled':False}
    p=px[j:k1]; a=ag[j:k1]
    if side==1:
        ok=np.where((a==-1)&(p<=limit*(1-TRADE_THROUGH_BP/1e4)))[0]
    else:
        ok=np.where((a==1)&(p>=limit*(1+TRADE_THROUGH_BP/1e4)))[0]
    if len(ok)==0:return {'filled':False}
    k=j+int(ok[0]); fill_ts=int(ts[k]); fill_px=float(limit)
    target=fill_ts+HOLD_MS
    q=np.searchsorted(ts,target,'left'); qend=np.searchsorted(ts,target+EXIT_TIMEOUT_MS,'right')
    if q>=len(ts) or q>=qend:return {'filled':True,'completed':False}
    exit_ts=int(ts[q]); exit_px=float(px[q])
    gross=float(side*np.log(exit_px/fill_px)*1e4)
    return {
        'filled':True,'completed':True,'fill_ts':fill_ts,'fill_px':fill_px,'exit_ts':exit_ts,'exit_px':exit_px,
        'gross_bp':gross,'net_opt_bp':gross-COST_OPT,'net_cons_bp':gross-COST_CONS,'net_hard_bp':gross-COST_HARD,
        'entry_wait_ms':fill_ts-post
    }

def trim_best5(x):
    x=np.sort(np.asarray(x,float))
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(len(x)*.05)))
    return float(x[:-n].mean())

def summarize(block,lat,signals,completed,filled,data_cov,agg_cov):
    e=pd.DataFrame(completed)
    row={
        'block':block,'latency_ms':lat,'signals':len(signals),'data_coverage':data_cov,'agg_coverage':agg_cov,
        'entry_fills':filled,'entry_fill_rate':filled/max(1,len(signals)),
        'completed':len(e),'symbols':0,'completed_per_sampled_day':len(e)/N_DAYS,
        'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_cons_mean_bp':np.nan,'net_cons_median_bp':np.nan,
        'net_cons_remove_best5_bp':np.nan,'net_hard_mean_bp':np.nan,'positive_week_frac':np.nan,
        'top_symbol_share':np.nan,'long_net_mean_bp':np.nan,'short_net_mean_bp':np.nan,'pass':False
    }
    if len(e)==0:return row
    x=e.net_cons_bp
    row.update({
        'symbols':int(e.symbol.nunique()),'gross_mean_bp':float(e.gross_bp.mean()),'gross_median_bp':float(e.gross_bp.median()),
        'net_cons_mean_bp':float(x.mean()),'net_cons_median_bp':float(x.median()),
        'net_cons_remove_best5_bp':trim_best5(x),'net_hard_mean_bp':float(e.net_hard_bp.mean()),
        'top_symbol_share':float(e.symbol.value_counts(normalize=True).max())
    })
    dt=pd.to_datetime(e.fill_ts,unit='ms',utc=True)
    e=e.assign(week=dt.dt.strftime('%G-%V'))
    wm=e.groupby('week').net_cons_bp.mean()
    row['positive_week_frac']=float((wm>0).mean()) if len(wm) else np.nan
    if (e.side==1).any():row['long_net_mean_bp']=float(e.loc[e.side==1,'net_cons_bp'].mean())
    if (e.side==-1).any():row['short_net_mean_bp']=float(e.loc[e.side==-1,'net_cons_bp'].mean())
    common=(row['data_coverage']>=.95 and row['agg_coverage']>=.95 and row['completed']>=450 and
            row['symbols']>=8 and row['completed_per_sampled_day']>=10 and row['entry_fill_rate']>=.20 and
            row['net_cons_mean_bp']>3.0 and row['net_cons_median_bp']>0 and
            row['net_cons_remove_best5_bp']>0 and row['net_hard_mean_bp']>0 and
            row['positive_week_frac']>=.60 and row['top_symbol_share']<=.25 and
            np.isfinite(row['long_net_mean_bp']) and row['long_net_mean_bp']>0 and
            np.isfinite(row['short_net_mean_bp']) and row['short_net_mean_bp']>0)
    if lat==100:
        row['pass']=bool(common)
    else:
        row['pass']=bool(row['data_coverage']>=.95 and row['agg_coverage']>=.95 and row['completed']>=450 and
                         row['symbols']>=8 and row['completed_per_sampled_day']>=10 and
                         row['entry_fill_rate']>=.20 and row['net_cons_mean_bp']>0 and
                         row['net_cons_median_bp']>0 and row['net_cons_remove_best5_bp']>0)
    return row

all_rows=[]; coverage_rows=[]; all_completed=[]
for block,(start,end) in BLOCKS.items():
    days=sampled_days(block,start,end)
    pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_sampled_days.csv',index=False)
    months,metric_days,kjobs,mjobs,kmiss,mmiss=download_block_inputs(block,start,end,days)
    kcov=(len(kjobs)-len(kmiss))/max(1,len(kjobs))
    mcov=(len(mjobs)-len(mmiss))/max(1,len(mjobs))
    data_cov=min(kcov,mcov)
    events=[]
    for n,s in enumerate(SYMS,1):
        k=load_kline_symbol(s,months); m=load_metric_symbol(s,metric_days)
        ev=detect_events(block,days,s,k,m); events.extend(ev)
        print(block,'signal',n,'/',len(SYMS),s,'events',len(ev),flush=True)
    events=sorted(events,key=lambda z:(z['event_ms'],z['symbol']))
    pd.DataFrame(events).to_csv(f'{OUT}/{block}_signals.csv',index=False)
    print(block,'coverage',data_cov,'signals',len(events),flush=True)
    pairs,amiss=download_event_aggs(events)
    agg_cov=(len(pairs)-len(amiss))/max(1,len(pairs)) if pairs else 0.0
    rawmap={}
    for s,d in pairs:
        if not os.path.exists(apath(s,d)):continue
        raw=read_agg(s,d)
        if raw is not None and len(raw):rawmap[(s,d)]=raw
    coverage_rows.append({'block':block,'kline_coverage':kcov,'metrics_coverage':mcov,'data_coverage':data_cov,
                          'event_symbol_days':len(pairs),'agg_missing':len(amiss),'agg_coverage':agg_cov,'signals':len(events)})
    for lat in LATENCIES:
        filled=0; completed=[]
        for e in events:
            z=execute(rawmap.get((e['symbol'],e['day'])),e,lat)
            if not z:continue
            if z.get('filled'):filled+=1
            if z.get('completed'):
                rec={**e,**z,'latency_ms':lat}; completed.append(rec); all_completed.append(rec)
        if completed:pd.DataFrame(completed).to_csv(f'{OUT}/{block}_{lat}ms_completed.csv',index=False)
        all_rows.append(summarize(block,lat,events,completed,filled,data_cov,agg_cov))

summary=pd.DataFrame(all_rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
pd.DataFrame(coverage_rows).to_csv(f'{OUT}/coverage.csv',index=False)
if all_completed:pd.DataFrame(all_completed).to_csv(f'{OUT}/completed_all.csv',index=False)
print('\n# Strategy #12 OI-Collapse Forced-Deleveraging Flush -> Passive Snapback\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nImportant: this uses OI collapse + price/flow/volume as a forced-deleveraging proxy; it is NOT direct liquidation-tape replay.')
print('Frozen economics: passive entry + taker exit; optimistic 5bp, conservative 8.5bp, hard 12bp. 30s hold. Trade-through fill proxy requires 0.1bp penetration.')
verdict=bool(len(summary)==4 and summary['pass'].all())
print('\nOVERALL:', 'PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')+'\n')
