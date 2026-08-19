import io, os, time, zipfile, gzip
from collections import deque
import requests, pandas as pd, numpy as np
from tabulate import tabulate

# Strategy #16: Cross-Venue Same-Asset Price Discovery
# Frozen before outcomes. Bybit is not assumed to lead; leadership must be
# established from strictly prior days. Execution venue is Binance USD-M only.
OUT='cross_venue_price_discovery_output'; os.makedirs(OUT,exist_ok=True)
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','SOLUSDT']
BLOCKS={'BLOCK_A_2024_JANFEB':'2024-01-01','BLOCK_B_2025_MARAPR':'2025-03-01'}
BLOCK_DAYS=45; WARMUP_DAYS=15; EVAL_DAYS=30; LATENCIES=[100,250]
GRID_MS=100; STALE_BINS=10; RET500_BINS=5; RET1S_BINS=10; TARGET_BINS=20
TRAIN_STRIDE=10; RIDGE=100.0; LEADER_LOOKBACK_DAYS=10
MIN_BYBIT_LEAD_CORR=0.02; MIN_LEAD_ADVANTAGE=0.01
MIN_BYBIT_MOVE_BP=8.0; MIN_GAP_BP=5.0; PRED_HURDLE_BP=12.0; DEOVERLAP_MS=3000
ENTRY_TIMEOUT_MS=500; HOLD_MS=2000; EXIT_TIMEOUT_MS=500
FEE_RT_BP=10.0; STRESS_RT_BP=12.0
UA={'User-Agent':'Mozilla/5.0'}; P=6

def dates_from(start,n):
    return [d.strftime('%Y-%m-%d') for d in pd.date_range(start,periods=n,freq='D')]

def get_bytes(url):
    for k in range(4):
        try:
            r=requests.get(url,timeout=60,headers=UA)
            if r.status_code==200 and len(r.content)>100:return r.content
            if r.status_code==404:return None
        except Exception: pass
        time.sleep(.5*(k+1))
    return None

def binance_url(sym,day):
    return f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'

def bybit_url(sym,day):
    return f'https://public.bybit.com/trading/{sym}/{sym}{day}.csv.gz'

