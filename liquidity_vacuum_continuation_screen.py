import os, io, zipfile, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #11: Liquidity Vacuum / Impact-Efficiency Continuation
# Frozen before outcomes. Single USD-M perp market, signal-directed one-sided passive entry.
# No continuous/two-sided quoting / market making.

OUT='liquidity_vacuum_output'; os.makedirs(OUT,exist_ok=True)
SYMS=['BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','LINKUSDT','TRXUSDT','ETCUSDT']
BLOCKS={
    'BLOCK_A_2024_SAMPLE':2024,
    'BLOCK_B_2025_SAMPLE':2025,
}
LATENCIES=[100,250]
N_DAYS=30

# Frozen signal parameters
RET5_BP=12.0
FLOW5=0.70
IMPACT_EFF_MIN=12.0   # abs(5s bp return) per 1x trailing-median 5s quote-volume unit
MIN_QV_REL=0.35
CONFIRM_MAX_REV_BP=2.0
CONFIRM_FLOW_MIN=0.10
DEOVERLAP_SEC=20

# Frozen execution parameters
PASSIVE_OFFSET_BP=0.5
TRADE_THROUGH_BP=0.1
ENTRY_TIMEOUT_MS=2000
HOLD_MS=10000
EXIT_TIMEOUT_MS=1000

# Cost model (bp per completed round trip)
# optimistic: maker entry 0 + taker exit 5
# conservative: maker entry 2.5 + taker exit 5 + 1bp aggressive slippage
# hard: maker entry 5 + taker exit 5 + 2bp aggressive slippage
COST_OPT=5.0
COST_CONS=8.5
COST_HARD=12.0


def sampled_days(year, block):
    days=pd.date_range(f'{year}-01-01',f'{year}-12-31',freq='D',tz='UTC')
    scored=[]
    for d in days:
        s=d.strftime('%Y-%m-%d')
        h=hashlib.sha256(f'S11|{block}|{s}'.encode()).hexdigest()
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
        out=out[(out.ts>1_500_000_000_000)&(out.ts<2_000_000_000_000)]
        out['qv']=out.price*out.qty
        out['aggr']=np.where(out.buyer_maker,-1,1)
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
    x['qv_rel']=x.qv5/(x.qv5_med+1e-12)
    x['impact_eff']=x.r5_bp.abs()/(x.qv_rel+1e-12)
    return x


def detect_events(raw, block, sym, day):
    x=build_1s(raw)
    if x is None:return []
    shock=(x.r5_bp.abs()>=RET5_BP)&(x.qv5_med>0)&(x.qv_rel>=MIN_QV_REL)&(x.impact_eff>=IMPACT_EFF_MIN)&((np.sign(x.r5_bp)*x.flow5)>=FLOW5)
    ids=np.where(shock.fillna(False).to_numpy())[0]
    ev=[]; last_ms=-10**18
    for i in ids:
        if i<305 or i+2>=len(x):continue
        sign=1 if x.r5_bp.iloc[i]>0 else -1
        q1=float(x.qv.iloc[i+1])
        f1=float(x.signed_qv.iloc[i+1]/(q1+1e-12)) if q1>0 else 0.0
        cont=float(sign*np.log(x.price.iloc[i+1]/x.price.iloc[i])*1e4)
        # continuation thesis: no immediate reversal >2bp, and next-second flow is not meaningfully contra
        if cont < -CONFIRM_MAX_REV_BP:continue
        if sign*f1 < CONFIRM_FLOW_MIN:continue
        confirm_ms=int(x.sec.iloc[i+1]+1000)
        if confirm_ms-last_ms<DEOVERLAP_SEC*1000:continue
        last_ms=confirm_ms
        ev.append({
            'block':block,'symbol':sym,'day':day,'confirm_ms':confirm_ms,
            'shock_sign':sign,'trade_side':sign,'r5_bp':float(x.r5_bp.iloc[i]),
            'flow5':float(x.flow5.iloc[i]),'qv_rel':float(x.qv_rel.iloc[i]),
            'impact_eff':float(x.impact_eff.iloc[i]),'confirm_cont_bp':cont,'confirm_flow':f1,
        })
    return ev


def execute_event(raw,e,latency_ms):
    ts=raw.ts.to_numpy(np.int64); px=raw.price.to_numpy(float); ag=raw.aggr.to_numpy(np.int8)
    post=int(e['confirm_ms']+latency_ms)
    j=np.searchsorted(ts,post,'left')
    if j>=len(ts):return None
    ref=float(px[j]); side=int(e['trade_side'])
    # continuation on a micro-pullback: buy below ref after up-shock, sell above ref after down-shock
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
        'entry_wait_ms':fill_ts-post,
    }


