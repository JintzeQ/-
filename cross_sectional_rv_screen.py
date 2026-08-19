import io, os, time, zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #7 frozen specification: cross-sectional market-neutral relative value.
# Two untouched blocks for this alpha family: 2020-09..2020-12 and 2023-01..2023-05.
# Every 30m, rank liquid symbols using 50% own premium-dislocation z + 50% cross-sectional 30m return z.
# Require premium deviation and relative return to have the same sign. Long 2 cheapest, short 2 richest,
# equal gross notional (50% long / 50% short), hold 30m, then fully exit. No overlapping holdings.
# Trigger only if >=10 valid symbols, >=2 aligned negative + >=2 aligned positive names,
# long/short premium-deviation gap >=6bp and composite-score gap >=2.0.
# Per-block gate frozen before outcomes: >=500 portfolio events; median universe >=10; mean30 >=20bp;
# median30 >5bp; remove-best-5% mean >0; >=75% positive-mean months; top symbol participation <=20%;
# fee-only mean after 10bp RT >=5bp; and both long/short relative contributions >=0.
# BOTH blocks must pass to proceed to tick execution.

BLOCKS={
 'BLOCK_A_2020Q4':('2020-09-01','2021-01-01'),
 'BLOCK_B_2023H1':('2023-01-01','2023-06-01'),
}
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','BCHUSDT','LINKUSDT','DOTUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','EOSUSDT','XLMUSDT','UNIUSDT','AAVEUSDT','SUSHIUSDT','YFIUSDT','FILUSDT']
OUT='cross_sectional_rv_output'; CACHE='cross_sectional_rv_cache'; os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)
KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/5m/{s}-5m-{m}.zip'
PBASE='https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/{s}/5m/{s}-5m-{m}.zip'
UA={'User-Agent':'Mozilla/5.0'}

# Frozen signal/gate constants.
PREM_ABS_BP=3.0; PREM_GAP_BP=6.0; SCORE_GAP=2.0; MIN_UNIVERSE=10
FEE_RT_BP=10.0; HOLD_BARS=6; REBAL_MINUTES={0,30}

def months_between(a,b):
    x=pd.Timestamp(a,tz='UTC').to_period('M'); z=(pd.Timestamp(b,tz='UTC')-pd.Timedelta(days=1)).to_period('M')
    return pd.period_range(x,z,freq='M').astype(str).tolist()

def download(url,path,min_size=200):
    if os.path.exists(path) and os.path.getsize(path)>=min_size:return True,'cache'
    msg='failed'
    for k in range(4):
        try:
            r=requests.get(url,headers=UA,timeout=45)
            if r.status_code==200 and len(r.content)>=min_size:
                open(path,'wb').write(r.content); return True,'download'
            if r.status_code==404:return False,'404'
            msg=f'http{r.status_code}'
        except Exception as e:msg=repr(e)
        time.sleep(.5*(k+1))
    return False,msg

def path(kind,s,m):return f'{CACHE}/{kind}-{s}-{m}.zip'

def read_zip(kind,s,m):
    p=path(kind,s,m)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            x=pd.read_csv(z.open(name),header=None)
        x=x.iloc[:,:12]; x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq','ignore']
        for c in ['ts','c','qv']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','c'])
        if len(x) and x.ts.median()>1e14:x['ts']=np.floor(x.ts/1000.0)
        x['ts']=x.ts.astype('int64')
        if kind=='p':return x[['ts','c']].rename(columns={'c':'premium'})
        return x[['ts','c','qv']]
    except Exception as e:
        print('BADZIP',kind,s,m,repr(e),flush=True); return None

