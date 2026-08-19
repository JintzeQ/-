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
        # Binance Vision metrics files have changed delimiter/format across history.
        # Detect the delimiter from raw bytes rather than assuming comma.
        tabs=head.count('\t'); commas=head.count(','); semis=head.count(';')
        sep='\t' if tabs>commas and tabs>=semis else (';' if semis>commas else ',')
        x=pd.read_csv(io.BytesIO(raw),sep=sep)
        expected=['create_time','symbol','sum_open_interest','sum_open_interest_value',
                  'count_toptrader_long_short_ratio','sum_toptrader_long_short_ratio',
                  'count_long_short_ratio','sum_taker_long_short_vol_ratio']
        x.columns=[str(c).strip() for c in x.columns]
        if not set(expected).issubset(set(x.columns)):
            x=pd.read_csv(io.BytesIO(raw),sep=sep,header=None)
            if x.shape[1] < 8:
                print('metrics schema unresolved',s,d,'sep',repr(sep),'shape',x.shape,'head',repr(head[:250]),flush=True)
                return None
            x=x.iloc[:,:8].copy(); x.columns=expected
            # If the first row was actually a header, numeric coercion below will discard it.
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
            x['dt']=pd.to_datetime(ct.astype(str).str.strip(),utc=True,errors='coerce')
        x=x.dropna(subset=['dt']+num)
        if x.empty:
            print('metrics parsed empty',s,d,'sep',repr(sep),'head',repr(head[:250]),flush=True)
            return None
        x['ts']=(x.dt.astype('int64')//1_000_000).astype('int64')
        return x[['ts','dt']+num]
    except Exception as exc:
        print('bad robust metrics',s,d,exc,flush=True)
        return None

m.parse_metrics_file=parse_metrics_file_robust
m.main()
