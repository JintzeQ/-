#!/usr/bin/env python3
import pandas as pd
from u_shadow_analyzer import simulate_symbol

# One stable ordinary BBO. Local receive is 2ms after exchange event; order arrives 10ms later.
b=pd.DataFrame([
 {'id':1,'symbol':'BTCU','exchange_ms':1000,'recv_wall_ns':1002_000_000,'recv_mono_ns':1002_000_000,'bid':100.0,'bid_qty':1.0,'ask':101.0,'ask_qty':1.0},
 {'id':2,'symbol':'BTCU','exchange_ms':1050,'recv_wall_ns':1052_000_000,'recv_mono_ns':1052_000_000,'bid':100.0,'bid_qty':1.0,'ask':101.0,'ask_qty':1.0},
 {'id':3,'symbol':'BTCU','exchange_ms':2500,'recv_wall_ns':2502_000_000,'recv_mono_ns':2502_000_000,'bid':100.0,'bid_qty':1.0,'ask':101.0,'ask_qty':1.0},
])
# $100 buy at 100 => own qty 1. Queue ahead 1, so 2 units must execute at the bid.
# The huge RPI print must NOT count. Two ordinary prints of 0.8 and 1.2 must cumulatively fill.
t=pd.DataFrame([
 {'symbol':'BTCU','trade_id':1,'exchange_ms':1015,'recv_wall_ns':1020_000_000,'recv_mono_ns':1020_000_000,'price':100.0,'qty':100.0,'buyer_maker':1,'is_rpi':1},
 {'symbol':'BTCU','trade_id':2,'exchange_ms':1020,'recv_wall_ns':1025_000_000,'recv_mono_ns':1025_000_000,'price':100.0,'qty':0.8,'buyer_maker':1,'is_rpi':0},
 {'symbol':'BTCU','trade_id':3,'exchange_ms':1030,'recv_wall_ns':1035_000_000,'recv_mono_ns':1035_000_000,'price':100.0,'qty':1.2,'buyer_maker':1,'is_rpi':0},
])
base,fills,q=simulate_symbol(b,t,offset_ms=0.0,latency_ms=10.0,order_usd=100.0,cap_usd=100.0,min_spread_bp=0.0,cancel_credit=0.0)
assert base['maker_fills']==1,base
assert len(fills)==1,fills
f=fills.iloc[0]
assert f.side=='buy',f
assert abs(f.fill_ms-1030)<1e-9,f
assert abs(f.notional-100.0)<1e-9,f
# If RPI had incorrectly depleted the queue the fill would have occurred at 1015.
print('u_shadow_selftest PASS')
