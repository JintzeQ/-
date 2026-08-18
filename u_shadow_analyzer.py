#!/usr/bin/env python3
"""Analyze u_shadow_recorder.py data with an event-driven conservative maker model.

Key points:
- decision can only happen after the BBO was locally received;
- estimated exchange arrival = local receive time + measured clock offset + order latency;
- quote only gets queue priority if its observed price is still the ordinary visible touch at arrival;
- only non-RPI trades deplete the ordinary visible queue;
- trades through our price fill immediately; trades at our price cumulatively consume queue + our own full size;
- optional cancellation credit is a stress scenario, default 0;
- residual inventory is forcibly flattened at the final ordinary touch.
"""
import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FEE_SCENARIOS={
    'u_promo_conditional':(0.0,4.0),
    'standard_user_fee':(2.0,5.0),
}

@dataclass
class Order:
    side:str
    px:float
    own_qty:float
    queue_ahead:float
    remaining_to_full:float
    placed_ms:float
    observed_recv_ms:float


def load_db(path):
    con=sqlite3.connect(path)
    b=pd.read_sql_query('SELECT * FROM bbo ORDER BY exchange_ms,id',con)
    t=pd.read_sql_query('SELECT * FROM trades ORDER BY exchange_ms,trade_id',con)
    c=pd.read_sql_query('SELECT * FROM clock_sync ORDER BY request_wall_ns',con)
    p=pd.read_sql_query('SELECT * FROM polls ORDER BY start_wall_ns',con)
    m=pd.read_sql_query('SELECT key,value FROM meta',con)
    con.close()
    return b,t,c,p,dict(zip(m.key,m.value))


def clock_offset_ms(c):
    good=c[(c.server_ms.notna())&(c.http_status==200)].copy()
    if len(good)==0:return 0.0,pd.DataFrame()
    good['mid_local_ms']=(good.request_wall_ns+good.response_wall_ns)/2/1e6
    good['rtt_ms']=(good.response_mono_ns-good.request_mono_ns)/1e6
    good['offset_ms']=good.server_ms-good.mid_local_ms
    cutoff=good.rtt_ms.quantile(.5) if len(good)>3 else good.rtt_ms.max()
    use=good[good.rtt_ms<=cutoff]
    return float(use.offset_ms.median()),good


def bbo_at_arrays(bt,bid,bq,ask,aq,ts):
    i=np.searchsorted(bt,ts,side='right')-1
    if i<0:return None
    return i,float(bid[i]),float(bq[i]),float(ask[i]),float(aq[i])


def future_mid(bt,bid,ask,ts):
    i=np.searchsorted(bt,ts,side='right')-1
    if i<0:return np.nan
    return (float(bid[i])+float(ask[i]))/2


