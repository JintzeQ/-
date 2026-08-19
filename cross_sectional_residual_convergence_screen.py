import io, os, time, zipfile, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #13: Cross-Sectional Common-Factor Residual Convergence
# Frozen before outcomes. BTC is NOT used as a privileged market benchmark.
# Common factor is leave-one-out cross-sectional median of 5s returns.
OUT='cross_sectional_residual_output'; os.makedirs(OUT,exist_ok=True)

SYMS=['BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','LINKUSDT','TRXUSDT','ETCUSDT']
BLOCKS={
    'BLOCK_A_2021_SAMPLE':2021,
    'BLOCK_B_2025_SAMPLE':2025,
}
N_DAYS=30
LATENCIES=[100,250]

# Frozen signal construction
RET_SEC=5
BETA_WIN_SEC=1800
BETA_MIN_SEC=900
BETA_MIN=0.0
BETA_MAX=3.0
RESID_SPREAD_BP=15.0
MIN_EXTREME_BP=6.0
DEOVERLAP_SEC=20

# Frozen execution
PASSIVE_OFFSET_BP=0.5
TRADE_THROUGH_BP=0.1
ENTRY_TIMEOUT_MS=2000
HEDGE_DELAY_MS=50
HEDGE_TIMEOUT_MS=1000
HOLD_MS=10000
EXIT_TIMEOUT_MS=1000

# Pair-level costs expressed per unit of gross notional (50% long / 50% short).
# Passive leg: maker entry + taker exit. Hedge leg: taker entry + taker exit.
# Optimistic: 0.5*(0+5) + 0.5*(5+5) = 7.5bp
# Conservative: passive 2.5+5+1 = 8.5; hedge 5+1+5+1 = 12; average = 10.25bp
# Hard: passive 5+5+2 = 12; hedge 5+2+5+2 = 14; average = 13bp
COST_OPT=7.5
COST_CONS=10.25
COST_HARD=13.0

UA={'User-Agent':'Mozilla/5.0'}

def sampled_days(year,block):
    days=pd.date_range(f'{year}-01-01',f'{year}-12-31',freq='D',tz='UTC')
    scored=[]
    for d in days:
        s=d.strftime('%Y-%m-%d')
        h=hashlib.sha256(f'S13|{block}|{s}'.encode()).hexdigest()
        scored.append((h,s))
    return [s for _,s in sorted(scored)[:N_DAYS]]

def day_url(sym,day):
    return f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'

def getzip(url):
    for k in range(4):
        try:
            r=requests.get(url,timeout=60,headers=UA)
            if r.status_code==200 and len(r.content)>100:
                return r.content
            if r.status_code==404:
                return None
        except Exception:
            pass
        time.sleep(.4*(k+1))
    return None

def read_agg(blob):
    if blob is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            d=pd.read_csv(z.open(name),header=None,low_memory=False)
        if d.shape[1]<7:return None
        px=pd.to_numeric(d.iloc[:,1],errors='coerce')
        qty=pd.to_numeric(d.iloc[:,2],errors='coerce')
        ts=pd.to_numeric(d.iloc[:,5],errors='coerce')
        maker=d.iloc[:,6].astype(str).str.lower().isin(['true','1'])
        x=pd.DataFrame({'ts':ts,'price':px,'qty':qty,'buyer_maker':maker})
        x=x.dropna(subset=['ts','price','qty'])
        x=x[(x.price>0)&(x.qty>0)]
        x['ts']=x.ts.astype('int64')
        x=x[(x.ts>1_500_000_000_000)&(x.ts<2_000_000_000_000)]
        x['aggr']=np.where(x.buyer_maker,-1,1).astype('int8')
        return x[['ts','price','aggr']].sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:
        return None

def load_symbol_day(sym,day):
    raw=read_agg(getzip(day_url(sym,day)))
    return sym,raw

def price_1s(raw, day):
    if raw is None or len(raw)<100:return None
    x=raw.copy()
    x['sec']=(x.ts//1000)*1000
    g=x.groupby('sec',sort=True).price.last()
    start=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)
    idx=np.arange(start,start+24*60*60*1000,1000,dtype='int64')
    return g.reindex(idx).ffill()

