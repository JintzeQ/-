import io, os, zipfile, math, json, requests
import numpy as np
import pandas as pd

SYMBOLS=['FETUSDT','OPUSDT','WIFUSDT']
DAYS=['2024-03-29','2024-03-30','2024-03-31']
TRAIN_DAY='2024-03-29'
OOS_DAYS=['2024-03-30','2024-03-31']
MAKER_FEE_BP=2.0
TAKER_FEE_BP=5.0
ORDER_NOTIONAL=100.0
DECISION_MS=2000
ENTRY_TTL_MS=20000
EXIT_TTL_MS=60000
OUT='maker_historical_v5_output'
os.makedirs(OUT,exist_ok=True)

CONFIGS=[]
for lat in [10,240]:
  for qmin in [0.0,0.3,0.6]:
    for flowcap in [0.2,0.5,1.0]:
      for qmult in [1.0,1.5]:
        CONFIGS.append({'lat':lat,'qmin':qmin,'flowcap':flowcap,'qmult':qmult})

def get_zip(kind,symbol,day):
    url=f'https://data.binance.vision/data/futures/um/daily/{kind}/{symbol}/{symbol}-{kind}-{day}.zip'
    r=requests.get(url,timeout=120)
    if r.status_code!=200: raise RuntimeError(f'{r.status_code} {url}')
    z=zipfile.ZipFile(io.BytesIO(r.content)); name=[x for x in z.namelist() if x.endswith('.csv')][0]
    return z.read(name)

def read_book(symbol,day):
    raw=get_zip('bookTicker',symbol,day)
    df=pd.read_csv(io.BytesIO(raw))
    want=['update_id','best_bid_price','best_bid_qty','best_ask_price','best_ask_qty','transaction_time','event_time']
    if not set(want).issubset(df.columns):
        df=pd.read_csv(io.BytesIO(raw),header=None)
        if df.shape[1]<7: raise RuntimeError('bad bookTicker columns')
        df=df.iloc[:,:7]; df.columns=want
        # if first row was actually a header, numeric conversion below drops it
    for c in ['best_bid_price','best_bid_qty','best_ask_price','best_ask_qty','event_time']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['event_time','best_bid_price','best_ask_price']).copy()
    df['ts']=df.event_time.astype(np.int64)
    df=df.sort_values(['ts','update_id' if 'update_id' in df.columns else 'ts']).drop_duplicates('ts',keep='last')
    return df[['ts','best_bid_price','best_bid_qty','best_ask_price','best_ask_qty']].reset_index(drop=True)

def read_trades(symbol,day):
    raw=get_zip('aggTrades',symbol,day)
    df=pd.read_csv(io.BytesIO(raw))
    cols=[str(x).lower() for x in df.columns]
    if not any('price'==x for x in cols):
        df=pd.read_csv(io.BytesIO(raw),header=None)
        df=df.iloc[:,:7]
        df.columns=['agg_id','price','qty','first_id','last_id','timestamp','buyer_maker']
    else:
        mp={}
        for c in df.columns:
            lc=str(c).lower().strip()
            if lc=='price':mp[c]='price'
            elif lc in ('quantity','qty'):mp[c]='qty'
            elif lc in ('timestamp','time'):mp[c]='timestamp'
            elif 'buyer' in lc and 'maker' in lc:mp[c]='buyer_maker'
        df=df.rename(columns=mp)
        if 'buyer_maker' not in df.columns and len(df.columns)>=7:df=df.rename(columns={df.columns[6]:'buyer_maker'})
    for c in ['price','qty','timestamp']:df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['price','qty','timestamp']).copy()
    df['buyer_maker']=df.buyer_maker.astype(str).str.lower().isin(['true','1','t'])
    df['ts']=df.timestamp.astype(np.int64)
    return df[['ts','price','qty','buyer_maker']].sort_values('ts').reset_index(drop=True)

def bbo_at(b,ts):
    a=b.ts.values; i=np.searchsorted(a,ts,side='right')-1
    return None if i<0 else b.iloc[i]

def flow_activity(t, sig):
    a=t.ts.values
    lo5=np.searchsorted(a,sig-5000,'left'); hi=np.searchsorted(a,sig,'right')
    x=t.iloc[lo5:hi]
    act=len(x)
    lo1=np.searchsorted(a,sig-1000,'left'); y=t.iloc[lo1:hi]
    if len(y)==0:return 0.0,act
    n=(y.price*y.qty).values; sg=np.where(y.buyer_maker.values,-n,n)
    return float(sg.sum()/(n.sum()+1e-12)),act

