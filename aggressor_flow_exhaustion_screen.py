import os, io, zipfile, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #10: Aggressor-Flow Exhaustion -> Passive Fade
# Frozen before outcomes. Single USD-M perp market, signal-directed one-sided passive entry.
# No continuous two-sided quoting / market making.

OUT='aggressor_flow_exhaustion_output'; os.makedirs(OUT,exist_ok=True)
SYMS=['BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','LINKUSDT','TRXUSDT','ETCUSDT']
BLOCKS={
    'BLOCK_A_2021_SAMPLE':2021,
    'BLOCK_B_2022_SAMPLE':2022,
}
LATENCIES=[100,250]
N_DAYS=30

# Frozen signal/execution parameters
RET5_BP=15.0
FLOW5=0.80
QV_MULT=2.5
CONFIRM_EXT_BP=3.0
CONFIRM_FLOW=0.25
DEOVERLAP_SEC=20
PASSIVE_OFFSET_BP=1.0
TRADE_THROUGH_BP=0.1
ENTRY_TIMEOUT_MS=2000
HOLD_MS=10000
EXIT_TIMEOUT_MS=1000

# Cost model (bp per completed round-trip)
# optimistic: maker entry 0 + taker exit 5
# conservative: maker entry 2.5 + taker exit 5 + 1bp aggressive slippage
# hard: maker entry 5 + taker exit 5 + 2bp aggressive slippage
COST_OPT=5.0
COST_CONS=8.5
COST_HARD=12.0


def sampled_days(year, block):
    # Return-independent deterministic calendar sampling across the whole year.
    days=pd.date_range(f'{year}-01-01',f'{year}-12-31',freq='D',tz='UTC')
    scored=[]
    for d in days:
        s=d.strftime('%Y-%m-%d')
        h=hashlib.sha256(f'S10|{block}|{s}'.encode()).hexdigest()
        scored.append((h,s))
    return [s for _,s in sorted(scored)[:N_DAYS]]


def getzip(url):
    for k in range(4):
        try:
            r=requests.get(url,timeout=45,headers={'User-Agent':'Mozilla/5.0'})
            if r.status_code==200:return r.content
            if r.status_code==404:return None
        except Exception:
            pass
        time.sleep(.35*(k+1))
    return None


def read_agg(blob):
    if blob is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            d=pd.read_csv(z.open(z.namelist()[0]),header=None,low_memory=False)
        if d.shape[1]<7:return None
        px=pd.to_numeric(d.iloc[:,1],errors='coerce')
        qty=pd.to_numeric(d.iloc[:,2],errors='coerce')
        ts=pd.to_numeric(d.iloc[:,5],errors='coerce')
        maker=d.iloc[:,6].astype(str).str.lower().isin(['true','1'])
        out=pd.DataFrame({'ts':ts,'price':px,'qty':qty,'buyer_maker':maker})
        out=out.dropna(subset=['ts','price','qty'])
        out=out[(out.price>0)&(out.qty>0)]
        out['ts']=out.ts.astype('int64')
        # Futures public aggTrades timestamps are ms. Guard against malformed rows only.
        out=out[(out.ts>1_500_000_000_000)&(out.ts<2_000_000_000_000)]
        out['qv']=out.price*out.qty
        out['aggr']=np.where(out.buyer_maker,-1,1)  # seller aggressor=-1, buyer aggressor=+1
        out['signed_qv']=out.qv*out.aggr
        return out.sort_values('ts').drop_duplicates(['ts','price','qty','aggr']).reset_index(drop=True)
    except Exception:
        return None


def day_url(sym,day):
    return f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'


