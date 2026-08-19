import io, os, zipfile
import numpy as np
import pandas as pd
import crowding_unwind_screen as m

# Strategy #5A-v3 CORE CROWDING — pre-performance data-availability revision.
# No valid #5 return/event statistics existed before this revision.
# Binance 2022 metrics availability diagnostics established:
#   - sum_open_interest_value: available through 2022
#   - count_long_short_ratio (global positioning): available through 2022
#   - sum_taker_long_short_vol_ratio: null after 2022-02-19
#   - top-trader ratios: null from about 2022-07-18
# Therefore v3 uses only historically stable fields plus kline-reconstructed taker flow.
# Dates, universe, premium/OI/global/taker thresholds, TRAIN-only grid, 60m horizon,
# de-overlap, splits and OOS gates are unchanged. The top-trader filter alone is removed.


def parse_metrics_file_v3(s,d):
    p=m.mpath(s,d)
    if not os.path.exists(p):
        return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            raw=z.read(name)
        head=raw[:4096].decode('utf-8','replace')
        tabs=head.count('\t'); commas=head.count(','); semis=head.count(';')
        sep='\t' if tabs>commas and tabs>=semis else (';' if semis>commas else ',')
        expected=['create_time','symbol','sum_open_interest','sum_open_interest_value',
                  'count_toptrader_long_short_ratio','sum_toptrader_long_short_ratio',
                  'count_long_short_ratio','sum_taker_long_short_vol_ratio']
        x=pd.read_csv(io.BytesIO(raw),sep=sep)
        x.columns=[str(c).strip() for c in x.columns]
        if not set(expected).issubset(set(x.columns)):
            x=pd.read_csv(io.BytesIO(raw),sep=sep,header=None)
            if x.shape[1] < 8:
                print('metrics schema unresolved',s,d,'shape',x.shape,flush=True)
                return None
            x=x.iloc[:,:8].copy(); x.columns=expected
        num=['sum_open_interest','sum_open_interest_value','count_toptrader_long_short_ratio',
             'sum_toptrader_long_short_ratio','count_long_short_ratio','sum_taker_long_short_vol_ratio']
        for c in num:
            x[c]=pd.to_numeric(x[c],errors='coerce')
        ct=x['create_time']; nct=pd.to_numeric(ct,errors='coerce')
        if nct.notna().mean()>0.8:
            med=nct.dropna().median()
            unit='ns' if med>1e17 else ('us' if med>1e14 else ('ms' if med>1e11 else 's'))
            x['dt']=pd.to_datetime(nct,unit=unit,utc=True,errors='coerce')
        else:
            x['dt']=pd.to_datetime(ct.astype(str).str.strip(),format='mixed',utc=True,errors='coerce')
        # Only stable, actually-used metrics fields are mandatory in v3.
        core=['sum_open_interest_value','count_long_short_ratio']
        y=x.dropna(subset=['dt']+core).copy()
        if y.empty:
            diag={c:int(x[c].notna().sum()) for c in ['dt']+num}
            print('metrics v3 parsed empty',s,d,'rows',len(x),'nonnull',diag,flush=True)
            return None
        y['ts']=(y.dt.astype('int64')//1_000_000).astype('int64')
        return y[['ts','dt']+num]
    except Exception as exc:
        print('bad v3 metrics',s,d,exc,flush=True)
        return None


def load_kline_v3(s):
    parts=[]
    for mon in m.MONTHS:
        p=m.kpath(s,mon)
        if not os.path.exists(p):
            continue
        try:
            with zipfile.ZipFile(p) as z:
                name=next(n for n in z.namelist() if n.endswith('.csv'))
                x=pd.read_csv(z.open(name),header=None)
            if x.shape[1] < 11:
                print('kline v3 too few columns',s,mon,x.shape,flush=True)
                continue
            x=x.iloc[:,:11].copy()
            x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
            for c in ['ts','c','qv','tbq']:
                x[c]=pd.to_numeric(x[c],errors='coerce')
            x=x.dropna(subset=['ts','c','qv','tbq'])
            if len(x) and x.ts.median()>1e14:
                x['ts']=np.floor(x.ts.to_numpy(dtype='float64')/1000.0)
            x['ts']=x.ts.astype('int64')
            parts.append(x[['ts','c','qv','tbq']])
        except Exception as exc:
            print('bad v3 kline',s,mon,exc,flush=True)
    if not parts:
        return None
    x=pd.concat(parts,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['dt']=pd.to_datetime(x.ts,unit='ms',utc=True)
    sell=(x.qv-x.tbq).clip(lower=0)
    x['taker_ratio_proxy']=x.tbq/(sell+1e-12)
    return x[['ts','dt','c','qv','tbq','taker_ratio_proxy']]


def build_symbol_frame_v3(s):
    K=load_kline_v3(s); P=m.load_premium(s); M,cov=m.load_metrics(s)
    if K is None or P is None or M is None:
        return None,cov
    kp=K.merge(P[['ts','pclose']],on='ts',how='inner')
    x=kp.merge(M.drop(columns='dt'),on='ts',how='inner')
    if x.empty:
        # Diagnostic only; no temporal realignment is performed automatically.
        print('EMPTY EXACT JOIN',s,
              'K first',K.ts.head(3).tolist(),
              'P first',P.ts.head(3).tolist(),
              'M first',M.ts.head(3).tolist(),
              'K mod5m',sorted(set((K.ts.head(100)%300000).tolist()))[:5],
              'M mod5m',sorted(set((M.ts.head(100)%300000).tolist()))[:5],flush=True)
        return None,cov
    x=x.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['symbol']=s
    x['premium_bp']=x.pclose*1e4
    x['premium_z']=m.past_z(x.premium_bp)
    x['ret30_bp']=np.log(x.c/x.c.shift(6))*1e4
    x['oi_growth_1h_pct']=np.log(x.sum_open_interest_value/x.sum_open_interest_value.shift(12))*100
    x['global_ratio']=x.count_long_short_ratio
    x['taker_ratio']=x.taker_ratio_proxy
    # Keep diagnostics present when historical data exists, but v3 event logic never uses them.
    x['top_pos']=x.sum_toptrader_long_short_ratio
    x['top_acct']=x.count_toptrader_long_short_ratio
    for mins,bars in m.HORIZONS.items():
        x[f'fwd{mins}_bp']=np.log(x.c.shift(-bars)/x.c)*1e4
    return x,cov


def event_mask_v3(x,zthr,oithr,extbp):
    long_crowd=(
        (x.premium_z>=zthr) & (x.oi_growth_1h_pct>=oithr) & (x.ret30_bp>=extbp) &
        (x.global_ratio>=m.GLOBAL_RATIO) & (x.taker_ratio>=m.TAKER_RATIO)
    )
    short_crowd=(
        (x.premium_z<=-zthr) & (x.oi_growth_1h_pct>=oithr) & (x.ret30_bp<=-extbp) &
        (x.global_ratio<=1/m.GLOBAL_RATIO) & (x.taker_ratio<=1/m.TAKER_RATIO)
    )
    return long_crowd,short_crowd

m.parse_metrics_file=parse_metrics_file_v3
m.load_kline=load_kline_v3
m.build_symbol_frame=build_symbol_frame_v3
m.event_mask=event_mask_v3
print('Strategy #5A-v3 Core Crowding: stable 2022 fields only; top-trader filter removed pre-performance; kline taker buy/sell quote ratio retained.',flush=True)
m.main()