def build_signals(rawmap, block, day):
    prices={}
    for s in SYMS:
        p=price_1s(rawmap.get(s),day)
        if p is not None: prices[s]=p
    if len(prices)<len(SYMS): return [],None
    pmat=pd.DataFrame(prices)
    ret=np.log(pmat/pmat.shift(RET_SEC))*1e4
    resid=pd.DataFrame(index=ret.index,columns=SYMS,dtype=float)
    beta_diag=[]
    for s in SYMS:
        others=[x for x in SYMS if x!=s]
        mkt=ret[others].median(axis=1,skipna=True)
        ri=ret[s]
        cov=ri.rolling(BETA_WIN_SEC,min_periods=BETA_MIN_SEC).cov(mkt)
        var=mkt.rolling(BETA_WIN_SEC,min_periods=BETA_MIN_SEC).var()
        beta=(cov/(var+1e-12)).shift(1).clip(BETA_MIN,BETA_MAX)
        resid[s]=ri-beta*mkt
        valid=(ri.notna()&mkt.notna())
        if valid.sum()>100:
            corr=float(ri[valid].corr(mkt[valid]))
            beta_diag.append({'block':block,'day':day,'symbol':s,'loo_factor_r2':corr*corr if np.isfinite(corr) else np.nan,
                              'beta_median':float(beta.median()) if beta.notna().any() else np.nan})
    start_ms=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)+(BETA_WIN_SEC+RET_SEC+5)*1000
    last_used={s:-10**18 for s in SYMS}
    events=[]
    for ts,row in resid.loc[resid.index>=start_ms].iterrows():
        if row.isna().any():continue
        hi=str(row.idxmax()); lo=str(row.idxmin())
        mx=float(row[hi]); mn=float(row[lo]); spread=mx-mn
        if spread<RESID_SPREAD_BP or mx<MIN_EXTREME_BP or mn>-MIN_EXTREME_BP:continue
        if int(ts)-last_used[hi]<DEOVERLAP_SEC*1000 or int(ts)-last_used[lo]<DEOVERLAP_SEC*1000:continue
        signal_ms=int(ts+1000)
        passive_symbol=lo if abs(mn)>=abs(mx) else hi
        passive_side=1 if passive_symbol==lo else -1
        hedge_symbol=hi if passive_symbol==lo else lo
        hedge_side=-1 if hedge_symbol==hi else 1
        events.append({
            'block':block,'day':day,'signal_ms':signal_ms,
            'long_symbol':lo,'short_symbol':hi,
            'long_resid_bp':mn,'short_resid_bp':mx,'resid_spread_bp':spread,
            'passive_symbol':passive_symbol,'passive_side':passive_side,
            'hedge_symbol':hedge_symbol,'hedge_side':hedge_side,
        })
        last_used[hi]=signal_ms; last_used[lo]=signal_ms
    return events,beta_diag

def first_trade(ts, px, when, timeout_ms=None):
    j=np.searchsorted(ts,when,'left')
    if j>=len(ts):return None
    if timeout_ms is not None and ts[j]>when+timeout_ms:return None
    return int(j)

