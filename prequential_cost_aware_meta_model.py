import io, os, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

OUT='prequential_meta_output'; os.makedirs(OUT,exist_ok=True)
SYMS=['BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','LINKUSDT']
BLOCKS={
 'BLOCK_A_2022_AUGSEP':('2022-08-01',45),
 'BLOCK_B_2024_OCTNOV':('2024-10-01',45),
}
WARMUP_DAYS=15
EVAL_DAYS=30
DECISION_STEP_SEC=5
HORIZON_SEC=10
LATENCIES=[100,250]
RIDGE_LAMBDA=100.0
PRED_HURDLE_BP=12.0
FEE_BP=10.0
STRESS_BP=12.0
DEOVERLAP_SEC=10
UA={'User-Agent':'Mozilla/5.0'}

# Fixed feature scales, frozen before outcomes.
FEATURES=['r1','r5','r10','imb1','imb5','imb10','volshock','rv30','resid5','mkt5']
SCALES=np.array([5.,10.,15.,1.,1.,1.,2.,10.,10.,10.],float)


def day_url(sym,day):
    return f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'

def getzip(url):
    for k in range(4):
        try:
            r=requests.get(url,timeout=75,headers=UA)
            if r.status_code==200 and len(r.content)>100:return r.content
            if r.status_code==404:return None
        except Exception:pass
        time.sleep(.5*(k+1))
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
        bm=d.iloc[:,6].astype(str).str.lower().isin(['true','1'])
        x=pd.DataFrame({'ts':ts,'price':px,'qty':qty,'buyer_maker':bm}).dropna()
        x=x[(x.price>0)&(x.qty>0)]
        x['ts']=x.ts.astype('int64')
        x=x[(x.ts>1_500_000_000_000)&(x.ts<2_000_000_000_000)]
        x['signed_quote']=np.where(x.buyer_maker,-1.0,1.0)*x.price*x.qty
        x['quote']=x.price*x.qty
        return x[['ts','price','signed_quote','quote']].sort_values('ts').reset_index(drop=True)
    except Exception:return None

def load_day(sym,day):
    return sym,read_agg(getzip(day_url(sym,day)))

def to_1s(raw,day):
    if raw is None or len(raw)<100:return None
    x=raw.copy(); x['sec']=(x.ts//1000)*1000
    g=x.groupby('sec',sort=True).agg(price=('price','last'),sq=('signed_quote','sum'),q=('quote','sum'))
    start=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)
    idx=np.arange(start,start+86400000,1000,dtype='int64')
    g=g.reindex(idx)
    g['price']=g.price.ffill(); g[['sq','q']]=g[['sq','q']].fillna(0.0)
    return g

