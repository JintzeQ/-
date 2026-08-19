import os, zipfile, hashlib, time, gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import requests
import numpy as np
import pandas as pd

# Strategy #4B: frozen tick-level execution validation.
# IMPORTANT: this revision changes only archive parsing/resource usage.
# Dates, universe, alpha rule, sample, execution semantics, fees, latencies and gate are unchanged.
ALTS=['SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','TRXUSDT','ETCUSDT','ATOMUSDT','NEARUSDT','APTUSDT','ARBUSDT','OPUSDT','SUIUSDT','FILUSDT','INJUSDT','FETUSDT','GRTUSDT','GALAUSDT','CHZUSDT','WOOUSDT']
BENCH=['BTCUSDT','ETHUSDT']
MONTHS=pd.period_range('2023-06','2024-02',freq='M').astype(str).tolist()
HORIZON_MS=5*60*1000
LATENCIES=[0,100,250,500]
SAMPLE_PER_MONTH=20
MAX_QUOTE_LAG_MS=1000
MAX_TRADE_LAG_MS=1000
TAKER_FEE_SIDE_BP=5.0
SLIPPAGE_STRESS_SIDE_BP=[0.0,1.0,2.0]
SHOCK_BP=100.0
VOLX_THR=3.0
FLOW_PREV_THR=0.35
FLOW_NOW_THR=0.25
RESID1_EXTEND_BP=10.0
CHUNK=200_000

OUT='trapped_flow_reversal_tick_output'
CACHE='trapped_flow_reversal_tick_cache'
os.makedirs(OUT,exist_ok=True); os.makedirs(CACHE,exist_ok=True)
KBASE='https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip'
BBASE='https://data.binance.vision/data/futures/um/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip'
ABASE='https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'
UA={'User-Agent':'Mozilla/5.0'}

def get_url(url,path,min_size=200):
    if os.path.exists(path) and os.path.getsize(path)>=min_size:return True,'cache'
    msg='failed'
    for k in range(3):
        try:
            r=requests.get(url,timeout=60,headers=UA)
            if r.status_code==200 and len(r.content)>=min_size:
                with open(path,'wb') as f:f.write(r.content)
                return True,'download'
            if r.status_code==404:return False,'404'
            msg=f'http{r.status_code}'
        except Exception as exc:msg=str(exc)
        time.sleep(.5*(k+1))
    return False,msg

def kpath(s,m):return f'{CACHE}/kline-{s}-{m}.zip'
def bpath(s,d):return f'{CACHE}/book-{s}-{d}.zip'
def apath(s,d):return f'{CACHE}/agg-{s}-{d}.zip'

def prefetch_klines():
    jobs=[(s,m) for s in BENCH+ALTS for m in MONTHS];miss=[];ok=0
    def one(sm):
        s,m=sm;return s,m,*get_url(KBASE.format(s=s,m=m),kpath(s,m),500)
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fut),1):
            s,m,good,msg=f.result();ok+=int(good)
            if not good:miss.append((s,m,msg))
            if n%45==0:print('kline download',n,'/',len(jobs),'ok',ok,'miss',len(miss),flush=True)
    print('kline coverage',ok,'/',len(jobs),'missing',len(miss),flush=True)
    if miss:print('kline missing sample',miss[:20],flush=True)
    return miss

