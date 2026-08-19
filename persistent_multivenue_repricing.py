import io, os, gzip, zipfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, pandas as pd, numpy as np

OUT='persistent_multivenue_output'; os.makedirs(OUT,exist_ok=True)
TMP='/tmp/s18_multivenue'; os.makedirs(TMP,exist_ok=True)
SYMS=['BTCUSDT','ETHUSDT','DOGEUSDT']
VENUES=['binance','bybit','gate']
BLOCKS={
    'BLOCK_A_2024_DEC': pd.date_range('2024-12-01','2024-12-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),
    'BLOCK_B_2026_JUN': pd.date_range('2026-06-01','2026-06-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),
}
LATENCIES=[100,250]
FORMATION_S=10
PERSIST_S=5
LEADER10_MIN_BP=25.0
LEADER5_FRACTION=0.25
MIN_LAG_GAP_BP=18.0
EXIT_RESID_BP=5.0
MIN_HOLD_MS=5000
MAX_HOLD_MS=60000
DEOVERLAP_MS=60000
FEE_BP=10.0
STRESS_BP=12.0
MAX_ABS_10S_BP=500.0
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
        if med<1e11: ts=ts*1000
        elif med>1e15: ts=ts/1000
        x=pd.DataFrame({'ts':ts,'px':px}).dropna(); x=x[x.px>0]
        x['ts']=x.ts.astype('int64')
        return x.sort_values('ts').drop_duplicates().reset_index(drop=True)
    except Exception:return None

def gate_contract(sym): return sym[:-4]+'_USDT'

def prepare_gate_month(sym,days):
    ym=days[0][:7].replace('-',''); contract=gate_contract(sym)
    src=f'{TMP}/gate-{contract}-{ym}.csv.gz'
    url=f'https://download.gatedata.org/futures_usdt/trades/{ym}/{contract}-{ym}.csv.gz'
    if not download_file(url,src):return {d:False for d in days}
    paths={d:f'{TMP}/gate-{contract}-{d}.csv' for d in days}
    if not all(os.path.exists(p) and os.path.getsize(p)>20 for p in paths.values()):
        for p in paths.values():
            try: os.remove(p)
            except Exception: pass
        starts={d:int(pd.Timestamp(d,tz='UTC').timestamp()) for d in days}
        try:
            for ch in pd.read_csv(src,compression='gzip',header=None,names=['sec','dealid','px','size'],chunksize=1000000,low_memory=False):
                sec=pd.to_numeric(ch.sec,errors='coerce'); px=pd.to_numeric(ch.px,errors='coerce')
                ok=sec.notna() & px.notna() & (px>0)
                q=ch.loc[ok,['sec','px']].copy(); q['sec']=pd.to_numeric(q.sec)
                if q.empty:continue
                for d,st in starts.items():
                    z=q[(q.sec>=st)&(q.sec<st+86400)]
                    if not z.empty:z.to_csv(paths[d],mode='a',header=False,index=False)
        except Exception:return {d:False for d in days}
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

def first_trade(x,when,timeout=1500):
    if x is None:return None
    ts=x.ts.to_numpy(np.int64); j=np.searchsorted(ts,when,'left')
    if j>=len(ts) or ts[j]>when+timeout:return None
    return j

def generate_signals(sym,day,frame):
    r10=np.log(frame/frame.shift(FORMATION_S))*1e4
    r5=np.log(frame/frame.shift(PERSIST_S))*1e4
    signals=[]; last=-10**18
    for ts in frame.index[max(FORMATION_S,PERSIST_S)+1:]:
        its=int(ts)
        if its-last<DEOVERLAP_MS:continue
        candidates=[]
        for target in VENUES:
            others=[v for v in VENUES if v!=target]
            a10=float(r10.at[ts,others[0]]); b10=float(r10.at[ts,others[1]]); own10=float(r10.at[ts,target])
            a5=float(r5.at[ts,others[0]]); b5=float(r5.at[ts,others[1]]); own5=float(r5.at[ts,target])
            vals=[a10,b10,own10,a5,b5,own5]
            if not np.all(np.isfinite(vals)) or max(abs(x) for x in [a10,b10,own10])>MAX_ABS_10S_BP:continue
            if a10==0 or b10==0 or np.sign(a10)!=np.sign(b10):continue
            side=1 if a10>0 else -1
            sa10=side*a10; sb10=side*b10; so10=side*own10
            sa5=side*a5; sb5=side*b5; so5=side*own5
            leader10=float(np.median([sa10,sb10])); leader5=float(np.median([sa5,sb5]))
            if leader10<LEADER10_MIN_BP:continue
            if sa5<=0 or sb5<=0:continue
            if sa5 < LEADER5_FRACTION*sa10 or sb5 < LEADER5_FRACTION*sb10:continue
            if so10>=min(sa10,sb10):continue
            gap=leader10-so10
            if gap<MIN_LAG_GAP_BP:continue
            if so5>=leader5:continue
            candidates.append((gap,target,side,leader10,leader5,so10,so5,others,a10,b10,a5,b5))
        if not candidates:continue
        gap,target,side,leader10,leader5,so10,so5,others,a10,b10,a5,b5=max(candidates,key=lambda z:z[0])
        signal=its+1000
        signals.append({'symbol':sym,'day':day,'signal_ms':signal,'formation_ts':its,'target_venue':target,'side':side,
                        'initial_gap_bp':gap,'leader10_bp':leader10,'leader5_bp':leader5,'target10_signed_bp':so10,
                        'target5_signed_bp':so5,'leader1':others[0],'leader2':others[1],
                        'leader1_10_bp':a10,'leader2_10_bp':b10,'leader1_5_bp':a5,'leader2_5_bp':b5,
                        'target_base_px':float(frame.at[ts,target]),'leader1_base_px':float(frame.at[ts,others[0]]),
                        'leader2_base_px':float(frame.at[ts,others[1]])})
        last=signal
    return signals

def choose_exit(ev,frame,raw_target,lat,entry_ts):
    side=ev['side']; target=ev['target_venue']; l1=ev['leader1']; l2=ev['leader2']
    start_check=ev['signal_ms']+MIN_HOLD_MS
    max_exit=entry_ts+MAX_HOLD_MS
    exit_when=None; exit_reason='max_hold'; resid=np.nan
    idx=frame.index.to_numpy(np.int64)
    j0=np.searchsorted(idx,start_check,'left'); j1=np.searchsorted(idx,max_exit,'right')
    for ts in idx[j0:j1]:
        try:
            tc=side*np.log(float(frame.at[ts,target])/ev['target_base_px'])*1e4
            l1c=side*np.log(float(frame.at[ts,l1])/ev['leader1_base_px'])*1e4
            l2c=side*np.log(float(frame.at[ts,l2])/ev['leader2_base_px'])*1e4
        except Exception:continue
        leader_cum=float(np.median([l1c,l2c]))
        resid=float(ev['initial_gap_bp']-(tc-leader_cum))
        if resid<=EXIT_RESID_BP:
            exit_when=int(ts+1000+lat); exit_reason='gap_closed'; break
    if exit_when is None: exit_when=max_exit
    k=first_trade(raw_target,exit_when,1500)
    if k is None:return None
    tsarr=raw_target.ts.to_numpy(np.int64); pxarr=raw_target.px.to_numpy(float)
    return int(tsarr[k]),float(pxarr[k]),exit_reason,resid

def execute_signal(ev,raw,frame,lat):
    x=raw.get(ev['target_venue'])
    j=first_trade(x,ev['signal_ms']+lat,1500)
    if j is None:return None
    ts=x.ts.to_numpy(np.int64); px=x.px.to_numpy(float)
    entry_ts=int(ts[j]); entry=float(px[j])
    out=choose_exit(ev,frame,x,lat,entry_ts)
    if out is None:return None
    exit_ts,exit_px,reason,resid=out
    gross=ev['side']*np.log(exit_px/entry)*1e4
    r=dict(ev); r.update({'latency_ms':lat,'entry_ts':entry_ts,'exit_ts':exit_ts,'entry_px':entry,'exit_px':exit_px,
                         'hold_s':(exit_ts-entry_ts)/1000.0,'exit_reason':reason,'exit_resid_bp':resid,
                         'gross_bp':gross,'net_fee_bp':gross-FEE_BP,'net_stress_bp':gross-STRESS_BP})
    return r

def trim5(x):
    x=np.asarray(x,float)
    if len(x)<20:return np.nan
    n=max(1,int(np.ceil(.05*len(x))))
    return float(np.sort(x)[:-n].mean())

summary_rows=[]; all_events=[]
for block,days in BLOCKS.items():
    print('Preparing Gate monthly archives for',block,flush=True)
    gate_ok={s:prepare_gate_month(s,days) for s in SYMS}
    ev_by_lat={lat:[] for lat in LATENCIES}; valid=0; expected=len(days)*len(SYMS)*len(VENUES)
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
        for s in SYMS: dl[s]['gate']=load_gate(s,day) if gate_ok[s].get(day) else None
        day_valid=0
        for s in SYMS:
            day_valid+=sum(dl[s].get(v) is not None for v in VENUES); valid+=sum(dl[s].get(v) is not None for v in VENUES)
            frame=make_frame(dl[s],day)
            if frame is None:continue
            signals=generate_signals(s,day,frame)
            for ev in signals:
                for lat in LATENCIES:
                    z=execute_signal(ev,dl[s],frame,lat)
                    if z is not None:ev_by_lat[lat].append(z); all_events.append(z)
        print(block,'day',di,'/',len(days),day,'valid_venue_symbol',day_valid,'signals100',len(ev_by_lat[100]),flush=True)
    coverage=valid/expected; nd=len(days)
    for lat in LATENCIES:
        e=pd.DataFrame(ev_by_lat[lat]); n=len(e)
        r={'block':block,'latency_ms':lat,'coverage':coverage,'completed':n,'symbols':0,'exec_venues':0,'completed_per_day':n/nd,
           'gross_mean_bp':np.nan,'gross_median_bp':np.nan,'net_fee_mean_bp':np.nan,'net_fee_median_bp':np.nan,
           'net_fee_remove_best5_bp':np.nan,'net_stress_mean_bp':np.nan,'positive_day_frac':np.nan,
           'top_symbol_share':np.nan,'top_venue_share':np.nan,'median_hold_s':np.nan,'gap_closed_frac':np.nan,'pass':False}
        if n:
            dm=e.groupby('day').net_fee_bp.mean()
            r.update({'symbols':int(e.symbol.nunique()),'exec_venues':int(e.target_venue.nunique()),
                      'gross_mean_bp':float(e.gross_bp.mean()),'gross_median_bp':float(e.gross_bp.median()),
                      'net_fee_mean_bp':float(e.net_fee_bp.mean()),'net_fee_median_bp':float(e.net_fee_bp.median()),
                      'net_fee_remove_best5_bp':trim5(e.net_fee_bp),'net_stress_mean_bp':float(e.net_stress_bp.mean()),
                      'positive_day_frac':float((dm>0).mean()),'top_symbol_share':float(e.symbol.value_counts(normalize=True).max()),
                      'top_venue_share':float(e.target_venue.value_counts(normalize=True).max()),'median_hold_s':float(e.hold_s.median()),
                      'gap_closed_frac':float((e.exit_reason=='gap_closed').mean())})
            primary=(coverage>=.95 and n>=70 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=5 and
                     r['gross_mean_bp']>=15 and r['net_fee_mean_bp']>5 and r['net_fee_median_bp']>0 and
                     r['net_fee_remove_best5_bp']>0 and r['net_stress_mean_bp']>3 and r['positive_day_frac']>=.60 and
                     r['top_symbol_share']<=.60 and r['top_venue_share']<=.70)
            stress=(coverage>=.95 and n>=70 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=5 and
                    r['net_fee_mean_bp']>0 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0)
            r['pass']=bool(primary if lat==100 else stress)
        summary_rows.append(r)
summary=pd.DataFrame(summary_rows); summary.to_csv(f'{OUT}/summary.csv',index=False)
if all_events: pd.DataFrame(all_events).to_csv(f'{OUT}/events.csv',index=False)
print('\n# Strategy #18 Persistent Multi-Venue Repricing\n')
print(summary.to_markdown(index=False,floatfmt='.3f'))
print('\nFrozen: Binance/Bybit/Gate symmetric candidates; 10s formation with both leader venues aligned and persistent over the latest 5s; median leader 10s move >=25bp; target trails leader consensus by >=18bp; route to largest lagger. Enter selected venue after 100/250ms. Exit when relative gap <=5bp after >=5s, otherwise max hold 60s. Fee-only=10bp RT; stress=12bp. No market making, no fee optimization, no post-outcome retuning.')
print('OVERALL:', 'PASS' if len(summary)==4 and summary['pass'].all() else 'REJECT_OR_REDESIGN')
