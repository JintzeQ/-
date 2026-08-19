import io, os, gzip, hashlib, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

OUT='cross_venue_leadlag_output'; os.makedirs(OUT,exist_ok=True)
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','DOGEUSDT']
BLOCKS={'BLOCK_A_2024':('2024-01-01','2024-06-30'),'BLOCK_B_2025':('2025-01-01','2025-06-30')}
N_DAYS=10
LATENCIES=[100,250]
RET_MS=1000
LEADER_SHOCK_BP=12.0
MIN_GAP_BP=10.0
MAX_LAGGER_MOVE_BP=8.0
HOLD_MS=2000
DEOVERLAP_MS=3000
FEE_BP=10.0
STRESS_BP=12.0
UA={'User-Agent':'Mozilla/5.0'}

def sampled_days(block,start,end):
    ds=pd.date_range(start,end,freq='D',tz='UTC')
    scored=[]
    for d in ds:
        s=d.strftime('%Y-%m-%d')
        scored.append((hashlib.sha256(f'S16|{block}|{s}'.encode()).hexdigest(),s))
    return [s for _,s in sorted(scored)[:N_DAYS]]

def get(url):
    for k in range(4):
        try:
            r=requests.get(url,timeout=60,headers=UA)
            if r.status_code==200 and len(r.content)>100:return r.content
            if r.status_code==404:return None
        except Exception: pass
        time.sleep(.4*(k+1))
    return None

def load_binance(sym,day):
    url=f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'
    b=get(url)
    if b is None:return None
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            n=next(x for x in z.namelist() if x.endswith('.csv'))
            d=pd.read_csv(z.open(n),header=None,low_memory=False)
        ts=pd.to_numeric(d.iloc[:,5],errors='coerce'); px=pd.to_numeric(d.iloc[:,1],errors='coerce')
        x=pd.DataFrame({'ts':ts,'px':px}).dropna(); x=x[(x.px>0)]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def load_bybit(sym,day):
    url=f'https://public.bybit.com/trading/{sym}/{sym}{day}.csv.gz'
    b=get(url)
    if b is None:return None
    try:
        raw=gzip.decompress(b)
        d=pd.read_csv(io.BytesIO(raw),low_memory=False)
        cols={c.lower():c for c in d.columns}
        pcol=cols.get('price')
        tcol=cols.get('timestamp') or cols.get('time')
        if pcol is None or tcol is None:return None
        px=pd.to_numeric(d[pcol],errors='coerce'); ts=pd.to_numeric(d[tcol],errors='coerce')
        # Bybit archive timestamp is commonly Unix seconds with fractional ms.
        med=float(ts.dropna().median())
        if med<1e11: ts=ts*1000
        elif med>1e15: ts=ts/1000
        x=pd.DataFrame({'ts':ts,'px':px}).dropna(); x=x[(x.px>0)]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def one_sec_last(x,day):
    if x is None or len(x)<100:return None
    s=(x.ts//1000)*1000
    g=pd.Series(x.px.to_numpy(float),index=s).groupby(level=0).last()
    start=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)
    idx=np.arange(start,start+86400000,1000,dtype='int64')
    return g.reindex(idx).ffill(limit=2)

def first_trade(x,when,timeout=1000):
    ts=x.ts.to_numpy(np.int64); j=np.searchsorted(ts,when,'left')
    if j>=len(ts) or ts[j]>when+timeout:return None
    return j

def build_and_exec(sym,day,bn,bb,lat):
    ps=one_sec_last(bn,day); pl=one_sec_last(bb,day)
    if ps is None or pl is None:return [],0
    frame=pd.DataFrame({'b':ps,'y':pl}).dropna()
    rb=np.log(frame.b/frame.b.shift(1))*1e4
    ry=np.log(frame.y/frame.y.shift(1))*1e4
    gap=ry-rb
    events=[]; last=-10**18
    bnts=bn.ts.to_numpy(np.int64); bnpx=bn.px.to_numpy(float)
    for ts in frame.index[2:]:
        if ts-last<DEOVERLAP_MS:continue
        a=float(ry.loc[ts]); b=float(rb.loc[ts]); g=float(gap.loc[ts])
        if not np.isfinite(a+b+g):continue
        if abs(a)<LEADER_SHOCK_BP or abs(b)>MAX_LAGGER_MOVE_BP:continue
        if np.sign(a)!=np.sign(g) or abs(g)<MIN_GAP_BP:continue
        signal=int(ts+1000); side=1 if a>0 else -1
        j=first_trade(bn,signal+lat,1000)
        if j is None:continue
        entry_ts=int(bnts[j]); entry=float(bnpx[j])
        k=first_trade(bn,entry_ts+HOLD_MS,1000)
        if k is None:continue
        exit_ts=int(bnts[k]); exit=float(bnpx[k])
        gross=side*np.log(exit/entry)*1e4
        events.append({'symbol':sym,'day':day,'latency_ms':lat,'signal_ms':signal,'entry_ts':entry_ts,'exit_ts':exit_ts,
                       'leader_ret_bp':a,'lagger_ret_bp':b,'gap_bp':g,'side':side,'gross_bp':gross,
                       'net_fee_bp':gross-FEE_BP,'net_stress_bp':gross-STRESS_BP})
        last=signal
    return events,1

