import os, io, zipfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #8: Spot-Perp Basis Dislocation Convergence
# Economic direction is predeclared for implementability: only positive-basis cash-and-carry unwind
# (LONG spot + SHORT perp). Negative-basis reverse cash-and-carry requires spot borrow and is excluded.
# BLOCK_A is globally unused in this research chain; BLOCK_B is alpha-family-blind but has been used
# by other strategies, so conclusions must require BOTH blocks independently to pass.
BLOCKS={
 'BLOCK_A_2020JJA':('2020-06-01','2020-09-01'),
 'BLOCK_B_2026H1':('2026-01-01','2026-07-01'),
}
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','BCHUSDT','LINKUSDT','TRXUSDT','ETCUSDT','EOSUSDT','XLMUSDT','ATOMUSDT','DASHUSDT','ZECUSDT','XMRUSDT','COMPUSDT','SNXUSDT','MKRUSDT']
OUT='spot_perp_basis_output'; os.makedirs(OUT,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0'})

def months(a,b):
    x=pd.Timestamp(a).to_period('M'); z=(pd.Timestamp(b)-pd.Timedelta(days=1)).to_period('M')
    out=[]
    while x<=z: out.append(str(x)); x+=1
    return out

def getzip(url):
    for k in range(4):
        try:
            r=S.get(url,timeout=30)
            if r.status_code==200:return r.content
            if r.status_code==404:return None
        except Exception: pass
        time.sleep(.4*(k+1))
    return None

def read_kline(blob, market):
    if blob is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name=z.namelist()[0]
            d=pd.read_csv(z.open(name),header=None)
        if d.shape[1]<8:return None
        ts=pd.Series(pd.to_numeric(d.iloc[:,0],errors='coerce'),index=d.index,dtype='float64')
        # Binance Spot archive timestamps are microseconds from 2025 onward; normalize to ms.
        if market=='spot': ts=ts.where(ts<=1e14,ts/1000.0)
        out=pd.DataFrame({'ts':ts,'close':pd.to_numeric(d.iloc[:,4],errors='coerce'),'qv':pd.to_numeric(d.iloc[:,7],errors='coerce')}).dropna()
        out['ts']=out['ts'].round().astype('int64')
        return out.drop_duplicates('ts').sort_values('ts')
    except Exception:return None

def urls(sym,ym):
    return (
      f'https://data.binance.vision/data/spot/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip',
      f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip')

def load_symbol(sym,a,b):
    ss=[]; ff=[]; missing=[]
    for ym in months(a,b):
        su,fu=urls(sym,ym); sb=getzip(su); fb=getzip(fu)
        if sb is None or fb is None:
            missing.append(ym); continue
        sd=read_kline(sb,'spot'); fd=read_kline(fb,'fut')
        if sd is not None and fd is not None: ss.append(sd); ff.append(fd)
    if not ss or not ff:return None,missing
    s=pd.concat(ss).drop_duplicates('ts').sort_values('ts'); f=pd.concat(ff).drop_duplicates('ts').sort_values('ts')
    s=s.rename(columns={'close':'spot','qv':'spot_qv'}); f=f.rename(columns={'close':'perp','qv':'perp_qv'})
    d=s.merge(f,on='ts',how='inner')
    lo=int(pd.Timestamp(a,tz='UTC').timestamp()*1000); hi=int(pd.Timestamp(b,tz='UTC').timestamp()*1000)
    d=d[(d.ts>=lo)&(d.ts<hi)].reset_index(drop=True)
    return d,missing

def one_block(name,a,b):
    parts={}; miss=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs={ex.submit(load_symbol,s,a,b):s for s in SYMS}
        for n,f in enumerate(as_completed(fs),1):
            s=fs[f]; d,m=f.result(); miss += [(s,x) for x in m]
            if d is not None and len(d)>300: parts[s]=d
            print(name,n,'/',len(SYMS),s,'rows',0 if d is None else len(d),'missing_months',len(m),flush=True)
    ev=[]
    for s,d in parts.items():
        d=d.copy()
        d['basis_bp']=(d.perp/d.spot-1.0)*1e4
        med=d.basis_bp.rolling(288,min_periods=144).median().shift(1)
        sd=d.basis_bp.rolling(288,min_periods=144).std(ddof=0).shift(1)
        d['dev_bp']=d.basis_bp-med; d['z']=d.dev_bp/(sd+1e-9)
        d['expand15_bp']=d.basis_bp-d.basis_bp.shift(3)
        sm=d.spot_qv.rolling(288,min_periods=144).median().shift(1)
        fm=d.perp_qv.rolling(288,min_periods=144).median().shift(1)
        # Positive-basis cash-and-carry only: long spot + short perp.
        sig=(d.dev_bp>=15)&(d.z>=3)&(d.expand15_bp>=5)&(d.spot_qv>=0.5*sm)&(d.perp_qv>=0.5*fm)
        idx=np.where(sig)[0]; keep=[]; last=-999
        for i in idx:
            if i-last>=6: keep.append(i); last=i
        for i in keep:
            if i+6>=len(d):continue
            spot_r=np.log(d.spot.iloc[i+6]/d.spot.iloc[i])*1e4
            perp_r=np.log(d.perp.iloc[i+6]/d.perp.iloc[i])*1e4
            ev.append({'block':name,'symbol':s,'ts':int(d.ts.iloc[i]),'basis_bp':d.basis_bp.iloc[i],'dev_bp':d.dev_bp.iloc[i],'z':d.z.iloc[i],'gross30_spread_bp':spot_r-perp_r})
    e=pd.DataFrame(ev)
    if len(e): e['month']=pd.to_datetime(e.ts,unit='ms',utc=True).dt.strftime('%Y-%m')
    return e,miss,len(parts)

def trimmed(x):
    x=np.sort(np.asarray(x,float)); cut=max(1,int(np.ceil(len(x)*.05))); return np.mean(x[:-cut]) if len(x)>cut else np.nan

all_ev=[]; rows=[]
for name,(a,b) in BLOCKS.items():
    e,miss,avail=one_block(name,a,b)
    pd.DataFrame(miss,columns=['symbol','month']).to_csv(f'{OUT}/{name}_missing.csv',index=False)
    if e is None or not len(e):
        rows.append({'block':name,'events':0,'symbols':0,'mean30_bp':np.nan,'median30_bp':np.nan,'win30':np.nan,'remove_best5_mean_bp':np.nan,'positive_month_frac':np.nan,'top_symbol_share':np.nan,'fee20_mean_bp':np.nan,'fee30_stress_mean_bp':np.nan,'pass':False}); continue
    e.to_csv(f'{OUT}/{name}_events.csv',index=False); all_ev.append(e)
    x=e.gross30_spread_bp
    mon=e.groupby('month').gross30_spread_bp.mean(); top=e.symbol.value_counts(normalize=True).max()
    r={'block':name,'events':len(e),'symbols':e.symbol.nunique(),'mean30_bp':x.mean(),'median30_bp':x.median(),'win30':(x>0).mean(),'p10_bp':x.quantile(.1),'p90_bp':x.quantile(.9),'remove_best5_mean_bp':trimmed(x),'positive_month_frac':(mon>=0).mean(),'top_symbol_share':top,'fee20_mean_bp':x.mean()-20,'fee30_stress_mean_bp':x.mean()-30}
    r['pass']=bool(r['events']>=300 and r['symbols']>=8 and r['mean30_bp']>=35 and r['median30_bp']>15 and r['remove_best5_mean_bp']>0 and r['positive_month_frac']>=.60 and r['top_symbol_share']<=.20 and r['fee20_mean_bp']>=10 and r['fee30_stress_mean_bp']>0)
    rows.append(r)

summary=pd.DataFrame(rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_ev: pd.concat(all_ev,ignore_index=True).to_csv(f'{OUT}/events_all.csv',index=False)
print('\n# Strategy #8 Spot-Perp Basis Convergence\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFee conventions: gross spread bp is on matched one-leg notional. fee20 assumes 5bp/side on BOTH spot and perp (20bp total per round trip); fee30 stress assumes spot 10bp/side + perp 5bp/side (30bp total). Funding, borrow, spread/slippage/latency are excluded at this gross screen.\n')
verdict=bool(len(summary)==2 and summary['pass'].all())
print('OVERALL:', 'PASS_TO_EXECUTION' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_EXECUTION' if verdict else 'REJECT_OR_REDESIGN')+'\n')