import io, os, time, zipfile, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #14: USD-M vs COIN-M same-underlying perpetual dislocation convergence.
# Frozen before outcomes. Stage 1 gross/fee-only screen; no tick execution unless both blocks pass.
OUT='dual_perp_convergence_output'; os.makedirs(OUT,exist_ok=True)
PAIRS={
 'BTC':('BTCUSDT','BTCUSD_PERP'),'ETH':('ETHUSDT','ETHUSD_PERP'),
 'BNB':('BNBUSDT','BNBUSD_PERP'),'XRP':('XRPUSDT','XRPUSD_PERP'),
 'ADA':('ADAUSDT','ADAUSD_PERP'),'DOGE':('DOGEUSDT','DOGEUSD_PERP'),
 'LINK':('LINKUSDT','LINKUSD_PERP'),'LTC':('LTCUSDT','LTCUSD_PERP')}
BLOCKS={'BLOCK_A_2023':2023,'BLOCK_B_2026':2026}
N_DAYS=30
LOOKBACK=360; MINHIST=240
DEV_BP=12.0; Z_THR=3.0; EXTEND_BP=3.0; VOLX=.5
DEOVERLAP_MIN=5; HOLD_MIN=5
FEE_ONLY_BP=10.0
UA={'User-Agent':'Mozilla/5.0'}

def sample_days(year,block):
    end=f'{year}-06-30' if year==2026 else f'{year}-12-31'
    days=pd.date_range(f'{year}-01-01',end,freq='D',tz='UTC')
    arr=[]
    for d in days:
        s=d.strftime('%Y-%m-%d'); h=hashlib.sha256(f'S14|{block}|{s}'.encode()).hexdigest(); arr.append((h,s))
    return [s for _,s in sorted(arr)[:N_DAYS]]

def url(kind,sym,day):
    return f'https://data.binance.vision/data/futures/{kind}/daily/klines/{sym}/1m/{sym}-1m-{day}.zip'

def get(url):
    for k in range(4):
        try:
            r=requests.get(url,headers=UA,timeout=45)
            if r.status_code==200 and len(r.content)>100:return r.content
            if r.status_code==404:return None
        except Exception:pass
        time.sleep(.4*(k+1))
    return None

def read(blob):
    if blob is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            n=next(x for x in z.namelist() if x.endswith('.csv')); d=pd.read_csv(z.open(n),header=None,low_memory=False)
        if d.shape[1]<12:return None
        x=pd.DataFrame({'ts':pd.to_numeric(d.iloc[:,0],errors='coerce'),'o':pd.to_numeric(d.iloc[:,1],errors='coerce'),'c':pd.to_numeric(d.iloc[:,4],errors='coerce'),'vol':pd.to_numeric(d.iloc[:,7],errors='coerce')})
        x=x.dropna(); x=x[(x.o>0)&(x.c>0)&(x.vol>=0)]
        if len(x) and x.ts.median()>1e14:x['ts']=np.floor(x.ts/1000.0)
        x['ts']=x.ts.astype('int64'); return x.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    except Exception:return None

def load_pair_day(asset,day):
    um,cm=PAIRS[asset]
    return asset,read(get(url('um',um,day))),read(get(url('cm',cm,day)))

def events_for(asset,day,u,c):
    if u is None or c is None or len(u)<800 or len(c)<800:return []
    z=u.merge(c,on='ts',suffixes=('_u','_c')).sort_values('ts').reset_index(drop=True)
    if len(z)<800:return []
    z['basis']=np.log(z.c_c/z.c_u)*1e4
    z['med']=z.basis.rolling(LOOKBACK,min_periods=MINHIST).median().shift(1)
    z['sd']=z.basis.rolling(LOOKBACK,min_periods=MINHIST).std().shift(1)
    z['dev']=z.basis-z.med; z['zs']=z.dev/(z.sd+1e-9)
    z['uvmed']=z.vol_u.rolling(LOOKBACK,min_periods=MINHIST).median().shift(1); z['cvmed']=z.vol_c.rolling(LOOKBACK,min_periods=MINHIST).median().shift(1)
    z['uvx']=z.vol_u/(z.uvmed+1e-12); z['cvx']=z.vol_c/(z.cvmed+1e-12)
    z['extend']=np.sign(z.dev)*(z.dev-z.dev.shift(2))
    out=[]; last=-10**18
    for i,r in z.iterrows():
        if i+1+HOLD_MIN>=len(z):continue
        if not (np.isfinite(r.dev) and np.isfinite(r.zs) and np.isfinite(r.extend)):continue
        if abs(r.dev)<DEV_BP or abs(r.zs)<Z_THR or r.extend<EXTEND_BP or r.uvx<VOLX or r.cvx<VOLX:continue
        ts=int(r.ts)
        if ts-last<DEOVERLAP_MIN*60000:continue
        entry=z.iloc[i+1]; exitr=z.iloc[i+1+HOLD_MIN]
        side_cm=-1 if r.dev>0 else 1; side_um=-side_cm
        ru=np.log(float(exitr.o_u)/float(entry.o_u))*1e4; rc=np.log(float(exitr.o_c)/float(entry.o_c))*1e4
        gross=.5*(side_um*ru+side_cm*rc)
        out.append({'block':'','day':day,'asset':asset,'signal_ts':ts,'dev_bp':float(r.dev),'z':float(r.zs),'basis_side':'CM_RICH' if r.dev>0 else 'CM_CHEAP','gross_bp':gross,'fee_only_bp':gross-FEE_ONLY_BP})
        last=ts
    return out