def execute_event(rawmap,e,latency_ms):
    ps=e['passive_symbol']; hs=e['hedge_symbol']
    pr=rawmap.get(ps); hr=rawmap.get(hs)
    if pr is None or hr is None:return None
    pts=pr.ts.to_numpy(np.int64); ppx=pr.price.to_numpy(float); pag=pr.aggr.to_numpy(np.int8)
    hts=hr.ts.to_numpy(np.int64); hpx=hr.price.to_numpy(float)
    post=int(e['signal_ms']+latency_ms)
    j=first_trade(pts,ppx,post,1000)
    if j is None:return {'filled':False}
    ref=float(ppx[j]); side=int(e['passive_side'])
    limit=ref*(1-side*PASSIVE_OFFSET_BP/1e4)
    end=post+ENTRY_TIMEOUT_MS
    j1=np.searchsorted(pts,end,'right')
    if j1<=j:return {'filled':False}
    p=ppx[j:j1]; a=pag[j:j1]
    if side==1:
        ok=np.where((a==-1)&(p<=limit*(1-TRADE_THROUGH_BP/1e4)))[0]
    else:
        ok=np.where((a==1)&(p>=limit*(1+TRADE_THROUGH_BP/1e4)))[0]
    if len(ok)==0:return {'filled':False}
    k=j+int(ok[0]); fill_ts=int(pts[k]); fill_px=float(limit)
    hk=first_trade(hts,hpx,fill_ts+HEDGE_DELAY_MS,HEDGE_TIMEOUT_MS)
    if hk is None:return {'filled':True,'completed':False,'hedge_failed':True}
    hedge_ts=int(hts[hk]); hedge_px=float(hpx[hk])
    target=hedge_ts+HOLD_MS
    pe=first_trade(pts,ppx,target,EXIT_TIMEOUT_MS)
    he=first_trade(hts,hpx,target,EXIT_TIMEOUT_MS)
    if pe is None or he is None:return {'filled':True,'completed':False,'hedge_failed':False}
    pexit=float(ppx[pe]); hexit=float(hpx[he])
    pgross=side*np.log(pexit/fill_px)*1e4
    hside=int(e['hedge_side'])
    hgross=hside*np.log(hexit/hedge_px)*1e4
    gross=.5*(pgross+hgross)
    long_contrib=.5*(pgross if side==1 else hgross)
    short_contrib=.5*(pgross if side==-1 else hgross)
    return {
        'filled':True,'completed':True,'fill_ts':fill_ts,'hedge_ts':hedge_ts,
        'passive_entry_px':fill_px,'hedge_entry_px':hedge_px,
        'passive_exit_px':pexit,'hedge_exit_px':hexit,
        'passive_leg_gross_bp':pgross,'hedge_leg_gross_bp':hgross,
        'long_contrib_bp':long_contrib,'short_contrib_bp':short_contrib,
        'gross_bp':gross,'net_opt_bp':gross-COST_OPT,
        'net_cons_bp':gross-COST_CONS,'net_hard_bp':gross-COST_HARD,
        'entry_wait_ms':fill_ts-post,'hedge_wait_ms':hedge_ts-fill_ts,
    }

def trim_best5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(len(x)*.05)))
    return float(np.sort(x)[:-n].mean())

def summarize(block,lat,signals,filled,completed,coverage):
    e=pd.DataFrame(completed)
    row={'block':block,'latency_ms':lat,'signals':signals,'data_coverage':coverage,
         'entry_fills':filled,'entry_fill_rate':filled/max(1,signals),'completed':len(e),
         'symbols':0,'completed_per_sampled_day':len(e)/N_DAYS,'gross_mean_bp':np.nan,
         'gross_median_bp':np.nan,'net_cons_mean_bp':np.nan,'net_cons_median_bp':np.nan,
         'net_cons_remove_best5_bp':np.nan,'net_hard_mean_bp':np.nan,
         'positive_week_frac':np.nan,'top_symbol_share':np.nan,'top_pair_share':np.nan,
         'long_contrib_mean_bp':np.nan,'short_contrib_mean_bp':np.nan,'pass':False}
    if len(e)==0:return row
    x=e.net_cons_bp
    syms=pd.concat([e.long_symbol,e.short_symbol],ignore_index=True)
    pairkey=e.apply(lambda r:'|'.join(sorted([r.long_symbol,r.short_symbol])),axis=1)
    dt=pd.to_datetime(e.hedge_ts,unit='ms',utc=True)
    weeks=dt.dt.strftime('%G-%V')
    wm=e.assign(week=weeks).groupby('week').net_cons_bp.mean()
    row.update({
        'symbols':int(syms.nunique()),
        'gross_mean_bp':float(e.gross_bp.mean()),'gross_median_bp':float(e.gross_bp.median()),
        'net_cons_mean_bp':float(x.mean()),'net_cons_median_bp':float(x.median()),
        'net_cons_remove_best5_bp':trim_best5(x),'net_hard_mean_bp':float(e.net_hard_bp.mean()),
        'positive_week_frac':float((wm>0).mean()) if len(wm) else np.nan,
        'top_symbol_share':float(syms.value_counts(normalize=True).max()),
        'top_pair_share':float(pairkey.value_counts(normalize=True).max()),
        'long_contrib_mean_bp':float(e.long_contrib_bp.mean()),
        'short_contrib_mean_bp':float(e.short_contrib_bp.mean()),
    })
    common=(row['data_coverage']>=.95 and row['completed']>=600 and row['symbols']>=8
            and row['completed_per_sampled_day']>=20 and row['entry_fill_rate']>=.20
            and row['net_cons_mean_bp']>2 and row['net_cons_median_bp']>0
            and row['net_cons_remove_best5_bp']>0 and row['net_hard_mean_bp']>0
            and row['positive_week_frac']>=.60 and row['top_symbol_share']<=.30
            and row['top_pair_share']<=.25
            and row['long_contrib_mean_bp']>0 and row['short_contrib_mean_bp']>0)
    if lat==100:
        row['pass']=bool(common)
    else:
        row['pass']=bool(row['data_coverage']>=.95 and row['completed']>=600 and row['symbols']>=8
                         and row['completed_per_sampled_day']>=20 and row['entry_fill_rate']>=.20
                         and row['net_cons_mean_bp']>0 and row['net_cons_median_bp']>0
                         and row['net_cons_remove_best5_bp']>0)
    return row

