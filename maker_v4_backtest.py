import io, os, zipfile, math, json, requests
import numpy as np
import pandas as pd

SYMBOL='ONEUSDT'
DATES=pd.date_range('2024-03-26','2024-03-31',freq='D').strftime('%Y-%m-%d').tolist()
TRAIN_END='2024-03-28'
GRID_MS=250
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
OUT='maker_v4_output'
os.makedirs(OUT,exist_ok=True)


def download_zip(kind, day):
    url=f'https://data.binance.vision/data/futures/um/daily/{kind}/{SYMBOL}/{SYMBOL}-{kind}-{day}.zip'
    r=requests.get(url,timeout=60)
    if r.status_code!=200:
        raise RuntimeError(f'{r.status_code} {url}')
    z=zipfile.ZipFile(io.BytesIO(r.content))
    name=[n for n in z.namelist() if n.endswith('.csv')][0]
    return z.read(name)


def read_book(day):
    raw=download_zip('bookTicker',day)
    df=pd.read_csv(io.BytesIO(raw))
    # canonical futures historical bookTicker columns
    if 'best_bid_price' not in df.columns:
        if df.shape[1] < 7:
            raise RuntimeError(f'Unexpected bookTicker columns: {df.columns.tolist()}')
        df=df.iloc[:,:7]
        df.columns=['update_id','best_bid_price','best_bid_qty','best_ask_price','best_ask_qty','transaction_time','event_time']
    ren={'bidPrice':'best_bid_price','bidQty':'best_bid_qty','askPrice':'best_ask_price','askQty':'best_ask_qty','time':'event_time'}
    df=df.rename(columns=ren)
    for c in ['best_bid_price','best_bid_qty','best_ask_price','best_ask_qty']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    et=pd.to_numeric(df['event_time'],errors='coerce')
    # tolerate string timestamps if archive format differs
    if et.notna().mean()<0.9:
        et=pd.to_datetime(df['event_time'],utc=True,errors='coerce').astype('int64')//10**6
    df['ts']=et.astype('Int64')
    df=df.dropna(subset=['ts','best_bid_price','best_ask_price']).copy()
    return df[['ts','best_bid_price','best_bid_qty','best_ask_price','best_ask_qty']].sort_values('ts')


def read_trades(day):
    raw=download_zip('aggTrades',day)
    df=pd.read_csv(io.BytesIO(raw))
    # archive may be headered or headerless
    if not any(str(c).lower() in ('price','p') for c in df.columns):
        df=pd.read_csv(io.BytesIO(raw),header=None)
        df=df.iloc[:,:7]
        df.columns=['agg_id','price','qty','first_id','last_id','timestamp','buyer_maker']
    else:
        cols=list(df.columns)
        mapping={}
        for c in cols:
            lc=str(c).lower().strip()
            if lc in ('price','p'): mapping[c]='price'
            elif lc in ('quantity','qty','q'): mapping[c]='qty'
            elif 'timestamp' in lc or lc in ('time','t'): mapping[c]='timestamp'
            elif 'buyer' in lc and 'maker' in lc: mapping[c]='buyer_maker'
        df=df.rename(columns=mapping)
        if 'buyer_maker' not in df.columns:
            # Binance standard is 7th column in futures aggTrades archive
            if len(df.columns)>=7: df=df.rename(columns={df.columns[6]:'buyer_maker'})
    for c in ['price','qty','timestamp']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    def as_bool(x):
        if isinstance(x,bool): return x
        return str(x).strip().lower() in ('true','1','t')
    df['buyer_maker']=df['buyer_maker'].map(as_bool)
    df=df.dropna(subset=['price','qty','timestamp']).copy()
    df['ts']=df['timestamp'].astype(np.int64)
    return df[['ts','price','qty','buyer_maker']].sort_values('ts')


def load_all():
    bs=[]; ts=[]
    for d in DATES:
        print('download',d,flush=True)
        b=read_book(d); t=read_trades(d)
        b['day']=d; t['day']=d
        bs.append(b); ts.append(t)
    return pd.concat(bs,ignore_index=True), pd.concat(ts,ignore_index=True)