def process_symbol_day(block,sym,day):
    raw=read_agg(getzip(day_url(sym,day)))
    if raw is None or len(raw)<100:
        return {'block':block,'symbol':sym,'day':day,'valid':False,'signals':[],'by_lat':{}}
    signals=detect_events(raw,block,sym,day)
    by_lat={}
    for lat in LATENCIES:
        filled=0; completed=[]
        for e in signals:
            z=execute_event(raw,e,lat)
            if not z:continue
            if z.get('filled'):filled+=1
            if z.get('completed'):completed.append({**e,**z,'latency_ms':lat})
        by_lat[lat]={'filled':filled,'completed':completed}
    return {'block':block,'symbol':sym,'day':day,'valid':True,'signals':signals,'by_lat':by_lat}


def trim_best5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(len(x)*.05)))
    return float(np.sort(x)[:-n].mean())


def summarize(block,lat,signals,filled,completed,coverage):
    e=pd.DataFrame(completed)
    row={
        'block':block,'latency_ms':lat,'signals':len(signals),'data_coverage':coverage,
        'entry_fills':filled,'entry_fill_rate':filled/max(1,len(signals)),'completed':len(e),'symbols':0,
        'completed_per_sampled_day':len(e)/N_DAYS,'gross_mean_bp':np.nan,'gross_median_bp':np.nan,
        'net_opt_mean_bp':np.nan,'net_cons_mean_bp':np.nan,'net_cons_median_bp':np.nan,
        'net_cons_remove_best5_bp':np.nan,'net_hard_mean_bp':np.nan,'win_cons':np.nan,
        'positive_week_frac':np.nan,'top_symbol_share':np.nan,'pass':False,
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
    if lat==100:
        row['pass']=bool(row['data_coverage']>=.95 and row['completed']>=600 and row['symbols']>=6 and row['completed_per_sampled_day']>=20.0 and row['entry_fill_rate']>=.20 and row['net_cons_mean_bp']>2.0 and row['net_cons_median_bp']>0 and row['net_cons_remove_best5_bp']>0 and row['net_hard_mean_bp']>0 and row['positive_week_frac']>=.60 and row['top_symbol_share']<=.30)
    else:
        row['pass']=bool(row['data_coverage']>=.95 and row['completed']>=600 and row['symbols']>=6 and row['completed_per_sampled_day']>=20.0 and row['entry_fill_rate']>=.20 and row['net_cons_mean_bp']>0 and row['net_cons_median_bp']>0 and row['net_cons_remove_best5_bp']>0)
    return row


rows=[]; coverage_rows=[]; all_completed=[]
for block,year in BLOCKS.items():
    days=sampled_days(year,block)
    pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_sampled_days.csv',index=False)
    results=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(process_symbol_day,block,s,d):(s,d) for s in SYMS for d in days}
        for n,f in enumerate(as_completed(futs),1):
            s,d=futs[f]
            try:r=f.result()
            except Exception:r={'block':block,'symbol':s,'day':d,'valid':False,'signals':[],'by_lat':{}}
            results.append(r)
            if n%40==0 or n==len(futs):print(block,'loaded',n,'/',len(futs),flush=True)
    valid=sum(1 for r in results if r['valid']); coverage=valid/len(results)
    coverage_rows.append({'block':block,'valid_symbol_days':valid,'expected_symbol_days':len(results),'coverage':coverage})
    signals=[e for r in results if r['valid'] for e in r['signals']]
    pd.DataFrame(signals).to_csv(f'{OUT}/{block}_signals.csv',index=False)
    print(block,'coverage',coverage,'signals',len(signals),flush=True)
    for lat in LATENCIES:
        filled=sum(r['by_lat'].get(lat,{}).get('filled',0) for r in results if r['valid'])
        completed=[e for r in results if r['valid'] for e in r['by_lat'].get(lat,{}).get('completed',[])]
        rows.append(summarize(block,lat,signals,filled,completed,coverage))
        if completed:
            pd.DataFrame(completed).to_csv(f'{OUT}/{block}_{lat}ms_completed.csv',index=False)
            all_completed.extend(completed)

pd.DataFrame(coverage_rows).to_csv(f'{OUT}/coverage.csv',index=False)
summary=pd.DataFrame(rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_completed:pd.DataFrame(all_completed).to_csv(f'{OUT}/completed_all.csv',index=False)
print('\n# Strategy #11 Liquidity Vacuum / Impact-Efficiency Continuation\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen economics: passive entry + taker exit; optimistic 5bp, conservative 8.5bp, hard 12bp. Trade-through fill proxy requires 0.1bp penetration and does not claim queue-priority realism. Each block is 30 SHA256-selected calendar days; first 5 minutes are warmup-only.\n')
verdict=bool(len(summary)==4 and summary['pass'].all())
print('OVERALL:', 'PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_NEXT_STAGE' if verdict else 'REJECT_OR_REDESIGN')+'\n')