summary_rows=[]; coverage_rows=[]; beta_rows=[]; all_completed=[]
for block,year in BLOCKS.items():
    days=sampled_days(year,block)
    pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_sampled_days.csv',index=False)
    block_signals=0; block_valid_symbol_days=0; expected=N_DAYS*len(SYMS)
    filled_by_lat={lat:0 for lat in LATENCIES}
    completed_by_lat={lat:[] for lat in LATENCIES}
    signal_rows=[]
    for di,day in enumerate(days,1):
        rawmap={}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs={ex.submit(load_symbol_day,s,day):s for s in SYMS}
            for f in as_completed(futs):
                try:s,raw=f.result()
                except Exception:s,raw=futs[f],None
                if raw is not None and len(raw)>=100:
                    rawmap[s]=raw; block_valid_symbol_days+=1
        if len(rawmap)==len(SYMS):
            events,bdiag=build_signals(rawmap,block,day)
            if bdiag:beta_rows.extend(bdiag)
        else:
            events=[]
        block_signals+=len(events); signal_rows.extend(events)
        for lat in LATENCIES:
            for e in events:
                z=execute_event(rawmap,e,lat)
                if not z:continue
                if z.get('filled'):filled_by_lat[lat]+=1
                if z.get('completed'):
                    rec={**e,**z,'latency_ms':lat}
                    completed_by_lat[lat].append(rec); all_completed.append(rec)
        print(block,'day',di,'/',len(days),day,'valid_syms',len(rawmap),'signals',len(events),flush=True)
        del rawmap
    coverage=block_valid_symbol_days/expected
    coverage_rows.append({'block':block,'valid_symbol_days':block_valid_symbol_days,
                          'expected_symbol_days':expected,'coverage':coverage,'signals':block_signals})
    if signal_rows:pd.DataFrame(signal_rows).to_csv(f'{OUT}/{block}_signals.csv',index=False)
    for lat in LATENCIES:
        comp=completed_by_lat[lat]
        if comp:pd.DataFrame(comp).to_csv(f'{OUT}/{block}_{lat}ms_completed.csv',index=False)
        summary_rows.append(summarize(block,lat,block_signals,filled_by_lat[lat],comp,coverage))

summary=pd.DataFrame(summary_rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
pd.DataFrame(coverage_rows).to_csv(f'{OUT}/coverage.csv',index=False)
if beta_rows:pd.DataFrame(beta_rows).to_csv(f'{OUT}/factor_diagnostics.csv',index=False)
if all_completed:pd.DataFrame(all_completed).to_csv(f'{OUT}/completed_all.csv',index=False)

print('\n# Strategy #13 Cross-Sectional Common-Factor Residual Convergence\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
if beta_rows:
    b=pd.DataFrame(beta_rows)
    diag=b.groupby('block').agg(median_loo_factor_r2=('loo_factor_r2','median'),
                                median_beta=('beta_median','median')).reset_index()
    print('\nCommon-factor diagnostics (descriptive only)\n')
    print(diag.to_markdown(index=False,floatfmt='.3f'))
print('\nBTC is not a privileged benchmark. Common factor is leave-one-out median of the trading universe.')
print('Frozen pair economics per gross notional: optimistic 7.5bp, conservative 10.25bp, hard 13bp.')
print('Execution: one extreme leg passive; after fill, opposite leg hedged aggressively; 10s hold; both exits aggressive.')
verdict=bool(len(summary)==4 and summary['pass'].all())
print('\nOVERALL:', 'PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')+'\n')