def build_day_dataset(rawmap,day):
    bars={s:to_1s(rawmap.get(s),day) for s in SYMS}
    if any(bars[s] is None for s in SYMS):return None
    idx=bars[SYMS[0]].index
    prices=pd.DataFrame({s:bars[s].price for s in SYMS},index=idx)
    r1=np.log(prices/prices.shift(1))*1e4
    r5=np.log(prices/prices.shift(5))*1e4
    r10=np.log(prices/prices.shift(10))*1e4
    fut=np.log(prices.shift(-HORIZON_SEC)/prices)*1e4
    out={}
    for s in SYMS:
        b=bars[s]
        q1=b.q; sq1=b.sq
        q5=q1.rolling(5,min_periods=5).sum(); sq5=sq1.rolling(5,min_periods=5).sum()
        q10=q1.rolling(10,min_periods=10).sum(); sq10=sq1.rolling(10,min_periods=10).sum()
        imb1=sq1/(q1+1e-9); imb5=sq5/(q5+1e-9); imb10=sq10/(q10+1e-9)
        medq=q5.rolling(300,min_periods=120).median().shift(1)
        volshock=np.log((q5+1.0)/(medq+1.0)).clip(-6,6)
        rv30=r1[s].rolling(30,min_periods=20).std()
        others=[x for x in SYMS if x!=s]
        mkt5=r5[others].median(axis=1)
        resid5=r5[s]-mkt5
        f=pd.DataFrame({
          'r1':r1[s],'r5':r5[s],'r10':r10[s],
          'imb1':imb1,'imb5':imb5,'imb10':imb10,
          'volshock':volshock,'rv30':rv30,'resid5':resid5,'mkt5':mkt5,
          'y':fut[s]
        },index=idx)
        take=((f.index//1000)%DECISION_STEP_SEC)==0
        f=f.loc[take].replace([np.inf,-np.inf],np.nan).dropna()
        if len(f):out[s]=f
    return out

def design(df,sym):
    z=df[FEATURES].to_numpy(float)/SCALES
    intercept=np.ones((len(df),1))
    # 5 symbol dummies; BNB is baseline. No symbol is a privileged market factor.
    dummies=np.zeros((len(df),len(SYMS)-1))
    if sym in SYMS[1:]:dummies[:,SYMS[1:].index(sym)]=1.0
    return np.hstack([intercept,z,dummies])

def solve_ridge(xtx,xty):
    p=xtx.shape[0]
    pen=np.eye(p)*RIDGE_LAMBDA; pen[0,0]=0.0
    # Do not penalize symbol intercept dummies strongly.
    for j in range(1+len(FEATURES),p):pen[j,j]=10.0
    try:return np.linalg.solve(xtx+pen,xty)
    except Exception:return np.linalg.pinv(xtx+pen)@xty

def first_trade(raw,when,timeout_ms=1000):
    ts=raw.ts.to_numpy(np.int64); j=np.searchsorted(ts,when,'left')
    if j>=len(ts) or ts[j]>when+timeout_ms:return None
    return j

def execute(raw,signal_ms,side,lat):
    j=first_trade(raw,signal_ms+lat,1000)
    if j is None:return None
    ts=raw.ts.to_numpy(np.int64); px=raw.price.to_numpy(float)
    entry_ts=int(ts[j]); entry=float(px[j])
    k=first_trade(raw,entry_ts+HORIZON_SEC*1000,1000)
    if k is None:return None
    exit_px=float(px[k]); gross=side*np.log(exit_px/entry)*1e4
    return {'entry_ts':entry_ts,'exit_ts':int(ts[k]),'entry_px':entry,'exit_px':exit_px,
            'gross_bp':gross,'net_fee_bp':gross-FEE_BP,'net_stress_bp':gross-STRESS_BP}

def trim_best5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(.05*len(x))))
    return float(np.sort(x)[:-n].mean())