def build_1s(raw):
    if raw is None or len(raw)<100:return None
    r=raw.copy(); r['sec']=(r.ts//1000)*1000
    g=r.groupby('sec',sort=True).agg(price=('price','last'),qv=('qv','sum'),signed_qv=('signed_qv','sum')).reset_index()
    if len(g)<30:return None
    idx=np.arange(int(g.sec.min()),int(g.sec.max())+1000,1000,dtype='int64')
    x=g.set_index('sec').reindex(idx)
    x.index.name='sec'
    x['price']=x.price.ffill().bfill()
    x['qv']=x.qv.fillna(0.0); x['signed_qv']=x.signed_qv.fillna(0.0)
    x=x.reset_index()
    x['r5_bp']=np.log(x.price/x.price.shift(5))*1e4
    x['qv5']=x.qv.rolling(5,min_periods=5).sum()
    x['sq5']=x.signed_qv.rolling(5,min_periods=5).sum()
    x['flow5']=x.sq5/(x.qv5+1e-12)
    x['qv5_med']=x.qv5.rolling(300,min_periods=120).median().shift(5)
    return x


def detect_events(raw, block, sym, day):
    x=build_1s(raw)
    if x is None:return []
    shock=(x.r5_bp.abs()>=RET5_BP)&(x.qv5_med>0)&(x.qv5>=QV_MULT*x.qv5_med)&((np.sign(x.r5_bp)*x.flow5)>=FLOW5)
    ids=np.where(shock.fillna(False).to_numpy())[0]
    ev=[]; last_ms=-10**18
    for i in ids:
        if i<305 or i+3>=len(x):continue
        sign=1 if x.r5_bp.iloc[i]>0 else -1
        q2=float(x.qv.iloc[i+1]+x.qv.iloc[i+2])
        if q2<=0:continue
        f2=float((x.signed_qv.iloc[i+1]+x.signed_qv.iloc[i+2])/(q2+1e-12))
        ext=float(sign*np.log(x.price.iloc[i+2]/x.price.iloc[i])*1e4)
        if ext>CONFIRM_EXT_BP:continue
        if sign*f2>CONFIRM_FLOW:continue
        confirm_ms=int(x.sec.iloc[i+2]+1000)  # after both confirmation seconds are complete
        if confirm_ms-last_ms<DEOVERLAP_SEC*1000:continue
        last_ms=confirm_ms
        ev.append({
            'block':block,'symbol':sym,'day':day,'confirm_ms':confirm_ms,
            'shock_sign':sign,'fade_side':-sign,'r5_bp':float(x.r5_bp.iloc[i]),
            'flow5':float(x.flow5.iloc[i]),'qv_mult':float(x.qv5.iloc[i]/x.qv5_med.iloc[i]),
            'confirm_ext_bp':ext,'confirm_flow':f2,
        })
    return ev


def execute_event(raw,e,latency_ms):
    ts=raw.ts.to_numpy(np.int64); px=raw.price.to_numpy(float); ag=raw.aggr.to_numpy(np.int8)
    post=int(e['confirm_ms']+latency_ms)
    j=np.searchsorted(ts,post,'left')
    if j>=len(ts):return None
    ref=float(px[j]); side=int(e['fade_side'])
    limit=ref*(1-side*PASSIVE_OFFSET_BP/1e4)  # long below, short above
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
        'entry_wait_ms':fill_ts-post,
    }


def load_symbol_day(block,sym,day):
    blob=getzip(day_url(sym,day)); raw=read_agg(blob)
    if raw is None or len(raw)<100:
        return {'block':block,'symbol':sym,'day':day,'valid':False,'events':[],'raw':None}
    ev=detect_events(raw,block,sym,day)
    return {'block':block,'symbol':sym,'day':day,'valid':True,'events':ev,'raw':raw}


def trim_best5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(len(x)*.05)))
    return float(np.sort(x)[:-n].mean())


