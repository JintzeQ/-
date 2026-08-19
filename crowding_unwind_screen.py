import os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

# Strategy #5 pre-execution discovery: materially different crowding/OI alpha family.
# Freeze before seeing outcomes: 2024-01..2024-12, 5m bars; use Binance USD-M public
# open-interest history plus price/volume. Signal is positioning-price divergence:
# crowded build = 1h OI +8% while price moves >=2% in same direction and 5m volume >=2x
# trailing median; enter reversal at signal bar close; horizons 15/30/60m.
# Gate to justify tick work: >=100 events, >=10 symbols, primary 30m gross mean >=20bp,
# median >0, remove-best-5% mean >0, and both sides individually non-negative mean.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
START='2024-01-01'; END='2025-01-01'; OUT='crowding_unwind_output'; os.makedirs(OUT,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0'})

def get_json(url,params):
    for k in range(4):
        try:
            r=S.get(url,params=params,timeout=30)
            if r.status_code==200:return r.json()
            if r.status_code in (400,404):return []
        except Exception: pass
        time.sleep(.4*(k+1))
    return []

def klines(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(pd.Timestamp(END,tz='UTC').timestamp()*1000); rows=[]
    while start<end:
        x=get_json('https://fapi.binance.com/fapi/v1/klines',{'symbol':sym,'interval':'5m','startTime':start,'endTime':end-1,'limit':1500})
        if not x:break
        rows.extend(x); start=int(x[-1][0])+300000
    if not rows:return None
    d=pd.DataFrame(rows,columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq','x'])
    for c in ['ts','c','qv']:d[c]=pd.to_numeric(d[c],errors='coerce')
    return d[['ts','c','qv']].dropna().drop_duplicates('ts').sort_values('ts')

def oi(sym):
    # Binance public OI history endpoint allows bounded windows; walk 30d chunks.
    a=pd.Timestamp(START,tz='UTC'); z=pd.Timestamp(END,tz='UTC'); rows=[]
    while a<z:
        b=min(a+pd.Timedelta(days=29),z)
        x=get_json('https://fapi.binance.com/futures/data/openInterestHist',{'symbol':sym,'period':'5m','startTime':int(a.timestamp()*1000),'endTime':int(b.timestamp()*1000)-1,'limit':500})
        # Endpoint limit may truncate; paginate within chunk.
        while x:
            rows.extend(x); last=int(x[-1]['timestamp']); nxt=pd.Timestamp(last+300000,unit='ms',tz='UTC')
            if nxt>=b or len(x)<500:break
            x=get_json('https://fapi.binance.com/futures/data/openInterestHist',{'symbol':sym,'period':'5m','startTime':last+1,'endTime':int(b.timestamp()*1000)-1,'limit':500})
        a=b
    if not rows:return None
    d=pd.DataFrame(rows); d['ts']=pd.to_numeric(d.timestamp,errors='coerce'); d['oi']=pd.to_numeric(d.sumOpenInterestValue,errors='coerce')
    return d[['ts','oi']].dropna().drop_duplicates('ts').sort_values('ts')

def one(sym):
    k=klines(sym); o=oi(sym)
    if k is None or o is None:return sym,None
    d=pd.merge_asof(k,o,on='ts',direction='nearest',tolerance=60000).dropna().reset_index(drop=True)
    d['r1h']=np.log(d.c/d.c.shift(12)); d['oi1h']=d.oi/d.oi.shift(12)-1
    d['volmed']=d.qv.rolling(288,min_periods=144).median().shift(1); d['volx']=d.qv/(d.volmed+1e-12)
    aligned=np.sign(d.r1h)==np.sign(d.oi1h)
    sig=(d.oi1h.abs()>=.08)&(d.r1h.abs()>=.02)&(d.volx>=2)&aligned
    idx=np.where(sig)[0]; keep=[]; last=-999
    for i in idx:
        if i-last>=12:keep.append(i); last=i
    out=[]
    for i in keep:
        if i+12>=len(d):continue
        side=-np.sign(d.r1h.iloc[i])
        row={'symbol':sym,'ts':int(d.ts.iloc[i]),'side':int(side),'oi1h':d.oi1h.iloc[i],'r1h':d.r1h.iloc[i],'volx':d.volx.iloc[i]}
        for h,n in [(15,3),(30,6),(60,12)]:row[f'gross_{h}m_bp']=side*np.log(d.c.iloc[i+n]/d.c.iloc[i])*1e4
        out.append(row)
    return sym,pd.DataFrame(out)

parts=[]
with ThreadPoolExecutor(max_workers=6) as ex:
    fs=[ex.submit(one,s) for s in ALTS]
    for n,f in enumerate(as_completed(fs),1):
        s,x=f.result(); print('symbol',n,'/',len(ALTS),s,'events',0 if x is None else len(x),flush=True)
        if x is not None and len(x):parts.append(x)
if not parts:raise RuntimeError('no Strategy #5 events/data')
e=pd.concat(parts,ignore_index=True).sort_values('ts'); e.to_csv(f'{OUT}/events.csv',index=False)

def robust(x):
    x=np.asarray(x,float); cut=max(1,int(np.ceil(len(x)*.05))); return np.mean(np.sort(x)[:-cut]) if len(x)>cut else np.nan
rows=[]
for h in [15,30,60]:
    x=e[f'gross_{h}m_bp']; rows.append({'horizon_min':h,'events':len(e),'symbols':e.symbol.nunique(),'mean_bp':x.mean(),'median_bp':x.median(),'win':(x>0).mean(),'remove_best5_mean_bp':robust(x)})
sumdf=pd.DataFrame(rows); sumdf.to_csv(f'{OUT}/summary.csv',index=False)
side=e.groupby('side').agg(events=('symbol','size'),symbols=('symbol','nunique'),mean30=('gross_30m_bp','mean'),median30=('gross_30m_bp','median')).reset_index(); side.to_csv(f'{OUT}/side.csv',index=False)
r=sumdf[sumdf.horizon_min==30].iloc[0]; sideok=(len(side)==2 and (side.mean30>=0).all())
passed=bool(r.events>=100 and r.symbols>=10 and r.mean_bp>=20 and r.median_bp>0 and r.remove_best5_mean_bp>0 and sideok)
print('\n# Strategy #5 Crowding/OI Unwind Gross Screen\n'); print(sumdf.to_markdown(index=False,floatfmt='.3f')); print('\nSides\n',side.to_markdown(index=False,floatfmt='.3f')); print('\nVerdict:', 'PASS_TO_EXECUTION' if passed else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write('PASS_TO_EXECUTION\n' if passed else 'REJECT_OR_REDESIGN\n')