def summarize(block,lat,trades,coverage,diag):
    e=pd.DataFrame(trades)
    row={'block':block,'latency_ms':lat,'coverage':coverage,'oos_samples':diag['n'],
         'oos_ic':diag['ic'],'sign_accuracy':diag['sign_acc'],'signals':diag['signals'],
         'completed':len(e),'symbols':0,'completed_per_eval_day':len(e)/EVAL_DAYS,
         'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_fee_mean_bp':np.nan,
         'net_fee_median_bp':np.nan,'net_fee_remove_best5_bp':np.nan,'net_stress_mean_bp':np.nan,
         'positive_day_frac':np.nan,'top_symbol_share':np.nan,'pass':False}
    if len(e)==0:return row
    dt=pd.to_datetime(e.entry_ts,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
    dm=e.assign(daykey=dt).groupby('daykey').net_fee_bp.mean()
    row.update({
      'symbols':int(e.symbol.nunique()),'gross_mean_bp':float(e.gross_bp.mean()),
      'gross_median_bp':float(e.gross_bp.median()),'net_fee_mean_bp':float(e.net_fee_bp.mean()),
      'net_fee_median_bp':float(e.net_fee_bp.median()),'net_fee_remove_best5_bp':trim_best5(e.net_fee_bp),
      'net_stress_mean_bp':float(e.net_stress_bp.mean()),
      'positive_day_frac':float((dm>0).mean()) if len(dm) else np.nan,
      'top_symbol_share':float(e.symbol.value_counts(normalize=True).max())
    })
    if lat==100:
        row['pass']=bool(coverage>=.95 and len(e)>=600 and row['symbols']==len(SYMS)
          and row['completed_per_eval_day']>=20 and row['net_fee_mean_bp']>2
          and row['net_fee_median_bp']>0 and row['net_fee_remove_best5_bp']>0
          and row['net_stress_mean_bp']>0 and row['positive_day_frac']>=.60
          and row['top_symbol_share']<=.30)
    else:
        row['pass']=bool(coverage>=.95 and len(e)>=600 and row['symbols']==len(SYMS)
          and row['completed_per_eval_day']>=20 and row['net_fee_mean_bp']>0
          and row['net_fee_median_bp']>0 and row['net_fee_remove_best5_bp']>0)
    return row

summary=[]; all_trades=[]; coef_rows=[]
for block,(start,n_days) in BLOCKS.items():
    days=[d.strftime('%Y-%m-%d') for d in pd.date_range(start,periods=n_days,freq='D',tz='UTC')]
    p=1+len(FEATURES)+(len(SYMS)-1)
    xtx=np.zeros((p,p)); xty=np.zeros(p)
    valid_symbol_days=0; expected=n_days*len(SYMS)
    trades_by_lat={lat:[] for lat in LATENCIES}
    pred_all=[]; y_all=[]; signal_count=0
    last_signal={lat:{s:-10**18 for s in SYMS} for lat in LATENCIES}
    for di,day in enumerate(days):
        rawmap={}
        with ThreadPoolExecutor(max_workers=6) as ex:
            fut={ex.submit(load_day,s,day):s for s in SYMS}
            for f in as_completed(fut):
                try:s,raw=f.result()
                except Exception:s,raw=fut[f],None
                if raw is not None and len(raw)>=100:
                    rawmap[s]=raw; valid_symbol_days+=1
        ds=build_day_dataset(rawmap,day) if len(rawmap)==len(SYMS) else None
        is_eval=di>=WARMUP_DAYS
        if ds is not None and is_eval and xtx.sum()!=0:
            beta=solve_ridge(xtx,xty)
            coef_rows.append({'block':block,'day':day,**{f'b{i}':float(v) for i,v in enumerate(beta)}})
            for s,df in ds.items():
                X=design(df,s); pred=X@beta; y=df.y.to_numpy(float)
                pred_all.extend(pred.tolist()); y_all.extend(y.tolist())
                mask=np.abs(pred)>=PRED_HURDLE_BP
                if mask.any():
                    idxs=np.where(mask)[0]
                    signal_count+=len(idxs)
                    for ii in idxs:
                        signal_ms=int(df.index[ii]+1000)
                        side=1 if pred[ii]>0 else -1
                        for lat in LATENCIES:
                            if signal_ms-last_signal[lat][s]<DEOVERLAP_SEC*1000:continue
                            z=execute(rawmap[s],signal_ms,side,lat)
                            if z is None:continue
                            rec={'block':block,'day':day,'symbol':s,'signal_ms':signal_ms,
                                 'pred_bp':float(pred[ii]),'target_bar_bp':float(y[ii]),'side':side,
                                 'latency_ms':lat,**z}
                            trades_by_lat[lat].append(rec); all_trades.append(rec)
                            last_signal[lat][s]=signal_ms
        # Prequential update happens only after the day has been scored.
        if ds is not None:
            for s,df in ds.items():
                X=design(df,s); y=df.y.to_numpy(float)
                xtx+=X.T@X; xty+=X.T@y
        print(block,'day',di+1,'/',n_days,day,'valid',len(rawmap),'eval',is_eval,
              'signals_total',signal_count,flush=True)
        del rawmap,ds
    coverage=valid_symbol_days/expected
    pa=np.asarray(pred_all,float); ya=np.asarray(y_all,float)
    if len(pa)>10 and np.std(pa)>0 and np.std(ya)>0:ic=float(np.corrcoef(pa,ya)[0,1])
    else:ic=np.nan
    sign_acc=float((np.sign(pa)==np.sign(ya)).mean()) if len(pa) else np.nan
    diag={'n':len(pa),'ic':ic,'sign_acc':sign_acc,'signals':signal_count}
    for lat in LATENCIES:
        summary.append(summarize(block,lat,trades_by_lat[lat],coverage,diag))

s=pd.DataFrame(summary)
pd.DataFrame(all_trades).to_csv(f'{OUT}/trades.csv',index=False)
pd.DataFrame(coef_rows).to_csv(f'{OUT}/daily_coefficients.csv',index=False)
s.to_csv(f'{OUT}/summary.csv',index=False)
print('\n# Strategy #15 Prequential Cost-Aware Microstructure Meta-Model\n')
print(s.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen model: pooled ridge regression, strictly prior-day training only; 5s decision cadence; 10s target/hold; features are returns, aggressor imbalance, volume shock, realized vol, and endogenous cross-sectional residual/common factor.')
print('BTC/ETH are not privileged benchmarks and are not in the universe. Cost-aware gate requires |predicted 10s return| >= 12bp. Taker/taker fee-only cost=10bp; stress=12bp.')
print('Validation: each block is 45 consecutive days = 15 warmup + 30 prequential OOS evaluation days. No post-outcome tuning.')
print('\nOVERALL:', 'PASS' if len(s)==4 and bool(s['pass'].all()) else 'REJECT_OR_REDESIGN')