def load_symbol(s,months):
    ks=[]; ps=[]
    for m in months:
        k=read_zip('k',s,m); p=read_zip('p',s,m)
        if k is not None and len(k):ks.append(k)
        if p is not None and len(p):ps.append(p)
    if not ks or not ps:return None
    k=pd.concat(ks,ignore_index=True).drop_duplicates('ts').sort_values('ts')
    p=pd.concat(ps,ignore_index=True).drop_duplicates('ts').sort_values('ts')
    d=pd.merge(k,p,on='ts',how='inner').sort_values('ts').reset_index(drop=True)
    if len(d)<200:return None
    d['prem_med']=d.premium.rolling(72,min_periods=48).median().shift(1)
    d['prem_std']=d.premium.rolling(72,min_periods=48).std().shift(1)
    d['prem_dev']=d.premium-d.prem_med
    d['prem_z']=d.prem_dev/(d.prem_std+1e-9)
    d['ret30']=np.log(d.c/d.c.shift(6))
    d['qvmed']=d.qv.rolling(72,min_periods=48).median().shift(1)
    d['liqratio']=d.qv/(d.qvmed+1e-12)
    d['fwd30_bp']=np.log(d.c.shift(-HOLD_BARS)/d.c)*1e4
    d['symbol']=s
    return d[['ts','symbol','prem_dev','prem_z','ret30','liqratio','fwd30_bp']]

def robust(x):
    x=np.sort(np.asarray(x,float)); cut=max(1,int(np.ceil(len(x)*.05)))
    return float(np.mean(x[:-cut])) if len(x)>cut else np.nan