def simulate_symbol(b,t,offset_ms,latency_ms,order_usd,cap_usd,min_spread_bp,cancel_credit):
    b=b.sort_values(['exchange_ms','id']).reset_index(drop=True)
    t=t[(t.is_rpi==0)].sort_values(['exchange_ms','trade_id']).reset_index(drop=True)
    if len(b)<2:return {},pd.DataFrame(),pd.DataFrame()
    bt=b.exchange_ms.to_numpy(float); bid=b.bid.to_numpy(float); bq=b.bid_qty.to_numpy(float); ask=b.ask.to_numpy(float); aq=b.ask_qty.to_numpy(float)
    events=[]
    for r in b.itertuples(index=False): events.append((float(r.exchange_ms),0,'bbo',r))
    for r in t.itertuples(index=False): events.append((float(r.exchange_ms),1,'trade',r))
    for r in b.itertuples(index=False):
        recv_ms=r.recv_wall_ns/1e6 + offset_ms
        events.append((float(recv_ms+latency_ms),2,'action',r))
    events.sort(key=lambda z:(z[0],z[1]))

    cur_bid=cur_ask=cur_bq=cur_aq=None
    orders={'buy':None,'sell':None}
    cash=0.0; pos=0.0; maker_notional=0.0; max_inventory=0.0
    quote_actions=0; rejects_stale=0; fills=[]; qevents=[]

    def inventory_usd(mid): return pos*mid
    def remove(side,reason,ts):
        o=orders[side]
        if o is not None:qevents.append({'ts':ts,'side':side,'event':'cancel','reason':reason,'px':o.px,'queue_remaining':o.queue_ahead,'remaining_to_full':o.remaining_to_full})
        orders[side]=None

    for ts,_,kind,r in events:
        if kind=='bbo':
            old_bid,old_ask,old_bq,old_aq=cur_bid,cur_ask,cur_bq,cur_aq
            cur_bid,cur_bq,cur_ask,cur_aq=float(r.bid),float(r.bid_qty),float(r.ask),float(r.ask_qty)
            # Cancellation credit is optional and only same-touch displayed shrink can receive it.
            if cancel_credit>0:
                o=orders['buy']
                if o is not None and old_bid==cur_bid==o.px and old_bq is not None and cur_bq<old_bq:
                    credit=min(o.queue_ahead,max(0.0,old_bq-cur_bq)*cancel_credit)
                    o.queue_ahead-=credit;o.remaining_to_full=max(o.own_qty,o.remaining_to_full-credit)
                    qevents.append({'ts':ts,'side':'buy','event':'cancel_credit','reason':'same_touch_shrink','px':o.px,'queue_remaining':o.queue_ahead,'remaining_to_full':o.remaining_to_full})
                o=orders['sell']
                if o is not None and old_ask==cur_ask==o.px and old_aq is not None and cur_aq<old_aq:
                    credit=min(o.queue_ahead,max(0.0,old_aq-cur_aq)*cancel_credit)
                    o.queue_ahead-=credit;o.remaining_to_full=max(o.own_qty,o.remaining_to_full-credit)
                    qevents.append({'ts':ts,'side':'sell','event':'cancel_credit','reason':'same_touch_shrink','px':o.px,'queue_remaining':o.queue_ahead,'remaining_to_full':o.remaining_to_full})
            continue

        if kind=='trade':
            px=float(r.price); qty=float(r.qty); bm=bool(r.buyer_maker)
            candidates=[]
            if bm and orders['buy'] is not None and px<=orders['buy'].px+1e-15:candidates.append('buy')
            if (not bm) and orders['sell'] is not None and px>=orders['sell'].px-1e-15:candidates.append('sell')
            for side in candidates:
                o=orders[side]
                through=(px<o.px-1e-15) if side=='buy' else (px>o.px+1e-15)
                if through:
                    o.queue_ahead=0.0;o.remaining_to_full=0.0
                else:
                    o.queue_ahead=max(0.0,o.queue_ahead-qty)
                    o.remaining_to_full=max(0.0,o.remaining_to_full-qty)
                if o.remaining_to_full<=1e-12:
                    n=o.px*o.own_qty
                    if side=='buy': cash-=n;pos+=o.own_qty
                    else: cash+=n;pos-=o.own_qty
                    maker_notional+=n
                    mid=future_mid(bt,bid,ask,ts)
                    fills.append({'fill_ms':ts,'side':side,'px':o.px,'qty':o.own_qty,'notional':n,'queue_wait_ms':ts-o.placed_ms,'through':through,'inventory_qty_after':pos,'mid_at_fill':mid,'observed_recv_ms':o.observed_recv_ms})
                    orders[side]=None
                else:
                    qevents.append({'ts':ts,'side':side,'event':'trade_queue','reason':'at_touch','px':o.px,'queue_remaining':o.queue_ahead,'remaining_to_full':o.remaining_to_full})
            if cur_bid is not None:max_inventory=max(max_inventory,abs(inventory_usd((cur_bid+cur_ask)/2)))
            continue

        # Reaction to a BBO actually received locally; this action reaches exchange after latency_ms.
        observed_bid=float(r.bid);observed_ask=float(r.ask)
        observed_mid=(observed_bid+observed_ask)/2;spread_bp=(observed_ask-observed_bid)/observed_mid*1e4
        inv=inventory_usd(observed_mid); max_inventory=max(max_inventory,abs(inv))
        buy_ok=spread_bp>=min_spread_bp and inv<cap_usd and inv<=0.5*cap_usd
        sell_ok=spread_bp>=min_spread_bp and inv>-cap_usd and inv>=-0.5*cap_usd
        for side,ok,target in [('buy',buy_ok,observed_bid),('sell',sell_ok,observed_ask)]:
            current=orders[side]
            if not ok:
                if current is not None:remove(side,'inventory_or_spread_gate',ts)
                continue
            if current is not None and abs(current.px-target)<=1e-15:continue
            if current is not None:remove(side,'requote',ts)
            state=bbo_at_arrays(bt,bid,bq,ask,aq,ts)
            if state is None:continue
            _,abid,abq,aask,aaq=state
            still_touch=(abs(target-abid)<=1e-15) if side=='buy' else (abs(target-aask)<=1e-15)
            if not still_touch:
                rejects_stale+=1;continue
            own=order_usd/target;queue=abq if side=='buy' else aaq
            orders[side]=Order(side,target,own,queue,queue+own,float(ts),r.recv_wall_ns/1e6+offset_ms)
            quote_actions+=1
            qevents.append({'ts':ts,'side':side,'event':'place','reason':'new_touch','px':target,'queue_remaining':queue,'remaining_to_full':queue+own})

    last_bid,last_ask=float(bid[-1]),float(ask[-1]);liq_notional=0.0
    if abs(pos)>1e-15:
        if pos>0:
            liq_notional=pos*last_bid;cash+=liq_notional
        else:
            liq_notional=(-pos)*last_ask;cash-=liq_notional
    F=pd.DataFrame(fills)
    if len(F):
        for h in (1000,5000,15000):
            vals=[]
            for x in F.itertuples(index=False):
                m=future_mid(bt,bid,ask,x.fill_ms+h)
                vals.append(((m-x.px)/x.px*1e4) if x.side=='buy' else ((x.px-m)/x.px*1e4))
            F[f'markout_{h//1000}s_bp']=vals
    base={'quote_actions':quote_actions,'stale_arrival_rejects':rejects_stale,'maker_fills':len(F),'maker_notional':maker_notional,'liquidation_notional':liq_notional,'total_notional':maker_notional+liq_notional,'gross_cash_pnl':cash,'max_inventory_usd':max_inventory}
    return base,F,pd.DataFrame(qevents)


