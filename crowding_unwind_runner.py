import io, os, zipfile
import numpy as np
import pandas as pd
import crowding_unwind_screen as m

# Parser-only hotfix. No Strategy #5 dates, universe, features, thresholds, horizon, selection, or OOS gates change.
def parse_metrics_file_robust(s,d):
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
                print('metrics schema unresolved',s,d,'sep',repr(sep),'shape',x.shape,'head',repr(head[:300]),flush=True)
                return None
            x=x.iloc[:,:8].copy(); x.columns=expected

        num=['sum_open_interest','sum_open_interest_value','count_toptrader_long_short_ratio',
             'sum_toptrader_long_short_ratio','count_long_short_ratio','sum_taker_long_short_vol_ratio']
        for c in num:
            x[c]=pd.to_numeric(x[c],errors='coerce')

        ct=x['create_time']
        nct=pd.to_numeric(ct,errors='coerce')
        if nct.notna().mean() > 0.8:
            med=nct.dropna().median()
            if med>1e17: unit='ns'
            elif med>1e14: unit='us'
            elif med>1e11: unit='ms'
            else: unit='s'
            x['dt']=pd.to_datetime(nct,unit=unit,utc=True,errors='coerce')
        else:
            # Historical metrics use human-readable timestamps; pandas 3.x is stricter
            # about mixed string formats, so parse each file explicitly as mixed UTC.
            x['dt']=pd.to_datetime(ct.astype(str).str.strip(),format='mixed',utc=True,errors='coerce')

        # Only fields actually used by the frozen signal are mandatory. Historical files
        # can have gaps in unused sum-OI/top-account fields; those must not delete valid
        # OI-value / top-position / global-position / taker-ratio observations.
        core=['sum_open_interest_value','sum_toptrader_long_short_ratio',
              'count_long_short_ratio','sum_taker_long_short_vol_ratio']
        y=x.dropna(subset=['dt']+core).copy()
        if y.empty:
            diag={c:int(x[c].notna().sum()) for c in ['dt']+num}
            print('metrics parsed empty',s,d,'rows',len(x),'nonnull',diag,'head',repr(head[:300]),flush=True)
            return None
        y['ts']=(y.dt.astype('int64')//1_000_000).astype('int64')
        return y[['ts','dt']+num]
    except Exception as exc:
        print('bad robust metrics',s,d,exc,flush=True)
        return None

m.parse_metrics_file=parse_metrics_file_robust
m.main()