def make_grid(book,trades):
    start=max(int(book.ts.min()),int(trades.ts.min()))
    end=min(int(book.ts.max()),int(trades.ts.max()))
    idx=np.arange((start//GRID_MS)*GRID_MS, (end//GRID_MS)*GRID_MS+GRID_MS, GRID_MS,dtype=np.int64)
    g=pd.DataFrame({'ts':idx})
    b=book[['ts','best_bid_price','best_bid_qty','best_ask_price','best_ask_qty']].drop_duplicates('ts',keep='last')
    g=pd.merge_asof(g,b,on='ts',direction='backward')
    g=g.dropna().reset_index(drop=True)
    g['mid']=(g.best_bid_price+g.best_ask_price)/2
    g['spread_bp']=(g.best_ask_price-g.best_bid_price)/g.mid*1e4
    g['qimb']=(g.best_bid_qty-g.best_ask_qty)/(g.best_bid_qty+g.best_ask_qty+1e-12)
    # rolling trade flow, calculated on 250ms buckets
    tb=trades.copy()
    tb['bucket']=(tb.ts//GRID_MS)*GRID_MS
    tb['notional']=tb.price*tb.qty
    tb['signed_notional']=np.where(tb.buyer_maker,-tb.notional,tb.notional)
    ag=tb.groupby('bucket').agg(total_notional=('notional','sum'),signed_notional=('signed_notional','sum'),last_trade=('price','last')).reset_index().rename(columns={'bucket':'ts'})
    g=g.merge(ag,on='ts',how='left')
    g[['total_notional','signed_notional']]=g[['total_notional','signed_notional']].fillna(0.0)
    g['last_trade']=g.last_trade.ffill().fillna(g.mid)
    w=max(1,1000//GRID_MS)
    sn=g.signed_notional.rolling(w,min_periods=1).sum(); tn=g.total_notional.rolling(w,min_periods=1).sum()
    g['flow1s']=sn/(tn+1e-12)
    g['day']=pd.to_datetime(g.ts,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
    return g


def fill_order(trades, start_ts, end_ts, side, px, queue_ahead, qmult):
    a=trades.ts.values
    lo=np.searchsorted(a,start_ts,side='left'); hi=np.searchsorted(a,end_ts,side='right')
    q=0.0
    # pessimistic: ignore cancellations ahead and require qmult*displayed queue to trade through
    need=max(queue_ahead*qmult,0.0)
    for row in trades.iloc[lo:hi].itertuples(index=False):
        if side=='buy':
            if row.buyer_maker and row.price<=px+1e-15:
                q+=row.qty
        else:
            if (not row.buyer_maker) and row.price>=px-1e-15:
                q+=row.qty
        if q>need:
            return int(row.ts)
    return None


def bbo_at(g, ts):
    arr=g.ts.values
    i=np.searchsorted(arr,ts,side='right')-1
    if i<0: return None
    return g.iloc[i]


def simulate(g,trades, cfg):
    spread_min,flow_cap,qmin,entry_ttl_s,exit_ttl_s,qmult,lat_ms=cfg
    lat_bars=max(1,int(math.ceil(lat_ms/GRID_MS)))
    i=4
    rec=[]
    N=len(g)
    while i<N-lat_bars-2:
        r=g.iloc[i]
        if r.spread_bp<spread_min:
            i+=1; continue
        # quote the side whose queue/flow is less toxic; if both qualify use stronger queue imbalance
        can_long=(r.flow1s>=-flow_cap) and (r.qimb>=qmin)
        can_short=(r.flow1s<= flow_cap) and (r.qimb<=-qmin)
        if not (can_long or can_short):
            i+=1; continue
        if can_long and can_short:
            side='buy' if r.qimb>=0 else 'sell'
        elif can_long: side='buy'
        else: side='sell'
        place_i=i+lat_bars
        if place_i>=N: break
        p=g.iloc[place_i]
        px=float(p.best_bid_price if side=='buy' else p.best_ask_price)
        queue=float(p.best_bid_qty if side=='buy' else p.best_ask_qty)
        fill_ts=fill_order(trades,int(p.ts),int(p.ts+entry_ttl_s*1000),side,px,queue,qmult)
        if fill_ts is None:
            i=place_i+1; continue
        f=bbo_at(g,fill_ts)
        if f is None: i=place_i+1; continue
        # opposite passive exit after latency, at current BBO; if not filled, force taker at end of exit TTL
        exit_start=fill_ts+lat_ms
        e0=bbo_at(g,exit_start)
        if e0 is None: i=place_i+1; continue
        exit_side='sell' if side=='buy' else 'buy'
        exit_px=float(e0.best_ask_price if exit_side=='sell' else e0.best_bid_price)
        exit_q=float(e0.best_ask_qty if exit_side=='sell' else e0.best_bid_qty)
        exit_fill=fill_order(trades,int(exit_start),int(exit_start+exit_ttl_s*1000),exit_side,exit_px,exit_q,qmult)
        if exit_fill is not None:
            gross=((exit_px-px)/px*1e4) * (1 if side=='buy' else -1)
            fees=2*MAKER_FEE_BP
            mode='MM'
            end_ts=exit_fill
        else:
            end_ts=int(exit_start+exit_ttl_s*1000)
            ex=bbo_at(g,end_ts)
            if ex is None: break
            # conservative taker exit at opposite touch
            mkt_px=float(ex.best_bid_price if side=='buy' else ex.best_ask_price)
            gross=((mkt_px-px)/px*1e4) * (1 if side=='buy' else -1)
            fees=MAKER_FEE_BP+TAKER_FEE_BP
            mode='MT'
        rec.append({'signal_ts':int(r.ts),'fill_ts':fill_ts,'end_ts':end_ts,'side':side,'entry':px,'gross_bp':gross,'fees_bp':fees,'net_bp':gross-fees,'exit_mode':mode,'spread_signal_bp':r.spread_bp,'flow1s':r.flow1s,'qimb':r.qimb})
        i=np.searchsorted(g.ts.values,end_ts,side='right')
    return pd.DataFrame(rec)


def stats(df):
    if len(df)==0: return {'n':0,'net':np.nan,'gross':np.nan,'mm_rate':np.nan,'t':np.nan,'ci_low':np.nan}
    x=df.net_bp.values.astype(float); n=len(x); sd=x.std(ddof=1) if n>1 else np.nan
    se=sd/math.sqrt(n) if n>1 and sd>0 else np.nan
    return {'n':n,'net':x.mean(),'gross':df.gross_bp.mean(),'mm_rate':(df.exit_mode=='MM').mean(),'t':x.mean()/se if se and np.isfinite(se) else np.nan,'ci_low':x.mean()-1.96*se if se and np.isfinite(se) else np.nan}


def main():
    book,trades=load_all(); g=make_grid(book,trades)
    configs=[]
    for spread in [4.0,5.0,6.0,8.0]:
      for flow_cap in [1.0,0.5,0.2]:
       for qmin in [-1.0,0.0,0.2]:
        for entry_ttl in [1,3]:
         for exit_ttl in [5,15,30]:
          for qmult in [1.0,1.5]:
           for lat in [250,500]:
            configs.append((spread,flow_cap,qmin,entry_ttl,exit_ttl,qmult,lat))
    rows=[]; trade_cache={}
    for k,cfg in enumerate(configs):
        d=simulate(g,trades,cfg)
        if len(d)==0: continue
        d['day']=pd.to_datetime(d.signal_ts,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
        tr=d[d.day<=TRAIN_END]; oo=d[d.day>TRAIN_END]
        st=stats(tr); so=stats(oo)
        rows.append({'rid':k,'spread_min':cfg[0],'flow_cap':cfg[1],'qmin':cfg[2],'entry_ttl_s':cfg[3],'exit_ttl_s':cfg[4],'qmult':cfg[5],'lat_ms':cfg[6],
                     'train_n':st['n'],'train_net_bp':st['net'],'train_ci_low':st['ci_low'],'train_mm_rate':st['mm_rate'],
                     'oos_n':so['n'],'oos_gross_bp':so['gross'],'oos_net_bp':so['net'],'oos_ci_low':so['ci_low'],'oos_mm_rate':so['mm_rate']})
        trade_cache[k]=d
    res=pd.DataFrame(rows)
    res.to_csv(f'{OUT}/all_results.csv',index=False)
    eligible=res[(res.train_n>=30)&(res.train_net_bp>=0)].sort_values(['train_net_bp','train_n'],ascending=[False,False]).head(20)
    # If no train-positive config, show best train configs but do not label them candidates.
    top=eligible if len(eligible) else res[res.train_n>=30].sort_values('train_net_bp',ascending=False).head(20)
    top.to_csv(f'{OUT}/selected.csv',index=False)
    passes=top[(top.oos_n>=20)&(top.oos_net_bp>=0)] if len(top) else top
    # market diagnostics
    diag={
      'symbol':SYMBOL,'dates':DATES,'train_end':TRAIN_END,'grid_ms':GRID_MS,
      'maker_fee_bp_side':MAKER_FEE_BP,'taker_fee_bp_side':TAKER_FEE_BP,
      'median_spread_bp':float(g.spread_bp.median()),
      'p75_spread_bp':float(g.spread_bp.quantile(.75)),
      'p90_spread_bp':float(g.spread_bp.quantile(.90)),
      'share_spread_ge4':float((g.spread_bp>=4).mean()),
      'share_spread_ge5':float((g.spread_bp>=5).mean()),
    }
    with open(f'{OUT}/diagnostics.json','w') as f: json.dump(diag,f,indent=2)
    lines=['# ONEUSDT conservative maker v4','',
      '- Historical BBO bookTicker + aggTrades; 250ms decision grid.','- Queue-ahead fill: displayed L1 queue at placement, no cancellation credit; require aggressive trade-through before fill.','- Entry maker 2bp/side. Exit attempts maker; if not filled within TTL, forced taker exit at 5bp.','- 250/500ms placement latency proxies. Historical archive is 2024-03-26..31 because public high-frequency bookTicker archives are not reliably current.','',
      f"Median spread: {diag['median_spread_bp']:.3f} bp; p90: {diag['p90_spread_bp']:.3f} bp; share >=4bp: {diag['share_spread_ge4']:.3%}.",'',
      f'## OOS PASS count (selected on train; after actual maker/taker fees): {len(passes)}','']
    if len(passes):
      lines+=['| spread | flowcap | qmin | entry ttl | exit ttl | qmult | lat | OOS n | OOS net bp | MM exit rate | 95% low |','|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
      for r in passes.sort_values('oos_net_bp',ascending=False).itertuples():
        lines.append(f'| {r.spread_min:.1f} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.entry_ttl_s} | {r.exit_ttl_s} | {r.qmult:.1f} | {r.lat_ms}ms | {int(r.oos_n)} | {r.oos_net_bp:.3f} | {r.oos_mm_rate:.3f} | {r.oos_ci_low:.3f} |')
    else:
      lines.append('None.')
    lines+=['','## Top selected configs','']
    if len(top):
      lines+=['| spread | flowcap | qmin | ttl in/out | qmult | lat | train n/net | OOS n/net | OOS MM rate |','|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
      for r in top.head(12).itertuples():
        lines.append(f'| {r.spread_min:.1f} | {r.flow_cap:.1f} | {r.qmin:.1f} | {r.entry_ttl_s}/{r.exit_ttl_s}s | {r.qmult:.1f} | {r.lat_ms}ms | {int(r.train_n)}/{r.train_net_bp:.3f} | {int(r.oos_n)}/{r.oos_net_bp:.3f} | {r.oos_mm_rate:.3f} |')
    lines+=['','A PASS is a research candidate, not deployment proof: L1 queue is modeled conservatively, but hidden/RPI liquidity, queue priority changes, and real order ACK/cancel timing are not reconstructed.']
    open(f'{OUT}/summary.md','w').write('\n'.join(lines))
    print('\n'.join(lines))

if __name__=='__main__': main()