def trim_best5(x):
    a=np.sort(np.asarray(x,float)); n=max(1,int(np.ceil(len(a)*.05)))
    return float(a[:-n].mean()) if len(a)>n and len(a)>=20 else np.nan

def summarize(block,e,coverage):
    if len(e)==0:return {'block':block,'events':0,'assets':0,'coverage':coverage,'events_per_day':0,'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'remove_best5_bp':np.nan,'fee_only_mean_bp':np.nan,'positive_day_frac':np.nan,'top_asset_share':np.nan,'rich_mean_bp':np.nan,'cheap_mean_bp':np.nan,'pass':False}
    x=pd.DataFrame(e); dm=x.groupby('day').gross_bp.mean(); side=x.groupby('basis_side').gross_bp.mean()
    row={'block':block,'events':len(x),'assets':x.asset.nunique(),'coverage':coverage,'events_per_day':len(x)/N_DAYS,'gross_mean_bp':x.gross_bp.mean(),'gross_median_bp':x.gross_bp.median(),'remove_best5_bp':trim_best5(x.gross_bp),'fee_only_mean_bp':x.fee_only_bp.mean(),'positive_day_frac':(dm>0).mean(),'top_asset_share':x.asset.value_counts(normalize=True).max(),'rich_mean_bp':side.get('CM_RICH',np.nan),'cheap_mean_bp':side.get('CM_CHEAP',np.nan)}
    row['pass']=bool(row['coverage']>=.95 and row['events']>=600 and row['assets']>=6 and row['events_per_day']>=20 and row['gross_mean_bp']>=15 and row['gross_median_bp']>5 and row['remove_best5_bp']>0 and row['fee_only_mean_bp']>2 and row['positive_day_frac']>=.60 and row['top_asset_share']<=.25 and np.isfinite(row['rich_mean_bp']) and np.isfinite(row['cheap_mean_bp']) and row['rich_mean_bp']>0 and row['cheap_mean_bp']>0)
    return row

summ=[]; allrows=[]; covrows=[]
for block,year in BLOCKS.items():
    days=sample_days(year,block); pd.DataFrame({'day':days}).to_csv(f'{OUT}/{block}_days.csv',index=False)
    valid=0; expected=len(days)*len(PAIRS); ev=[]
    for di,day in enumerate(days,1):
        with ThreadPoolExecutor(max_workers=8) as ex:
            results=[f.result() for f in as_completed([ex.submit(load_pair_day,a,day) for a in PAIRS])]
        ds=0
        for a,u,c in results:
            if u is not None and c is not None and len(u)>=800 and len(c)>=800:
                valid+=1; ds+=1; q=events_for(a,day,u,c)
                for r in q:r['block']=block
                ev.extend(q)
        print(block,'day',di,'/',len(days),day,'valid_pairs',ds,'events_so_far',len(ev),flush=True)
    coverage=valid/expected; covrows.append({'block':block,'valid_pair_days':valid,'expected_pair_days':expected,'coverage':coverage})
    allrows.extend(ev); summ.append(summarize(block,ev,coverage))

s=pd.DataFrame(summ); pd.DataFrame(allrows).to_csv(f'{OUT}/events.csv',index=False); pd.DataFrame(covrows).to_csv(f'{OUT}/coverage.csv',index=False); s.to_csv(f'{OUT}/summary.csv',index=False)
print('\n# Strategy #14 USD-M vs COIN-M Dual-Perp Convergence\n'); print(s.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen: own-basis 6h median/std, |dev|>=12bp, |z|>=3, 2m extension>=3bp, both venues vol>=0.5x trailing median, next-minute open entry, 5m hold, 5m de-overlap.')
print('Pair return normalized to 100% gross notional (50/50 legs); fee-only hurdle=10bp. No spread/slippage/latency yet.')
verdict='PASS_TO_TICK_EXECUTION' if len(s)==2 and bool(s['pass'].all()) else 'REJECT_OR_REDESIGN'
print('\nOVERALL:',verdict); open(f'{OUT}/verdict.txt','w').write(verdict+'\n')