def block_run(name,a,b):
    months=months_between(a,b); jobs=[]
    for s in SYMS:
        for m in months:
            jobs.append(('k',s,m,KBASE.format(s=s,m=m),path('k',s,m)))
            jobs.append(('p',s,m,PBASE.format(s=s,m=m),path('p',s,m)))
    miss=[]
    def one(j):
        kind,s,m,u,p=j; ok,msg=download(u,p); return kind,s,m,ok,msg
    with ThreadPoolExecutor(max_workers=24) as ex:
        fs=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fs),1):
            kind,s,m,ok,msg=f.result()
            if not ok:miss.append((kind,s,m,msg))
            if n%100==0:print(name,'archives',n,'/',len(jobs),'missing',len(miss),flush=True)
    print(name,'archive coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)

    parts=[]
    for i,s in enumerate(SYMS,1):
        d=load_symbol(s,months)
        if d is not None:
            lo=int(pd.Timestamp(a,tz='UTC').timestamp()*1000); hi=int(pd.Timestamp(b,tz='UTC').timestamp()*1000)
            d=d[(d.ts>=lo)&(d.ts<hi)]
            if len(d):parts.append(d)
        print(name,'prepared',i,'/',len(SYMS),s,'rows',0 if d is None else len(d),flush=True)
    if not parts:raise RuntimeError(f'{name}: no valid data')
    panel=pd.concat(parts,ignore_index=True)
    panel=panel[np.isfinite(panel.prem_z)&np.isfinite(panel.ret30)&np.isfinite(panel.fwd30_bp)&np.isfinite(panel.liqratio)]
    dt=pd.to_datetime(panel.ts,unit='ms',utc=True); panel=panel[(dt.dt.minute.isin(REBAL_MINUTES))&(dt.dt.second==0)]

    events=[]; sel=Counter(); universes=[]
    for ts,g in panel.groupby('ts',sort=True):
        g=g[g.liqratio>=0.5].copy()
        if len(g)<MIN_UNIVERSE:continue
        universes.append(len(g))
        mu=g.ret30.mean(); sd=g.ret30.std(ddof=1)
        if not np.isfinite(sd) or sd<=1e-12:continue
        g['ret_z']=(g.ret30-mu)/sd
        g['score']=0.5*np.clip(g.prem_z,-5,5)+0.5*np.clip(g.ret_z,-5,5)
        g['prem_bp']=g.prem_dev*1e4
        aligned=g[(np.sign(g.prem_z)==np.sign(g.ret_z))&(g.prem_bp.abs()>=PREM_ABS_BP)]
        neg=aligned[(aligned.prem_z<0)&(aligned.ret_z<0)].nsmallest(2,'score')
        pos=aligned[(aligned.prem_z>0)&(aligned.ret_z>0)].nlargest(2,'score')
        if len(neg)<2 or len(pos)<2:continue
        premgap=pos.prem_bp.mean()-neg.prem_bp.mean(); scoregap=pos.score.mean()-neg.score.mean()
        if premgap<PREM_GAP_BP or scoregap<SCORE_GAP:continue
        market=g.fwd30_bp.mean(); longret=neg.fwd30_bp.mean(); shortret=pos.fwd30_bp.mean()
        gross=0.5*(longret-shortret)
        longrel=0.5*(longret-market); shortrel=0.5*(market-shortret)
        for s in list(neg.symbol)+list(pos.symbol):sel[s]+=1
        events.append({'block':name,'ts':int(ts),'universe':len(g),'gross30_bp':gross,'feeonly30_bp':gross-FEE_RT_BP,'long_rel_bp':longrel,'short_rel_bp':shortrel,'premium_gap_bp':premgap,'score_gap':scoregap,'long_symbols':'|'.join(neg.symbol),'short_symbols':'|'.join(pos.symbol)})
    e=pd.DataFrame(events)
    if e.empty:return e,{'block':name,'events':0,'symbols':0,'median_universe':np.nan,'mean30_bp':np.nan,'median30_bp':np.nan,'remove_best5_mean_bp':np.nan,'positive_month_frac':np.nan,'top_symbol_share':np.nan,'feeonly_mean_bp':np.nan,'long_rel_mean_bp':np.nan,'short_rel_mean_bp':np.nan,'pass':False},miss
    e['month']=pd.to_datetime(e.ts,unit='ms',utc=True).dt.strftime('%Y-%m')
    month=e.groupby('month').gross30_bp.mean(); posmonth=float((month>=0).mean())
    top_share=max(sel.values())/(4*len(e)) if sel else np.nan
    symbols=len(sel)
    row={'block':name,'events':len(e),'symbols':symbols,'median_universe':float(e.universe.median()),'mean30_bp':e.gross30_bp.mean(),'median30_bp':e.gross30_bp.median(),'win30':float((e.gross30_bp>0).mean()),'p10_bp':e.gross30_bp.quantile(.1),'p90_bp':e.gross30_bp.quantile(.9),'remove_best5_mean_bp':robust(e.gross30_bp),'positive_month_frac':posmonth,'top_symbol_share':top_share,'feeonly_mean_bp':e.feeonly30_bp.mean(),'long_rel_mean_bp':e.long_rel_bp.mean(),'short_rel_mean_bp':e.short_rel_bp.mean()}
    row['pass']=bool(row['events']>=500 and row['median_universe']>=10 and row['mean30_bp']>=20 and row['median30_bp']>5 and row['remove_best5_mean_bp']>0 and row['positive_month_frac']>=.75 and row['top_symbol_share']<=.20 and row['feeonly_mean_bp']>=5 and row['long_rel_mean_bp']>=0 and row['short_rel_mean_bp']>=0)
    return e,row,miss

summ=[]; all_events=[]; all_miss=[]
for name,(a,b) in BLOCKS.items():
    e,row,miss=block_run(name,a,b); summ.append(row); all_miss += [(name,*x) for x in miss]
    if len(e):all_events.append(e)
if all_events:pd.concat(all_events,ignore_index=True).to_csv(f'{OUT}/events.csv',index=False)
pd.DataFrame(all_miss,columns=['block','kind','symbol','month','reason']).to_csv(f'{OUT}/missing.csv',index=False)
s=pd.DataFrame(summ); s.to_csv(f'{OUT}/summary.csv',index=False)
overall=bool(len(s)==2 and s['pass'].all())
print('\n# Strategy #7 Cross-Sectional Relative Value\n'); print(s.to_markdown(index=False,floatfmt='.3f')); print('\nOVERALL:', 'PASS_TO_TICK' if overall else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_TICK' if overall else 'REJECT_OR_REDESIGN')+'\n')