def summarize(block,lat,all_signals,valid_signal_count,filled,completed,coverage):
    e=pd.DataFrame(completed)
    row={
        'block':block,'latency_ms':lat,'signals':len(all_signals),'data_valid_signals':valid_signal_count,
        'data_coverage':coverage,'entry_fills':filled,'entry_fill_rate':filled/max(1,valid_signal_count),
        'completed':len(e),'symbols':0,'completed_per_sampled_day':len(e)/N_DAYS,
        'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_opt_mean_bp':np.nan,
        'net_cons_mean_bp':np.nan,'net_cons_median_bp':np.nan,'net_cons_remove_best5_bp':np.nan,
        'net_hard_mean_bp':np.nan,'win_cons':np.nan,'positive_week_frac':np.nan,'top_symbol_share':np.nan,'pass':False,
    }
    if len(e)==0:return row
    x=e.net_cons_bp
    row.update({
        'symbols':int(e.symbol.nunique()),'gross_mean_bp':float(e.gross_bp.mean()),'gross_median_bp':float(e.gross_bp.median()),
        'net_opt_mean_bp':float(e.net_opt_bp.mean()),'net_cons_mean_bp':float(x.mean()),'net_cons_median_bp':float(x.median()),
        'net_cons_remove_best5_bp':trim_best5(x),'net_hard_mean_bp':float(e.net_hard_bp.mean()),'win_cons':float((x>0).mean()),
        'top_symbol_share':float(e.symbol.value_counts(normalize=True).max()),
    })
    dt=pd.to_datetime(e.fill_ts,unit='ms',utc=True)
    e=e.assign(week=dt.dt.strftime('%G-%V'))
    wm=e.groupby('week').net_cons_bp.mean()
    row['positive_week_frac']=float((wm>0).mean()) if len(wm) else np.nan
    primary=(lat==100)
    common=(row['data_coverage']>=.95 and row['completed']>=150 and row['symbols']>=6 and row['completed_per_sampled_day']>=5.0 and row['entry_fill_rate']>=.20 and row['net_cons_mean_bp']>2.0 and row['net_cons_median_bp']>0 and row['net_cons_remove_best5_bp']>0 and row['net_hard_mean_bp']>0 and row['positive_week_frac']>=.60 and row['top_symbol_share']<=.30)
    if primary: row['pass']=bool(common)
    else:
        row['pass']=bool(row['data_coverage']>=.95 and row['completed']>=150 and row['symbols']>=6 and row['completed_per_sampled_day']>=5.0 and row['entry_fill_rate']>=.20 and row['net_cons_mean_bp']>0 and row['net_cons_median_bp']>0 and row['net_cons_remove_best5_bp']>0)
    return row


rows=[]; all_exec=[]; coverage_rows=[]
for block,year in BLOCKS.items():
    days=sampled_days(year,block)
    pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_sampled_days.csv',index=False)
    jobs=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(load_symbol_day,block,s,d):(s,d) for s in SYMS for d in days}
        for n,f in enumerate(as_completed(futs),1):
            s,d=futs[f]
            try:r=f.result()
            except Exception:r={'block':block,'symbol':s,'day':d,'valid':False,'events':[],'raw':None}
            jobs.append(r)
            if n%40==0 or n==len(futs):
                print(block,'loaded',n,'/',len(futs),flush=True)
    valid_days=sum(1 for r in jobs if r['valid']); coverage=valid_days/len(jobs)
    coverage_rows.append({'block':block,'valid_symbol_days':valid_days,'expected_symbol_days':len(jobs),'coverage':coverage})
    all_signals=[]
    for r in jobs:
        if r['valid']: all_signals.extend(r['events'])
    pd.DataFrame(all_signals).to_csv(f'{OUT}/{block}_signals.csv',index=False)
    print(block,'coverage',coverage,'signals',len(all_signals),flush=True)
    # Index loaded raw by symbol/day; signals on missing data do not exist, so coverage is separately gated.
    rawmap={(r['symbol'],r['day']):r['raw'] for r in jobs if r['valid']}
    for lat in LATENCIES:
        filled=0; completed=[]
        for e in all_signals:
            raw=rawmap.get((e['symbol'],e['day']))
            if raw is None:continue
            z=execute_event(raw,e,lat)
            if not z:continue
            if z.get('filled'):filled+=1
            if z.get('completed'):
                rec={**e,**z,'latency_ms':lat}; completed.append(rec); all_exec.append(rec)
        row=summarize(block,lat,all_signals,len(all_signals),filled,completed,coverage)
        rows.append(row)
        if completed:pd.DataFrame(completed).to_csv(f'{OUT}/{block}_{lat}ms_completed.csv',index=False)

pd.DataFrame(coverage_rows).to_csv(f'{OUT}/coverage.csv',index=False)
summary=pd.DataFrame(rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_exec:pd.DataFrame(all_exec).to_csv(f'{OUT}/completed_all.csv',index=False)
print('\n# Strategy #10 Aggressor-Flow Exhaustion -> Passive Fade\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen economics: maker entry only; optimistic cost 5bp, conservative 8.5bp, hard 12bp. Small-size trade-through fill proxy ignores queue depth but requires 0.1bp price penetration, not mere touch. First 5 minutes of each sampled UTC day are warmup-only.\n')
# BOTH blocks and BOTH latency rows must pass.
verdict=bool(len(summary)==4 and summary['pass'].all())
print('OVERALL:', 'PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')+'\n')
