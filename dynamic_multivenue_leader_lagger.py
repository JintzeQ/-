import io, os, gzip, zipfile, time, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

OUT='dynamic_multivenue_output'; os.makedirs(OUT,exist_ok=True)
TMP='/tmp/s17_multivenue'; os.makedirs(TMP,exist_ok=True)
SYMS=['BTCUSDT','ETHUSDT','DOGEUSDT']
VENUES=['binance','bybit','gate']
BLOCKS={
    'BLOCK_A_2024_SEP': pd.date_range('2024-09-01','2024-09-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),
    'BLOCK_B_2025_SEP': pd.date_range('2025-09-01','2025-09-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),
}
WARMUP_DAYS=4
LATENCIES=[100,250]
HOLD_MS=2000
DEOVERLAP_MS=3000
LEADER_MIN_BP=10.0
PRED_MIN_BP=12.0
FEE_BP=10.0
STRESS_BP=12.0
RIDGE=10000.0
MAX_ABS_BP=200.0
UA={'User-Agent':'Mozilla/5.0'}

def get_bytes(url,tries=4):
    for k in range(tries):
        try:
            r=requests.get(url,timeout=90,headers=UA)
            if r.status_code==200 and len(r.content)>100:return r.content
            if r.status_code==404:return None
        except Exception: pass
        time.sleep(.6*(k+1))
    return None

def download_file(url,path):
    if os.path.exists(path) and os.path.getsize(path)>100:return True
    for k in range(4):
        try:
            with requests.get(url,stream=True,timeout=(20,240),headers=UA) as r:
                if r.status_code==404:return False
                r.raise_for_status()
                with open(path+'.part','wb') as f:
                    for c in r.iter_content(1024*1024):
                        if c:f.write(c)
            os.replace(path+'.part',path)
            return os.path.getsize(path)>100
        except Exception:
            try: os.remove(path+'.part')
            except Exception: pass
            time.sleep(1.0*(k+1))
    return False

def load_binance(sym,day):
    url=f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'
    b=get_bytes(url)
    if b is None:return None
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            n=next(x for x in z.namelist() if x.endswith('.csv'))
            d=pd.read_csv(z.open(n),header=None,low_memory=False)
        ts=pd.to_numeric(d.iloc[:,5],errors='coerce'); px=pd.to_numeric(d.iloc[:,1],errors='coerce')
        x=pd.DataFrame({'ts':ts,'px':px}).dropna(); x=x[x.px>0]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def load_bybit(sym,day):
    url=f'https://public.bybit.com/trading/{sym}/{sym}{day}.csv.gz'
    b=get_bytes(url)
    if b is None:return None
    try:
        d=pd.read_csv(io.BytesIO(gzip.decompress(b)),low_memory=False)
        cols={str(c).lower():c for c in d.columns}
        pcol=cols.get('price'); tcol=cols.get('timestamp') or cols.get('time')
        if pcol is None or tcol is None:return None
        px=pd.to_numeric(d[pcol],errors='coerce'); ts=pd.to_numeric(d[tcol],errors='coerce')
        med=float(ts.dropna().median())
        if med<1e11:ts=ts*1000
        elif med>1e15:ts=ts/1000
        x=pd.DataFrame({'ts':ts,'px':px}).dropna(); x=x[x.px>0]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def gate_contract(sym): return sym[:-4]+'_USDT'

def prepare_gate_month(sym,days):
    ym=days[0][:7].replace('-','')
    contract=gate_contract(sym)
    src=f'{TMP}/gate-{contract}-{ym}.csv.gz'
    url=f'https://download.gatedata.org/futures_usdt/trades/{ym}/{contract}-{ym}.csv.gz'
    if not download_file(url,src):return {d:False for d in days}
    paths={d:f'{TMP}/gate-{contract}-{d}.csv' for d in days}
    complete=all(os.path.exists(p) and os.path.getsize(p)>20 for p in paths.values())
    if not complete:
        for p in paths.values():
            try: os.remove(p)
            except Exception: pass
        starts={d:int(pd.Timestamp(d,tz='UTC').timestamp()) for d in days}
        try:
            for ch in pd.read_csv(src,compression='gzip',header=None,names=['sec','dealid','px','size'],chunksize=1000000,low_memory=False):
                sec=pd.to_numeric(ch.sec,errors='coerce'); px=pd.to_numeric(ch.px,errors='coerce')
                ok=sec.notna() & px.notna() & (px>0)
                ch=ch.loc[ok,['sec','px']].copy(); ch['sec']=pd.to_numeric(ch.sec)
                if ch.empty:continue
                for d,st in starts.items():
                    q=ch[(ch.sec>=st)&(ch.sec<st+86400)]
                    if not q.empty:q.to_csv(paths[d],mode='a',header=False,index=False)
        except Exception:
            return {d:False for d in days}
    return {d:(os.path.exists(paths[d]) and os.path.getsize(paths[d])>20) for d in days}

def load_gate(sym,day):
    p=f'{TMP}/gate-{gate_contract(sym)}-{day}.csv'
    if not os.path.exists(p):return None
    try:
        d=pd.read_csv(p,header=None,names=['sec','px'])
        sec=pd.to_numeric(d.sec,errors='coerce'); px=pd.to_numeric(d.px,errors='coerce')
        x=pd.DataFrame({'ts':sec*1000,'px':px}).dropna(); x=x[x.px>0]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def one_sec_last(x,day):
    if x is None or len(x)<100:return None
    s=(x.ts.to_numpy(np.int64)//1000)*1000
    g=pd.Series(x.px.to_numpy(float),index=s).groupby(level=0).last()
    start=int(pd.Timestamp(day,tz='UTC').timestamp()*1000)
    idx=np.arange(start,start+86400000,1000,dtype='int64')
    return g.reindex(idx).ffill(limit=2)

def make_frame(raw,day):
    cols={}
    for v in VENUES:
        s=one_sec_last(raw.get(v),day)
        if s is None:return None
        cols[v]=s
    f=pd.DataFrame(cols).dropna()
    return f if len(f)>1000 else None

def first_trade(x,when,timeout=1000):
    if x is None:return None
    ts=x.ts.to_numpy(np.int64); j=np.searchsorted(ts,when,'left')
    if j>=len(ts) or ts[j]>when+timeout:return None
    return j

def blank_stats():
    return {s:{v:{'xtx':np.zeros((4,4)), 'xty':np.zeros(4), 'n':0} for v in VENUES} for s in SYMS}

def design(frame,target):
    ret=np.log(frame/frame.shift(1))*1e4
    others=[v for v in VENUES if v!=target]
    y=np.log(frame[target].shift(-2)/frame[target])*1e4
    q=pd.DataFrame({'self':ret[target],'o1':ret[others[0]],'o2':ret[others[1]],'y':y}).dropna()
    q=q[(q[['self','o1','o2','y']].abs()<=MAX_ABS_BP).all(axis=1)]
    if q.empty:return None,None
    X=np.column_stack([np.ones(len(q)),q[['self','o1','o2']].to_numpy(float)])
    return X,q.y.to_numpy(float)

def update_stats(stats,sym,frame):
    for target in VENUES:
        X,y=design(frame,target)
        if X is None:continue
        st=stats[sym][target]; st['xtx']+=X.T@X; st['xty']+=X.T@y; st['n']+=len(y)

def coefficients(st):
    if st['n']<10000:return None
    pen=np.diag([0.0,RIDGE,RIDGE,RIDGE])
    try:return np.linalg.solve(st['xtx']+pen,st['xty'])
    except Exception:return None

def generate_signals(sym,day,frame,stats):
    ret=np.log(frame/frame.shift(1))*1e4
    models={v:coefficients(stats[sym][v]) for v in VENUES}
    sig=[]; last=-10**18; model_rows=[]
    for target in VENUES:
        b=models[target]
        if b is not None:
            others=[v for v in VENUES if v!=target]
            model_rows.append({'symbol':sym,'day':day,'target':target,'n_train':stats[sym][target]['n'],
                               'intercept':b[0],'self_beta':b[1],f'{others[0]}_beta':b[2],f'{others[1]}_beta':b[3]})
    for ts,row in ret.iterrows():
        its=int(ts)
        if its-last<DEOVERLAP_MS:continue
        candidates=[]
        for target in VENUES:
            b=models[target]
            if b is None:continue
            others=[v for v in VENUES if v!=target]
            vals=[row[target],row[others[0]],row[others[1]]]
            if not np.all(np.isfinite(vals)) or max(abs(x) for x in vals)>MAX_ABS_BP:continue
            a=float(row[others[0]]); c=float(row[others[1]]); own=float(row[target])
            if a==0 or c==0 or np.sign(a)!=np.sign(c):continue
            side=1 if a>0 else -1
            leader_mag=float(np.median([abs(a),abs(c)]))
            if leader_mag<LEADER_MIN_BP:continue
            if abs(own)>=min(abs(a),abs(c)):continue
            x=np.array([1.0,own,a,c]); pred=float(x@b)
            if not np.isfinite(pred) or np.sign(pred)!=side or abs(pred)<PRED_MIN_BP:continue
            candidates.append((abs(pred),target,side,pred,own,a,c,others))
        if not candidates:continue
        _,target,side,pred,own,a,c,others=max(candidates,key=lambda z:z[0])
        signal=its+1000
        sig.append({'symbol':sym,'day':day,'signal_ms':signal,'target_venue':target,'side':side,
                    'pred_2s_bp':pred,'target_ret_1s_bp':own,'leader1':others[0],'leader1_ret_bp':a,
                    'leader2':others[1],'leader2_ret_bp':c})
        last=signal
    return sig,model_rows

def execute_signal(ev,raw,lat):
    x=raw.get(ev['target_venue'])
    j=first_trade(x,ev['signal_ms']+lat,1000)
    if j is None:return None
    ts=x.ts.to_numpy(np.int64); px=x.px.to_numpy(float)
    entry_ts=int(ts[j]); entry=float(px[j])
    k=first_trade(x,entry_ts+HOLD_MS,1000)
    if k is None:return None
    exit_ts=int(ts[k]); exit=float(px[k]); gross=ev['side']*np.log(exit/entry)*1e4
    r=dict(ev); r.update({'latency_ms':lat,'entry_ts':entry_ts,'exit_ts':exit_ts,'entry_px':entry,'exit_px':exit,
                         'gross_bp':gross,'net_fee_bp':gross-FEE_BP,'net_stress_bp':gross-STRESS_BP})
    return r

def trim5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(.05*len(x))))
    return float(np.sort(x)[:-n].mean())

summary_rows=[]; all_events=[]; model_rows=[]
for block,days in BLOCKS.items():
    print('Preparing Gate monthly archives for',block,flush=True)
    gate_ok={}
    for s in SYMS:gate_ok[s]=prepare_gate_month(s,days)
    stats=blank_stats(); ev_by_lat={lat:[] for lat in LATENCIES}; valid=0; expected=len(days)*len(SYMS)*len(VENUES)
    eval_days=days[WARMUP_DAYS:]
    for di,day in enumerate(days,1):
        dl={s:{} for s in SYMS}
        with ThreadPoolExecutor(max_workers=6) as ex:
            fut={}
            for s in SYMS:
                fut[ex.submit(load_binance,s,day)]=(s,'binance'); fut[ex.submit(load_bybit,s,day)]=(s,'bybit')
            for f in as_completed(fut):
                s,v=fut[f]
                try:dl[s][v]=f.result()
                except Exception:dl[s][v]=None
        for s in SYMS:dl[s]['gate']=load_gate(s,day) if gate_ok[s].get(day) else None
        day_valid=0
        for s in SYMS:
            day_valid+=sum(dl[s].get(v) is not None for v in VENUES); valid+=sum(dl[s].get(v) is not None for v in VENUES)
            frame=make_frame(dl[s],day)
            if frame is None:continue
            if di>WARMUP_DAYS:
                signals,mr=generate_signals(s,day,frame,stats); model_rows.extend(mr)
                for ev in signals:
                    for lat in LATENCIES:
                        z=execute_signal(ev,dl[s],lat)
                        if z is not None:ev_by_lat[lat].append(z); all_events.append(z)
            update_stats(stats,s,frame)
        print(block,'day',di,'/',len(days),day,'valid_venue_symbol',day_valid,'eval',di>WARMUP_DAYS,
              'signals100',len(ev_by_lat[100]),flush=True)
    coverage=valid/expected
    for lat in LATENCIES:
        e=pd.DataFrame(ev_by_lat[lat]); n=len(e); nd=len(eval_days)
        r={'block':block,'latency_ms':lat,'coverage':coverage,'completed':n,'symbols':0,'exec_venues':0,
           'completed_per_day':n/nd,'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_fee_mean_bp':np.nan,
           'net_fee_median_bp':np.nan,'net_fee_remove_best5_bp':np.nan,'net_stress_mean_bp':np.nan,
           'positive_day_frac':np.nan,'top_symbol_share':np.nan,'top_venue_share':np.nan,'pass':False}
        if n:
            dm=e.groupby('day').net_fee_bp.mean()
            r.update({'symbols':int(e.symbol.nunique()),'exec_venues':int(e.target_venue.nunique()),
                      'gross_mean_bp':float(e.gross_bp.mean()),'gross_median_bp':float(e.gross_bp.median()),
                      'net_fee_mean_bp':float(e.net_fee_bp.mean()),'net_fee_median_bp':float(e.net_fee_bp.median()),
                      'net_fee_remove_best5_bp':trim5(e.net_fee_bp),'net_stress_mean_bp':float(e.net_stress_bp.mean()),
                      'positive_day_frac':float((dm>0).mean()),'top_symbol_share':float(e.symbol.value_counts(normalize=True).max()),
                      'top_venue_share':float(e.target_venue.value_counts(normalize=True).max())})
            primary=(coverage>=.95 and n>=500 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=50 and
                     r['net_fee_mean_bp']>2 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0 and
                     r['net_stress_mean_bp']>0 and r['positive_day_frac']>=.60 and r['top_symbol_share']<=.60 and r['top_venue_share']<=.70)
            stress=(coverage>=.95 and n>=500 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=50 and
                    r['net_fee_mean_bp']>0 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0)
            r['pass']=bool(primary if lat==100 else stress)
        summary_rows.append(r)

summary=pd.DataFrame(summary_rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_events:pd.DataFrame(all_events).to_csv(f'{OUT}/events.csv',index=False)
if model_rows:pd.DataFrame(model_rows).to_csv(f'{OUT}/daily_models.csv',index=False)
print('\n# Strategy #17 Dynamic Multi-Venue Consensus + Smart Lagger Routing\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen: Binance USD-M, Bybit USDT perp, Gate USD-M all symmetric candidates. Per target venue, expanding past-only ridge predicts next 2s return from current 1s returns on all 3 venues. Other two venues must agree in sign, median leader magnitude >=10bp, target must have moved less than both leaders, predicted remaining move >=12bp. Choose target with largest predicted catch-up; enter that venue after 100/250ms, hold 2s. Fee-only=10bp RT, stress=12bp. No market making, no fixed leader/lagger venue, no post-outcome retuning.')
print('OVERALL:', 'PASS' if len(summary)==4 and summary['pass'].all() else 'REJECT_OR_REDESIGN')