def read_binance(blob):
    if blob is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            d=pd.read_csv(z.open(name),header=None,low_memory=False)
        if d.shape[1]<7:return None
        px=pd.to_numeric(d.iloc[:,1],errors='coerce'); ts=pd.to_numeric(d.iloc[:,5],errors='coerce')
        x=pd.DataFrame({'ts':ts,'price':px}).dropna()
        x=x[(x.price>0)&(x.ts>1_500_000_000_000)&(x.ts<2_000_000_000_000)]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def read_bybit(blob):
    if blob is None:return None
    try:
        d=pd.read_csv(io.BytesIO(gzip.decompress(blob)),low_memory=False)
        cols={str(c).strip().lower():c for c in d.columns}
        pc=cols.get('price'); tc=cols.get('timestamp')
        if pc is None or tc is None:return None
        px=pd.to_numeric(d[pc],errors='coerce'); ts=pd.to_numeric(d[tc],errors='coerce')
        med=float(ts.dropna().median()) if ts.notna().any() else np.nan
        if not np.isfinite(med):return None
        if med<10_000_000_000:ts=ts*1000.0
        x=pd.DataFrame({'ts':ts,'price':px}).dropna()
        x=x[(x.price>0)&(x.ts>1_500_000_000_000)&(x.ts<2_000_000_000_000)]
        x['ts']=x.ts.round().astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def grid_prices(raw,start_ms):
    if raw is None or len(raw)<100:return None
    b=(raw.ts.to_numpy(np.int64)//GRID_MS)*GRID_MS; p=raw.price.to_numpy(float)
    s=pd.Series(p,index=b).groupby(level=0).last()
    idx=np.arange(start_ms,start_ms+86_400_000,GRID_MS,dtype=np.int64)
    return s.reindex(idx).ffill(limit=STALE_BINS).to_numpy(float)

def safe_corr(a,b):
    m=np.isfinite(a)&np.isfinite(b)
    if m.sum()<1000:return np.nan
    aa=a[m]; bb=b[m]
    if np.std(aa)<1e-12 or np.std(bb)<1e-12:return np.nan
    return float(np.corrcoef(aa,bb)[0,1])

def build_day_arrays(binp,bybp):
    if binp is None or bybp is None:return None
    n=len(binp); lb=np.log(binp); ly=np.log(bybp)
    def lagret(x,k):
        z=np.full(n,np.nan); z[k:]=(x[k:]-x[:-k])*1e4; return z
    def futret(x,k):
        z=np.full(n,np.nan); z[:-k]=(x[k:]-x[:-k])*1e4; return z
    b500=lagret(lb,RET500_BINS); y500=lagret(ly,RET500_BINS)
    b1=lagret(lb,RET1S_BINS); y1=lagret(ly,RET1S_BINS)
    fb500=futret(lb,RET500_BINS); fy500=futret(ly,RET500_BINS); target=futret(lb,TARGET_BINS)
    gap=y500-b500
    X=np.column_stack([np.ones(n),y500/10.0,b500/10.0,gap/10.0,y1/10.0,b1/10.0])
    return X,target,b500,y500,gap,fb500,fy500

def fit_beta(xtx,xty):
    reg=np.eye(P)*RIDGE; reg[0,0]=0.0
    try:return np.linalg.solve(xtx+reg,xty)
    except Exception:return np.linalg.pinv(xtx+reg)@xty

def update_train(xtx,xty,X,y):
    idx=np.arange(0,len(y),TRAIN_STRIDE); xx=X[idx]; yy=y[idx]
    m=np.isfinite(xx).all(axis=1)&np.isfinite(yy)
    if m.sum():
        xm=xx[m]; ym=yy[m]; xtx += xm.T@xm; xty += xm.T@ym
    return xtx,xty,int(m.sum())

def first_trade(ts,when,timeout_ms):
    j=np.searchsorted(ts,when,'left')
    if j>=len(ts) or ts[j]>when+timeout_ms:return None
    return j

def execute(raw,signal_ms,side,lat):
    if raw is None:return None
    ts=raw.ts.to_numpy(np.int64); px=raw.price.to_numpy(float)
    j=first_trade(ts,signal_ms+lat,ENTRY_TIMEOUT_MS)
    if j is None:return None
    entry_ts=int(ts[j]); entry=float(px[j]); k=first_trade(ts,entry_ts+HOLD_MS,EXIT_TIMEOUT_MS)
    if k is None:return None
    exit_ts=int(ts[k]); exitp=float(px[k]); gross=side*np.log(exitp/entry)*1e4
    return {'entry_ts':entry_ts,'exit_ts':exit_ts,'entry_px':entry,'exit_px':exitp,
            'gross_bp':gross,'net_fee_bp':gross-FEE_RT_BP,'net_stress_bp':gross-STRESS_RT_BP}

def trim_best5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(.05*len(x)))); return float(np.sort(x)[:-n].mean())