def fill_time(t,start,end,side,px,queue_ahead,order_qty,qmult,front=False):
    a=t.ts.values; lo=np.searchsorted(a,start,'left'); hi=np.searchsorted(a,end,'right')
    need=order_qty if front else queue_ahead*qmult+order_qty
    done=0.0
    for r in t.iloc[lo:hi].itertuples(index=False):
        ok=(side=='buy' and r.buyer_maker and r.price<=px+1e-15) or (side=='sell' and (not r.buyer_maker) and r.price>=px-1e-15)
        if ok:
            done+=r.qty
            if done>=need:return int(r.ts)
    return None

def simulate(b,t,cfg,front=False):
    if len(b)<2 or len(t)<2:return pd.DataFrame()
    start=max(int(b.ts.min()),int(t.ts.min())+5000); end=min(int(b.ts.max()),int(t.ts.max()))
    sigs=np.arange(((start+DECISION_MS-1)//DECISION_MS)*DECISION_MS,end,DECISION_MS,dtype=np.int64)
    out=[]; free=start
    for sig in sigs:
        if sig<free:continue
        r=bbo_at(b,sig)
        if r is None:continue
        mid=(r.best_bid_price+r.best_ask_price)/2
        spread=(r.best_ask_price-r.best_bid_price)/mid*1e4
        if spread<4.25:continue
        qimb=(r.best_bid_qty-r.best_ask_qty)/(r.best_bid_qty+r.best_ask_qty+1e-12)
        fl,act=flow_activity(t,sig)
        if act<3:continue
        choices=[]
        if qimb>=cfg['qmin'] and fl>=-cfg['flowcap']:choices.append(('buy',qimb))
        if qimb<=-cfg['qmin'] and fl<=cfg['flowcap']:choices.append(('sell',-qimb))
        if not choices:continue
        side=max(choices,key=lambda z:z[1])[0]
        place=sig+cfg['lat']; p=bbo_at(b,place)
        if p is None:continue
        px=float(p.best_bid_price if side=='buy' else p.best_ask_price)
        qa=float(p.best_bid_qty if side=='buy' else p.best_ask_qty)
        oq=ORDER_NOTIONAL/px
        ft=fill_time(t,place,min(place+ENTRY_TTL_MS,end),side,px,qa,oq,cfg['qmult'],front)
        if ft is None:continue
        f=bbo_at(b,ft)
        if f is None:continue
        mark1=((float(f.best_bid_price+f.best_ask_price)/2-px)/px*1e4) if side=='buy' else ((px-float(f.best_bid_price+f.best_ask_price)/2)/px*1e4)
        estart=ft+cfg['lat']; er=bbo_at(b,estart)
        if er is None:continue
        es='sell' if side=='buy' else 'buy'
        epx=float(er.best_ask_price if es=='sell' else er.best_bid_price)
        eq=float(er.best_ask_qty if es=='sell' else er.best_bid_qty); eoq=ORDER_NOTIONAL/epx
        eft=fill_time(t,estart,min(estart+EXIT_TTL_MS,end),es,epx,eq,eoq,cfg['qmult'],front)
        if eft is not None:
            gross=((epx-px)/px*1e4)*(1 if side=='buy' else -1); fees=4.0; mode='MM'; close=eft
        else:
            close=min(estart+EXIT_TTL_MS,end); cr=bbo_at(b,close)
            if cr is None:continue
            cpx=float(cr.best_bid_price if side=='buy' else cr.best_ask_price)
            gross=((cpx-px)/px*1e4)*(1 if side=='buy' else -1); fees=7.0; mode='MT'
        out.append({'net_bp':gross-fees,'gross_bp':gross,'mode':mode,'markout_fill_bp':mark1,'spread_signal_bp':spread,'side':side,'fill_ts':ft,'close_ts':close})
        free=close
    return pd.DataFrame(out)

def stat(d):
    if len(d)==0:return {'n':0,'net':np.nan,'gross':np.nan,'mm':np.nan,'mark':np.nan,'ci':np.nan}
    x=d.net_bp.values.astype(float); n=len(x); se=x.std(ddof=1)/math.sqrt(n) if n>1 else np.nan
    return {'n':n,'net':x.mean(),'gross':d.gross_bp.mean(),'mm':(d.mode=='MM').mean(),'mark':d.markout_fill_bp.mean(),'ci':x.mean()-1.96*se if np.isfinite(se) else np.nan}

def main():
    rows=[]; diag=[]
    for s in SYMBOLS:
        cache={}
        for day in DAYS:
            print('loading',s,day,flush=True)
            try:
                b=read_book(s,day); t=read_trades(s,day)
            except Exception as e:
                diag.append({'symbol':s,'day':day,'error':repr(e)}); continue
            if len(b):
                mids=(b.best_bid_price+b.best_ask_price)/2
                spr=(b.best_ask_price-b.best_bid_price)/mids*1e4
                diag.append({'symbol':s,'day':day,'book_rows':len(b),'trades':len(t),'median_spread_bp':float(spr.median()),'p75_spread_bp':float(spr.quantile(.75))})
            cache[day]=(b,t)
        if TRAIN_DAY not in cache:continue
        # Train all predefined configs on one day, then evaluate only top 8 on two untouched days.
        train=[]
        for j,c in enumerate(CONFIGS):
            d=simulate(*cache[TRAIN_DAY],c,front=False); st=stat(d)
            train.append((j,c,st))
        ranked=sorted([x for x in train if x[2]['n']>=10], key=lambda x:(x[2]['net'],x[2]['n']), reverse=True)[:8]
        for j,c,tr in ranked:
            parts=[]
            for day in OOS_DAYS:
                if day in cache:
                    d=simulate(*cache[day],c,front=False)
                    if len(d):parts.append(d.assign(day=day))
            oo=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()
            so=stat(oo)
            rows.append({'symbol':s,'rid':j,**c,'train_n':tr['n'],'train_net_bp':tr['net'],'train_mm_rate':tr['mm'],'oos_n':so['n'],'oos_net_bp':so['net'],'oos_gross_bp':so['gross'],'oos_mm_rate':so['mm'],'oos_fill_markout_bp':so['mark'],'oos_ci95_low_net':so['ci']})
        # front-of-queue upper bound for the best 3 train configs
        for j,c,tr in ranked[:3]:
            parts=[]
            for day in OOS_DAYS:
                if day in cache:
                    d=simulate(*cache[day],c,front=True)
                    if len(d):parts.append(d)
            so=stat(pd.concat(parts,ignore_index=True) if parts else pd.DataFrame())
            rows.append({'symbol':s,'rid':j,**c,'train_n':tr['n'],'train_net_bp':tr['net'],'train_mm_rate':tr['mm'],'oos_n':so['n'],'oos_net_bp':so['net'],'oos_gross_bp':so['gross'],'oos_mm_rate':so['mm'],'oos_fill_markout_bp':so['mark'],'oos_ci95_low_net':so['ci'],'upper_bound_front':True})
    R=pd.DataFrame(rows); D=pd.DataFrame(diag); R.to_csv(f'{OUT}/results.csv',index=False); D.to_csv(f'{OUT}/diagnostics.csv',index=False)
    if 'upper_bound_front' not in R.columns:R['upper_bound_front']=False
    R['upper_bound_front']=R.upper_bound_front.fillna(False)
    cons=R[~R.upper_bound_front].copy(); pos=cons[(cons.oos_n>=10)&(cons.oos_net_bp>=0)].sort_values(['oos_net_bp','oos_n'],ascending=[False,False]) if len(cons) else cons
    best=cons.sort_values(['oos_net_bp','oos_n'],ascending=[False,False]) if len(cons) else cons
    front=R[R.upper_bound_front].sort_values('oos_net_bp',ascending=False)
    lines=['# Historical wide-tick maker v5','', '- Data: Binance Vision USDⓈ-M bookTicker + aggTrades, 2024-03-29..31.','- Train: 2024-03-29; OOS: 2024-03-30..31.','- $100 orders; decision every 2s; entry TTL 20s; maker exit TTL 60s.','- Conservative fill requires displayed L1 queue × qmult + own size to trade through; no cancellation credit.','- Fees use current research assumption: maker 2bp/side, emergency taker 5bp.','- This is structural validation on 2024 microstructure, not proof that 2026 opportunity persists.','', '## Archive diagnostics','']
    if len(D):lines.append(D.to_markdown(index=False))
    lines+=['','## OOS conservative PASS (n>=10, net after fees >=0)','']
    if len(pos):lines.append(pos.head(12).to_markdown(index=False,floatfmt='.3f'))
    else:lines.append('None.')
    lines+=['','## Best conservative OOS regardless of sign','']
    if len(best):lines.append(best.head(12).to_markdown(index=False,floatfmt='.3f'))
    else:lines.append('None.')
    lines+=['','## Front-of-queue upper bound','']
    if len(front):lines.append(front.head(9).to_markdown(index=False,floatfmt='.3f'))
    else:lines.append('None.')
    open(f'{OUT}/summary.md','w').write('\n'.join(lines)); print('\n'.join(lines))

if __name__=='__main__':main()