def summarize_latency(b,offset):
    if len(b)==0:return {}
    x=(b.recv_wall_ns/1e6+offset)-b.exchange_ms
    return {'n_bbo':len(x),'feed_latency_p50_ms':float(x.quantile(.5)),'feed_latency_p90_ms':float(x.quantile(.9)),'feed_latency_p99_ms':float(x.quantile(.99)),'feed_latency_max_ms':float(x.max())}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='u_shadow.sqlite3')
    ap.add_argument('--out',default='u_shadow_analysis')
    ap.add_argument('--latency-ms',type=float,default=10.0)
    ap.add_argument('--order-usd',type=float,default=100.0)
    ap.add_argument('--inventory-cap-usd',type=float,default=100.0)
    ap.add_argument('--min-spread-bp',type=float,default=0.0)
    ap.add_argument('--cancel-credit',type=float,default=0.0,choices=[0.0,0.25,0.5,0.75,1.0])
    args=ap.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    b,t,c,p,meta=load_db(args.db);offset,clock=clock_offset_ms(c)
    symbols=sorted(set(b.symbol));rows=[];allfills=[];allq=[];latrows=[]
    for s in symbols:
        bs=b[b.symbol==s].copy();ts=t[t.symbol==s].copy()
        lat={'symbol':s,**summarize_latency(bs,offset),'all_trades':len(ts),'non_rpi_trades':int((ts.is_rpi==0).sum()),'rpi_trades':int((ts.is_rpi==1).sum())};latrows.append(lat)
        base,F,Q=simulate_symbol(bs,ts,offset,args.latency_ms,args.order_usd,args.inventory_cap_usd,args.min_spread_bp,args.cancel_credit)
        if len(F):F.insert(0,'symbol',s);allfills.append(F)
        if len(Q):Q.insert(0,'symbol',s);allq.append(Q)
        for fee_name,(maker_bp,taker_bp) in FEE_SCENARIOS.items():
            vol=base.get('total_notional',0.0);fees=base.get('maker_notional',0.0)*maker_bp/1e4+base.get('liquidation_notional',0.0)*taker_bp/1e4
            pnl=base.get('gross_cash_pnl',0.0)-fees
            rows.append({'symbol':s,'fee_scenario':fee_name,'maker_fee_bp':maker_bp,'taker_fee_bp':taker_bp,**base,'fees_usd':fees,'net_pnl_usd':pnl,'net_bp_per_volume':pnl/vol*1e4 if vol else np.nan,'liquidation_share':base.get('liquidation_notional',0.0)/vol if vol else np.nan})
    R=pd.DataFrame(rows);L=pd.DataFrame(latrows);FF=pd.concat(allfills,ignore_index=True) if allfills else pd.DataFrame();QQ=pd.concat(allq,ignore_index=True) if allq else pd.DataFrame()
    R.to_csv(out/'results.csv',index=False);L.to_csv(out/'latency.csv',index=False);FF.to_csv(out/'fills.csv',index=False);QQ.to_csv(out/'queue_events.csv',index=False);clock.to_csv(out/'clock_sync.csv',index=False);p.to_csv(out/'polls.csv',index=False)
    lines=['# U-margined shadow analysis','',f'- DB: `{args.db}`',f'- estimated server-minus-local clock offset: **{offset:.3f} ms**',f'- order-to-exchange latency: **{args.latency_ms:.1f} ms**',f'- order size / inventory cap: **${args.order_usd:.2f} / ${args.inventory_cap_usd:.2f}**',f'- minimum ordinary visible spread: **{args.min_spread_bp:.3f} bp**',f'- same-touch queue cancellation credit: **{args.cancel_credit:.2f}**','- RPI trades are excluded from ordinary visible-queue depletion.','- A quote is placed only if the price observed locally is still the ordinary touch when the order is estimated to reach the exchange.','- At-price trades cumulatively consume displayed queue + the full modeled order; trade-through fills immediately.','- Residual inventory is forcibly flattened at the final ordinary touch.','', '## Data / latency','']
    lines.append(L.to_markdown(index=False,floatfmt='.4f') if len(L) else 'No BBO data.')
    lines+=['','## PnL by fee scenario','']
    lines.append(R.to_markdown(index=False,floatfmt='.4f') if len(R) else 'No results.')
    if len(FF):
        lines+=['','## Fill diagnostics','',FF.groupby(['symbol','side']).agg(fills=('notional','size'),notional=('notional','sum'),queue_wait_p50_ms=('queue_wait_ms','median'),markout_1s_bp=('markout_1s_bp','mean'),markout_5s_bp=('markout_5s_bp','mean'),markout_15s_bp=('markout_15s_bp','mean')).reset_index().to_markdown(index=False,floatfmt='.4f')]
    lines+=['','Interpret a strategy as a candidate only after multiple independent recording windows remain net >= 0 after the applicable fee scenario and liquidation cost. This analyzer is deliberately conservative but still a queue model, not exchange-native order-priority proof.']
    (out/'summary.md').write_text('\n'.join(lines),encoding='utf-8');print('\n'.join(lines))

if __name__=='__main__': main()