def trim5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(.05*len(x))))
    return float(np.sort(x)[:-n].mean())

rows=[]; all_events=[]; coverage=[]
for block,(start,end) in BLOCKS.items():
    days=sampled_days(block,start,end); pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_days.csv',index=False)
    valid_pairs=0; expected=len(days)*len(SYMS)
    ev_by_lat={x:[] for x in LATENCIES}
    for di,day in enumerate(days,1):
        pairs={}
        with ThreadPoolExecutor(max_workers=6) as ex:
            fut={}
            for s in SYMS:
                fut[ex.submit(load_binance,s,day)]=(s,'bn'); fut[ex.submit(load_bybit,s,day)]=(s,'bb')
            tmp={s:{} for s in SYMS}
            for f in as_completed(fut):
                s,v=fut[f]
                try:tmp[s][v]=f.result()
                except Exception:tmp[s][v]=None
        for s in SYMS:
            bn=tmp[s].get('bn'); bb=tmp[s].get('bb')
            if bn is not None and bb is not None:
                valid_pairs+=1
                for lat in LATENCIES:
                    ev,_=build_and_exec(s,day,bn,bb,lat); ev_by_lat[lat].extend(ev); all_events.extend(ev)
        print(block,'day',di,'/',len(days),day,'valid_pairs',sum(1 for s in SYMS if tmp[s].get('bn') is not None and tmp[s].get('bb') is not None),flush=True)
    cov=valid_pairs/expected
    for lat in LATENCIES:
        e=pd.DataFrame(ev_by_lat[lat]); n=len(e)
        r={'block':block,'latency_ms':lat,'coverage':cov,'completed':n,'symbols':0,'completed_per_day':n/N_DAYS,
           'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_fee_mean_bp':np.nan,'net_fee_median_bp':np.nan,
           'net_fee_remove_best5_bp':np.nan,'net_stress_mean_bp':np.nan,'positive_day_frac':np.nan,'top_symbol_share':np.nan,'pass':False}
        if n:
            dm=e.groupby('day').net_fee_bp.mean(); r.update({'symbols':int(e.symbol.nunique()),'gross_mean_bp':float(e.gross_bp.mean()),
            'gross_median_bp':float(e.gross_bp.median()),'net_fee_mean_bp':float(e.net_fee_bp.mean()),'net_fee_median_bp':float(e.net_fee_bp.median()),
            'net_fee_remove_best5_bp':trim5(e.net_fee_bp),'net_stress_mean_bp':float(e.net_stress_bp.mean()),
            'positive_day_frac':float((dm>0).mean()),'top_symbol_share':float(e.symbol.value_counts(normalize=True).max())})
            core=(cov>=.95 and n>=500 and r['symbols']>=3 and r['completed_per_day']>=50 and r['net_fee_mean_bp']>2 and
                  r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0 and r['net_stress_mean_bp']>0 and
                  r['positive_day_frac']>=.60 and r['top_symbol_share']<=.50)
            r['pass']=bool(core if lat==100 else (cov>=.95 and n>=500 and r['symbols']>=3 and r['completed_per_day']>=50 and r['net_fee_mean_bp']>0 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0))
        rows.append(r)
summary=pd.DataFrame(rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_events:pd.DataFrame(all_events).to_csv(f'{OUT}/events.csv',index=False)
print('\n# Strategy #16 Cross-Venue Same-Asset Price Discovery\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen: Bybit information venue; Binance USD-M execution venue; 1s leader shock >=12bp; Binance same-second move <=8bp; residual gap >=10bp aligned with leader; enter Binance first trade after signal+latency; hold 2s; taker/taker cost 10bp, stress 12bp; no market making.')
print('OVERALL:', 'PASS' if len(summary)==4 and summary['pass'].all() else 'REJECT_OR_REDESIGN')