def read_kline_zip(s,m):
    p=kpath(s,m)
    if not os.path.exists(p):return None
    try:
        with zipfile.ZipFile(p) as z:
            names=[n for n in z.namelist() if n.endswith('.csv')]
            if not names:return None
            x=pd.read_csv(z.open(names[0]),header=None).iloc[:,:11]
        x.columns=['ts','o','h','l','c','v','ct','qv','n','tbv','tbq']
        for c in ['ts','ct','c','qv','tbq']:x[c]=pd.to_numeric(x[c],errors='coerce')
        x=x.dropna(subset=['ts','ct','c','qv','tbq'])
        for c in ['ts','ct']:
            if len(x) and x[c].median()>1e14:x[c]=(x[c]//1000)
            x[c]=x[c].astype('int64')
        x['month']=m
        return x[['ts','ct','c','qv','tbq','month']]
    except Exception as exc:
        print('bad kline',s,m,exc,flush=True);return None

def load_kline(s):
    parts=[]
    for m in MONTHS:
        x=read_kline_zip(s,m)
        if x is not None and len(x):parts.append(x)
    if not parts:return None
    x=pd.concat(parts,ignore_index=True).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    x['r1']=np.log(x.c/x.c.shift(1))*1e4;x['r3']=np.log(x.c/x.c.shift(3))*1e4
    x['flow']=(2*x.tbq-x.qv)/(x.qv+1e-12)
    x['flow_prev3']=x.flow.rolling(3,min_periods=2).mean().shift(1)
    x['volbase']=x.qv.rolling(120,min_periods=60).median().shift(1)
    x['volx']=x.qv/(x.volbase+1e-12)
    return x

def build_frozen_signals():
    print('loading BTC/ETH benchmarks',flush=True)
    b=load_kline('BTCUSDT');e=load_kline('ETHUSDT')
    if b is None or e is None:raise RuntimeError('benchmark kline missing')
    bench=b[['ts','r1','r3']].rename(columns={'r1':'btc1','r3':'btc3'}).merge(e[['ts','r1','r3']].rename(columns={'r1':'eth1','r3':'eth3'}),on='ts',how='inner')
    rows=[]
    for k,s in enumerate(ALTS,1):
        print('signal symbol',k,'/',len(ALTS),s,flush=True)
        x=load_kline(s)
        if x is None:continue
        x=x.merge(bench,on='ts',how='left')
        x['resid1']=x.r1-.5*x.btc1-.5*x.eth1;x['resid3']=x.r3-.5*x.btc3-.5*x.eth3
        x['f5']=np.log(x.c.shift(-5)/x.c)*1e4
        sign=np.sign(x.resid3)
        cond=(x.resid3.abs()>=SHOCK_BP)&(x.volx>=VOLX_THR)&((sign*x.flow_prev3)>=FLOW_PREV_THR)&((sign*x.flow)>=FLOW_NOW_THR)&((sign*x.resid1)>=RESID1_EXTEND_BP)
        idx=np.flatnonzero(cond.fillna(False).to_numpy());keep=[];last=-10**9
        for i in idx:
            if i-last>=5:keep.append(i);last=i
        for i in keep:
            shock=1 if x.resid3.iloc[i]>0 else -1;side=-shock;sig_close=int(x.ct.iloc[i])
            rows.append({'symbol':s,'month':str(x.month.iloc[i]),'signal_close_ts':sig_close,'signal_dt':pd.to_datetime(sig_close,unit='ms',utc=True),'shock_sign':shock,'trade_side':side,'resid1':float(x.resid1.iloc[i]),'resid3':float(x.resid3.iloc[i]),'volx':float(x.volx.iloc[i]),'flow_prev3':float(x.flow_prev3.iloc[i]),'flow_now':float(x.flow.iloc[i]),'close_gross5_bp':side*float(x.f5.iloc[i])})
        del x;gc.collect()
    E=pd.DataFrame(rows).sort_values(['signal_close_ts','symbol']).reset_index(drop=True)
    if E.empty:raise RuntimeError('no frozen signals')
    E.to_csv(f'{OUT}/all_signals.csv',index=False)
    print('frozen signals',len(E),'symbols',E.symbol.nunique(),'months',E.month.nunique(),flush=True)
    return E

def deterministic_sample(E):
    E=E.copy();E['sample_hash']=E.apply(lambda r:hashlib.sha256(f"{r.symbol}|{int(r.signal_close_ts)}".encode()).hexdigest(),axis=1)
    S=E.sort_values(['month','sample_hash']).groupby('month',group_keys=False).head(SAMPLE_PER_MONTH).sort_values('signal_close_ts').reset_index(drop=True)
    S.to_csv(f'{OUT}/tick_sample_signals.csv',index=False)
    print('tick sample',len(S),'symbols',S.symbol.nunique(),'months',S.month.nunique(),flush=True)
    print('sample gross close mean/median',S.close_gross5_bp.mean(),S.close_gross5_bp.median(),flush=True)
    return S

def utc_day(ms):return pd.to_datetime(ms,unit='ms',utc=True).strftime('%Y-%m-%d')

def build_requests(S):
    book_req=defaultdict(list);agg_req=defaultdict(list);reqdays=set()
    for ei,r in enumerate(S.itertuples()):
        for lat in LATENCIES:
            ent=int(r.signal_close_ts+lat);ex=int(r.signal_close_ts+HORIZON_MS+lat)
            ke=(ei,lat,'entry');kx=(ei,lat,'exit')
            book_req[(r.symbol,utc_day(ent))].append((ke,ent));book_req[(r.symbol,utc_day(ex))].append((kx,ex))
            agg_req[(r.symbol,utc_day(ent))].append((ke,ent))
            reqdays.add((r.symbol,utc_day(ent)));reqdays.add((r.symbol,utc_day(ex)))
    return book_req,agg_req,sorted(reqdays)

def prefetch_tick(reqdays):
    jobs=[]
    for s,d in reqdays:
        jobs += [('book',s,d,BBASE.format(s=s,d=d),bpath(s,d),500),('agg',s,d,ABASE.format(s=s,d=d),apath(s,d),300)]
    miss=[]
    def one(j):
        typ,s,d,u,p,minsz=j;good,msg=get_url(u,p,minsz);return typ,s,d,good,msg
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut=[ex.submit(one,j) for j in jobs]
        for n,f in enumerate(as_completed(fut),1):
            typ,s,d,good,msg=f.result()
            if not good:miss.append((typ,s,d,msg))
            if n%50==0:print('tick archive download',n,'/',len(jobs),'miss',len(miss),flush=True)
    print('tick archive coverage',len(jobs)-len(miss),'/',len(jobs),'missing',len(miss),flush=True)
    if miss:print('tick missing sample',miss[:30],flush=True)
    pd.DataFrame(miss,columns=['type','symbol','day','reason']).to_csv(f'{OUT}/tick_missing.csv',index=False)
    return miss

def norm_ms(a):
    a=np.asarray(a,dtype='float64');m=np.isfinite(a)&(a>1e14);a[m]=np.floor(a[m]/1000.0);return a

def scan_book_day(s,d,requests,out):
    p=bpath(s,d)
    if not os.path.exists(p):return
    req=sorted(requests,key=lambda x:x[1]);best={}
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            for chunk in pd.read_csv(z.open(name),header=None,usecols=[0,1,2,3,4,6],chunksize=CHUNK,low_memory=False):
                chunk.columns=['uid','bid','bidq','ask','askq','et']
                for c in chunk.columns:chunk[c]=pd.to_numeric(chunk[c],errors='coerce')
                chunk=chunk.dropna(subset=['uid','bid','bidq','ask','askq','et'])
                if chunk.empty:continue
                chunk['et']=norm_ms(chunk.et.to_numpy())
                et=chunk.et.to_numpy();uid=chunk.uid.to_numpy()
                for key,target in req:
                    mask=et>=target
                    if not mask.any():continue
                    mt=float(et[mask].min());same=np.flatnonzero(mask&(et==mt))
                    if len(same)==0:continue
                    j=same[np.argmin(uid[same])];row=chunk.iloc[int(j)]
                    cand=(int(row.et),int(row.uid),float(row.bid),float(row.bidq),float(row.ask),float(row.askq))
                    old=best.get(key)
                    if old is None or cand[:2]<old[:2]:best[key]=cand
                del chunk
        out.update(best)
    except Exception as exc:print('bad book scan',s,d,exc,flush=True)

def scan_agg_day(s,d,requests,out):
    p=apath(s,d)
    if not os.path.exists(p):return
    req=sorted(requests,key=lambda x:x[1]);best={}
    try:
        with zipfile.ZipFile(p) as z:
            name=next(n for n in z.namelist() if n.endswith('.csv'))
            for chunk in pd.read_csv(z.open(name),header=None,usecols=[5],chunksize=CHUNK,low_memory=False):
                t=pd.to_numeric(chunk.iloc[:,0],errors='coerce').dropna().to_numpy(dtype='float64')
                if not len(t):continue
                t=norm_ms(t)
                for key,target in req:
                    v=t[t>=target]
                    if len(v):
                        cand=int(v.min());old=best.get(key)
                        if old is None or cand<old:best[key]=cand
                del chunk
        out.update(best)
    except Exception as exc:print('bad agg scan',s,d,exc,flush=True)

def scan_all(book_req,agg_req):
    quotes={};trades={};keys=sorted(set(book_req)|set(agg_req))
    for n,(s,d) in enumerate(keys,1):
        if (s,d) in book_req:scan_book_day(s,d,book_req[(s,d)],quotes)
        if (s,d) in agg_req:scan_agg_day(s,d,agg_req[(s,d)],trades)
        if n%20==0:print('archive scanned',n,'/',len(keys),'quote hits',len(quotes),'trade hits',len(trades),flush=True)
        gc.collect()
    print('archive scan done',len(keys),'days quote hits',len(quotes),'trade hits',len(trades),flush=True)
    return quotes,trades

def evaluate(S,quotes,trades):
    rows=[]
    for ei,r in enumerate(S.itertuples()):
        for lat in LATENCIES:
            ent_target=int(r.signal_close_ts+lat);ex_target=int(r.signal_close_ts+HORIZON_MS+lat)
            be=quotes.get((ei,lat,'entry'));bx=quotes.get((ei,lat,'exit'));at=trades.get((ei,lat,'entry'))
            valid=be is not None and bx is not None
            entry_lag=np.nan if be is None else be[0]-ent_target;exit_lag=np.nan if bx is None else bx[0]-ex_target;trade_lag=np.nan if at is None else at-ent_target
            if valid and (entry_lag>MAX_QUOTE_LAG_MS or exit_lag>MAX_QUOTE_LAG_MS):valid=False
            if valid:
                _,_,bid,bidq,ask,askq=be;_,_,xbid,xbidq,xask,xaskq=bx
                if r.trade_side==1:entry=ask;exitp=xbid;entryq=askq;exitq=xbidq;gross=np.log(exitp/entry)*1e4
                else:entry=bid;exitp=xask;entryq=bidq;exitq=xaskq;gross=np.log(entry/exitp)*1e4
                esp=np.log(ask/bid)*1e4;xsp=np.log(xask/xbid)*1e4
            else:entry=exitp=entryq=exitq=gross=esp=xsp=np.nan
            base={'symbol':r.symbol,'month':r.month,'signal_close_ts':int(r.signal_close_ts),'trade_side':int(r.trade_side),'latency_ms':lat,'valid':bool(valid),'entry_target_ts':ent_target,'exit_target_ts':ex_target,'entry_quote_lag_ms':entry_lag,'exit_quote_lag_ms':exit_lag,'first_trade_lag_ms':trade_lag,'trade_confirmed_1s':bool(pd.notna(trade_lag) and trade_lag<=MAX_TRADE_LAG_MS),'entry_px':entry,'exit_px':exitp,'entry_bbo_qty':entryq,'exit_bbo_qty':exitq,'entry_spread_bp':esp,'exit_spread_bp':xsp,'bbo_gross_bp':gross,'close_gross5_bp':float(r.close_gross5_bp)}
            for slip in SLIPPAGE_STRESS_SIDE_BP:base[f'net_slip{slip:.0f}_bp']=gross-2*TAKER_FEE_SIDE_BP-2*slip if valid else np.nan
            rows.append(base)
    R=pd.DataFrame(rows);R.to_csv(f'{OUT}/tick_trades.csv',index=False);return R

def summarize(R,S,missing):
    rows=[]
    for lat,g0 in R.groupby('latency_ms'):
        g=g0[g0.valid].copy();v=g.net_slip0_bp.dropna();trim=v[v<=v.quantile(.95)] if len(v) else v
        rows.append({'latency_ms':lat,'sample_events':len(S),'valid_trades':len(g),'symbols':g.symbol.nunique(),'months':g.month.nunique(),'coverage_rate':len(g)/len(S),'gross_mean_bp':g.bbo_gross_bp.mean(),'gross_median_bp':g.bbo_gross_bp.median(),'net_mean_bp':v.mean(),'net_median_bp':v.median(),'net_win':(v>0).mean(),'net_p10_bp':v.quantile(.1),'net_p90_bp':v.quantile(.9),'net_mean_remove_best5_bp':trim.mean() if len(trim) else np.nan,'net_mean_slip1_bp':g.net_slip1_bp.mean(),'net_mean_slip2_bp':g.net_slip2_bp.mean(),'median_entry_spread_bp':g.entry_spread_bp.median(),'median_exit_spread_bp':g.exit_spread_bp.median(),'trade_confirmed_1s':g.trade_confirmed_1s.mean()})
    Q=pd.DataFrame(rows).sort_values('latency_ms');Q.to_csv(f'{OUT}/summary_by_latency.csv',index=False)
    primary=Q[Q.latency_ms==100];p250=Q[Q.latency_ms==250];gate=False
    if len(primary) and len(p250):
        p=primary.iloc[0];q=p250.iloc[0];gate=bool(p.valid_trades>=100 and p.symbols>=10 and p.net_mean_bp>3 and p.net_median_bp>0 and q.net_mean_bp>0 and p.net_mean_remove_best5_bp>0 and p.net_mean_slip1_bp>0)
    G=R[(R.latency_ms==100)&R.valid].copy()
    side=G.groupby('trade_side').agg(trades=('symbol','size'),symbols=('symbol','nunique'),gross_mean_bp=('bbo_gross_bp','mean'),net_mean_bp=('net_slip0_bp','mean'),net_median_bp=('net_slip0_bp','median'),net_win=('net_slip0_bp',lambda z:(z>0).mean())).reset_index();side.to_csv(f'{OUT}/primary_by_side.csv',index=False)
    month=G.groupby('month').agg(trades=('symbol','size'),symbols=('symbol','nunique'),net_mean_bp=('net_slip0_bp','mean'),net_median_bp=('net_slip0_bp','median'),net_win=('net_slip0_bp',lambda z:(z>0).mean())).reset_index();month.to_csv(f'{OUT}/primary_by_month.csv',index=False)
    lines=['# Strategy #4B Frozen Tick-Level Execution Validation','','Frozen alpha: 100bp residual shock / 3x volume / persistent aligned flow / reverse shock; 5m horizon.',f'Historical block: {MONTHS[0]} through {MONTHS[-1]}. Tick sample is deterministic and return-blind: up to {SAMPLE_PER_MONTH} events/month selected by SHA256(symbol|signal_close_ts).','Signal timing correction: the full 1m bar is required to know the signal, so entry lookup starts only after kline close time + latency.','Execution: taker at first USD-M bookTicker BBO at/after target; long enters ask/exits bid, short enters bid/exits ask. 5bp/side fee. Quotes >1s late are invalid.','aggTrades is used as an independent market-activity timestamp diagnostic; it does not improve fill price.','Base is small-size/BBO execution and therefore excludes market impact. Slippage stress subtracts 1bp/side and 2bp/side.','',f'All frozen signals: {len(pd.read_csv(f"{OUT}/all_signals.csv"))}; deterministic tick sample: {len(S)} across {S.symbol.nunique()} symbols and {S.month.nunique()} months.',f'Tick archive missing files: {len(missing)}.','','## Latency summary','',Q.to_markdown(index=False,floatfmt='.3f'),'','## 100ms side diagnostic','',side.to_markdown(index=False,floatfmt='.3f'),'','## 100ms month diagnostic','',month.to_markdown(index=False,floatfmt='.3f'),'',f"Verdict: **{'PASS_EXECUTION' if gate else 'REJECT_OR_REDESIGN'}**.",'Predeclared execution gate: at 100ms valid trades>=100, symbols>=10, mean net>+3bp, median net>0, remove-best-5% mean>0, +1bp/side slippage-stress mean>0; and 250ms mean net>0.','Do not use side/month diagnostics to retroactively change the frozen rule without a new untouched holdout.']
    text='\n'.join(lines);open(f'{OUT}/summary.md','w').write(text);print(text,flush=True)

if __name__=='__main__':
    missk=prefetch_klines()
    if missk:raise RuntimeError('monthly kline coverage incomplete')
    E=build_frozen_signals();S=deterministic_sample(E);book_req,agg_req,reqdays=build_requests(S);print('required sampled symbol-days',len(reqdays),flush=True);missing=prefetch_tick(reqdays);quotes,trades=scan_all(book_req,agg_req);R=evaluate(S,quotes,trades);summarize(R,S,missing)