def summarize(block,lat,signals,rows,coverage,leader_diag):
    e=pd.DataFrame(rows)
    out={'block':block,'latency_ms':lat,'coverage':coverage,'signals':signals,'completed':len(e),
         'symbols':0,'completed_per_eval_day':len(e)/EVAL_DAYS,'gross_mean_bp':np.nan,'gross_median_bp':np.nan,
         'net_fee_mean_bp':np.nan,'net_fee_median_bp':np.nan,'net_fee_remove_best5_bp':np.nan,
         'net_stress_mean_bp':np.nan,'positive_day_frac':np.nan,'top_symbol_share':np.nan,
         'median_bybit_lead_corr':leader_diag,'pass':False}
    if len(e)==0:return out
    d=pd.to_datetime(e.entry_ts,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
    dm=e.assign(daykey=d).groupby('daykey').net_fee_bp.mean()
    out.update({'symbols':int(e.symbol.nunique()),'gross_mean_bp':float(e.gross_bp.mean()),
                'gross_median_bp':float(e.gross_bp.median()),'net_fee_mean_bp':float(e.net_fee_bp.mean()),
                'net_fee_median_bp':float(e.net_fee_bp.median()),'net_fee_remove_best5_bp':trim_best5(e.net_fee_bp),
                'net_stress_mean_bp':float(e.net_stress_bp.mean()),
                'positive_day_frac':float((dm>0).mean()) if len(dm) else np.nan,
                'top_symbol_share':float(e.symbol.value_counts(normalize=True).max())})
    core=(coverage>=.95 and len(e)>=600 and out['symbols']>=4 and out['completed_per_eval_day']>=20 and
          out['net_fee_mean_bp']>2 and out['net_fee_median_bp']>0 and np.isfinite(out['net_fee_remove_best5_bp']) and
          out['net_fee_remove_best5_bp']>0 and out['net_stress_mean_bp']>0 and out['positive_day_frac']>=.60 and
          out['top_symbol_share']<=.35)
    if lat==100:out['pass']=bool(core)
    else:out['pass']=bool(coverage>=.95 and len(e)>=600 and out['symbols']>=4 and out['completed_per_eval_day']>=20 and
                          out['net_fee_mean_bp']>0 and out['net_fee_median_bp']>0 and
                          np.isfinite(out['net_fee_remove_best5_bp']) and out['net_fee_remove_best5_bp']>0)
    return out

summary=[]; allrows=[]; diagrows=[]; covrows=[]
for block,start in BLOCKS.items():
    days=dates_from(start,BLOCK_DAYS)
    states={s:{'xtx':np.zeros((P,P)),'xty':np.zeros(P),'train_n':0,
               'lead_hist':deque(maxlen=LEADER_LOOKBACK_DAYS),'reverse_hist':deque(maxlen=LEADER_LOOKBACK_DAYS)} for s in SYMS}
    valid_pairs=0; signals_by_lat={lat:0 for lat in LATENCIES}; rows_by_lat={lat:[] for lat in LATENCIES}
    for di,day in enumerate(days):
        eval_day=di>=WARMUP_DAYS
        for sym in SYMS:
            bb=read_binance(get_bytes(binance_url(sym,day))); yb=read_bybit(get_bytes(bybit_url(sym,day)))
            start_ms=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)
            bp=grid_prices(bb,start_ms); yp=grid_prices(yb,start_ms)
            if bp is None or yp is None:
                covrows.append({'block':block,'day':day,'symbol':sym,'valid':False}); continue
            valid_pairs+=1; covrows.append({'block':block,'day':day,'symbol':sym,'valid':True})
            X,target,b500,y500,gap,fb500,fy500=build_day_arrays(bp,yp); st=states[sym]
            bylead=safe_corr(y500,fb500); reverse=safe_corr(b500,fy500)
            prior_bylead=float(np.nanmean(st['lead_hist'])) if len(st['lead_hist']) else np.nan
            prior_reverse=float(np.nanmean(st['reverse_hist'])) if len(st['reverse_hist']) else np.nan
            leader_ok=(eval_day and len(st['lead_hist'])>=LEADER_LOOKBACK_DAYS and np.isfinite(prior_bylead) and
                       np.isfinite(prior_reverse) and prior_bylead>=MIN_BYBIT_LEAD_CORR and
                       prior_bylead-prior_reverse>=MIN_LEAD_ADVANTAGE and st['train_n']>100000)
            beta=fit_beta(st['xtx'],st['xty']) if st['train_n'] else np.zeros(P)
            if eval_day:
                pred=X@beta; idx=np.where(np.isfinite(pred)&np.isfinite(y500)&np.isfinite(b500)&np.isfinite(gap))[0]
                last_sig=-10**18; candidates=[]
                if leader_ok:
                    for j in idx:
                        side=1 if pred[j]>0 else -1
                        if abs(pred[j])<PRED_HURDLE_BP or abs(y500[j])<MIN_BYBIT_MOVE_BP or abs(gap[j])<MIN_GAP_BP:continue
                        if np.sign(y500[j])!=side or np.sign(gap[j])!=side:continue
                        signal_ms=start_ms+j*GRID_MS+GRID_MS
                        if signal_ms-last_sig<DEOVERLAP_MS:continue
                        candidates.append((signal_ms,side,float(pred[j]),float(y500[j]),float(b500[j]),float(gap[j]))); last_sig=signal_ms
                for lat in LATENCIES:
                    signals_by_lat[lat]+=len(candidates)
                    for signal_ms,side,predbp,yr,br,g in candidates:
                        z=execute(bb,signal_ms,side,lat)
                        if z is None:continue
                        rec={'block':block,'day':day,'symbol':sym,'latency_ms':lat,'side':side,'pred_bp':predbp,
                             'bybit_ret500_bp':yr,'binance_ret500_bp':br,'gap_bp':g,
                             'prior_bybit_lead_corr':prior_bylead,'prior_reverse_lead_corr':prior_reverse,**z}
                        rows_by_lat[lat].append(rec); allrows.append(rec)
                diagrows.append({'block':block,'day':day,'symbol':sym,'leader_ok':leader_ok,
                                 'prior_bybit_lead_corr':prior_bylead,'prior_reverse_lead_corr':prior_reverse,
                                 'today_bybit_lead_corr':bylead,'today_reverse_lead_corr':reverse,
                                 'beta_bybit500':float(beta[1]),'beta_gap':float(beta[3])})
            st['xtx'],st['xty'],nadd=update_train(st['xtx'],st['xty'],X,target); st['train_n']+=nadd
            if np.isfinite(bylead):st['lead_hist'].append(bylead)
            if np.isfinite(reverse):st['reverse_hist'].append(reverse)
        print(block,'day',di+1,'/',BLOCK_DAYS,day,'eval',eval_day,'signals100',signals_by_lat[100],flush=True)
    coverage=valid_pairs/(BLOCK_DAYS*len(SYMS))
    db=pd.DataFrame([d for d in diagrows if d['block']==block]); enabled=db[db.leader_ok] if len(db) else pd.DataFrame()
    medlead=float(enabled.prior_bybit_lead_corr.median()) if len(enabled) else np.nan
    for lat in LATENCIES:summary.append(summarize(block,lat,signals_by_lat[lat],rows_by_lat[lat],coverage,medlead))

pd.DataFrame(allrows).to_csv(f'{OUT}/trades.csv',index=False)
pd.DataFrame(diagrows).to_csv(f'{OUT}/leader_diagnostics.csv',index=False)
pd.DataFrame(covrows).to_csv(f'{OUT}/coverage.csv',index=False)
s=pd.DataFrame(summary); s.to_csv(f'{OUT}/summary.csv',index=False)
print('\n# Strategy #16 Cross-Venue Same-Asset Price Discovery\n')
print(tabulate(s,headers='keys',tablefmt='pipe',floatfmt='.3f',showindex=False))
print('\nFrozen: Bybit leadership must be established from strictly prior 10 days; per-symbol ridge uses strictly prior-day data.')
print('Signal: 100ms grid, Bybit 500ms move >=8bp, Bybit-Binance gap >=5bp, predicted Binance 2s catch-up >=12bp; trade Binance only.')
print('Execution: first Binance aggTrade after +100ms/+250ms, fixed 2s hold, taker/taker fee-only 10bp, stress 12bp.')
print('\nOVERALL:', 'PASS' if len(s) and s['pass'].all() else 'REJECT_OR_REDESIGN')
