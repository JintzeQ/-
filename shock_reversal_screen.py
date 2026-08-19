import io, zipfile, requests, math, os
import numpy as np
import pandas as pd

# Strategy #2A: broad, deliberately coarse discovery screen.
# No execution/fill assumptions here: this stage asks whether the gross event edge is large enough
# to justify a later tick-level execution study under a 5bp/side taker fee.
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','SEIUSDT','TIAUSDT','WIFUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
MONTHS=pd.period_range('2025-01','2026-06',freq='M').astype(str).tolist()
HORIZONS=[1,2,3,5,10,15]
SHOCKS=[50,75,100]
VOLS=[2.0,3.0]
OUT='shock_reversal_output'; os.makedirs(OUT,exist_ok=True)
BASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'

def dl(s,m):
    u=BASE.format(s=s,m=m)
    r=requests.get(u,timeout=45)
    if r.status_code!=200:return None
    try:
      z=zipfile.ZipFile(io.BytesIO(r.content)); f=z.namelist()[0]
      x=pd.read_csv(z.open(f),header=None)
      # Binance kline: open time, O,H,L,C,volume,close time,quote vol,trades,taker buy base,taker buy quote,...
      x=x.iloc[:,:11]; x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
      for c in ['ts','o','h','l','c','qv','tbq']:x[c]=pd.to_numeric(x[c],errors='coerce')
      x=x.dropna(subset=['ts','c','qv','tbq']); x['ts']=x.ts.astype('int64'); x['month']=m
      return x[['ts','c','qv','tbq','month']]
    except Exception:return None

def load(s):
    a=[]
    for m in MONTHS:
      x=dl(s,m)
      if x is not None and len(x):a.append(x)
    if not a:return None
    x=pd.concat(a,ignore_index=True).sort_values('ts').drop_duplicates('ts')
    x['r1']=np.log(x.c).diff()*1e4
    x['r3']=np.log(x.c/x.c.shift(3))*1e4
    x['flow']=(2*x.tbq-x.qv)/(x.qv+1e-9)
    x['volbase']=x.qv.rolling(120,min_periods=60).median().shift(1)
    x['volx']=x.qv/(x.volbase+1e-9)
    return x

print('loading BTC/ETH')
b=load('BTCUSDT'); e=load('ETHUSDT')
if b is None or e is None: raise RuntimeError('missing BTC/ETH benchmark data')
bench=b[['ts','r3']].rename(columns={'r3':'btc3'}).merge(e[['ts','r3']].rename(columns={'r3':'eth3'}),on='ts',how='inner')
rows=[]; events=[]
for si,s in enumerate(SYMS):
  print('symbol',si+1,len(SYMS),s,flush=True)
  x=load(s)
  if x is None:continue
  x=x.merge(bench,on='ts',how='left')
  # equal-weight benchmark residual; robust enough for discovery, no fitted beta leakage.
  x['resid3']=x.r3-0.5*x.btc3-0.5*x.eth3
  x['prevflow']=x.flow.rolling(3,min_periods=2).mean().shift(1)
  # Exhaustion: prior 3m flow strongly aligned with shock, current minute materially less extreme.
  for h in HORIZONS:x[f'f{h}']=np.log(x.c.shift(-h)/x.c)*1e4
  for shock in SHOCKS:
    for vx in VOLS:
      neg=(x.resid3<=-shock)&(x.volx>=vx)&(x.prevflow<=-0.35)&(x.flow>=x.prevflow+0.20)
      pos=(x.resid3>= shock)&(x.volx>=vx)&(x.prevflow>= 0.35)&(x.flow<=x.prevflow-0.20)
      idx=np.flatnonzero((neg|pos).fillna(False).to_numpy())
      if len(idx)==0:continue
      # de-overlap: one event per symbol per 5 minutes
      keep=[]; last=-10**9
      for i in idx:
        if i-last>=5:keep.append(i);last=i
      for i in keep:
        side=1 if bool(neg.iloc[i]) else -1
        d={'symbol':s,'ts':int(x.ts.iloc[i]),'month':x.month.iloc[i],'shock_bp':shock,'volx_thr':vx,'side':side,'resid3':float(x.resid3.iloc[i]),'volx':float(x.volx.iloc[i]),'flow_prev':float(x.prevflow.iloc[i]),'flow_now':float(x.flow.iloc[i])}
        for h in HORIZONS:d[f'r{h}_bp']=side*float(x[f'f{h}'].iloc[i])
        events.append(d)
E=pd.DataFrame(events)
if E.empty: raise RuntimeError('no events')
E['date']=pd.to_datetime(E.ts,unit='ms',utc=True)
E['split']=np.where(E.date<'2025-07-01','train',np.where(E.date<'2026-01-01','validation','oos'))
E.to_csv(f'{OUT}/events.csv',index=False)
for (shock,vx,split),g in E.groupby(['shock_bp','volx_thr','split']):
  d={'shock_bp':shock,'volx_thr':vx,'split':split,'events':len(g),'symbols':g.symbol.nunique(),'months':g.month.nunique()}
  for h in HORIZONS:
    v=g[f'r{h}_bp'].dropna(); d[f'mean{h}']=v.mean(); d[f'median{h}']=v.median(); d[f'win{h}']=(v>0).mean(); d[f'p10_{h}']=v.quantile(.1)
  rows.append(d)
R=pd.DataFrame(rows);R.to_csv(f'{OUT}/summary.csv',index=False)
# Train selection: maximize 3m mean subject to >=100 events and >=10 symbols; freeze threshold, report all splits.
tr=R[(R.split=='train')&(R.events>=100)&(R.symbols>=10)].sort_values('mean3',ascending=False)
choice=None if tr.empty else tr.iloc[0]
lines=['# Strategy #2A shock/exhaustion reversal screen','',f'Universe requested: {len(SYMS)} USD-M perpetuals; months {MONTHS[0]}..{MONTHS[-1]}.','Residual = coin 3m return - 0.5 BTC 3m - 0.5 ETH 3m. No fitted beta, avoiding estimation leakage.','Signal: |3m residual| >= 50/75/100bp, quote-volume >=2x/3x trailing 120m median, prior 3m aggressor flow aligned with shock, latest 1m flow exhausts by >=0.20.','Events de-overlapped by 5m per symbol. Train=2025H1, validation=2025H2, frozen OOS=2026H1. This is gross-alpha discovery; no execution assumptions.','']
if choice is None: lines+=['No train cell met minimum breadth/count.']
else:
  sh=float(choice.shock_bp);vx=float(choice.volx_thr);lines += [f'Train-selected threshold: shock={sh:.0f}bp, vol>={vx:.1f}x','',R[(R.shock_bp==sh)&(R.volx_thr==vx)].to_markdown(index=False,floatfmt='.3f')]
  oo=R[(R.shock_bp==sh)&(R.volx_thr==vx)&(R.split=='oos')]
  if len(oo):
    z=oo.iloc[0]; verdict='PASS_TO_TICK' if (z.mean3>=20 and z.median3>10 and z.events>=100 and z.symbols>=10) else 'REJECT_OR_REDESIGN'
    lines += ['',f'Verdict: **{verdict}** (gate: OOS mean3>=20bp, median3>10bp, >=100 events, >=10 symbols).']
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
