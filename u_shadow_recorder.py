#!/usr/bin/env python3
"""Public-data-only recorder for BTCU/ETHU maker research.

Records ordinary (RPI-excluded) BBO from Futures bookTicker plus recent market
trades from REST, preserving isRPITrade. No orders are submitted.

Designed to run on the user's low-latency cloud host, not GitHub Actions (Binance
REST may return HTTP 451 from hosted CI regions).
"""
import asyncio
import json
import os
import signal
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import requests
import websockets

SYMBOLS=[x.strip().upper() for x in os.getenv('SHADOW_SYMBOLS','BTCU,ETHU').split(',') if x.strip()]
DB_PATH=Path(os.getenv('SHADOW_DB','u_shadow.sqlite3'))
POLL_SECONDS=float(os.getenv('SHADOW_TRADE_POLL_SECONDS','0.5'))
DURATION_SECONDS=float(os.getenv('SHADOW_DURATION_SECONDS','0'))  # 0 = until Ctrl-C
REST_BASE=os.getenv('BINANCE_FAPI_REST','https://fapi.binance.com').rstrip('/')
WS_BASE=os.getenv('BINANCE_FAPI_WS','wss://fstream.binance.com/public/stream?streams=')
TRADE_LIMIT=int(os.getenv('SHADOW_TRADE_LIMIT','1000'))
TIME_SYNC_SECONDS=float(os.getenv('SHADOW_TIME_SYNC_SECONDS','10'))
COMMIT_SECONDS=float(os.getenv('SHADOW_COMMIT_SECONDS','1'))

streams='/'.join(f'{s.lower()}@bookTicker' for s in SYMBOLS)
BOOK_URL=WS_BASE+streams
stop_event=asyncio.Event()
session=requests.Session()
session.headers.update({'User-Agent':'u-shadow-recorder/1.0','Cache-Control':'no-cache','Pragma':'no-cache'})


def now_wall_ns(): return time.time_ns()
def now_mono_ns(): return time.monotonic_ns()


def init_db():
    con=sqlite3.connect(DB_PATH,timeout=30)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bbo(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL, exchange_ms INTEGER NOT NULL,
      recv_wall_ns INTEGER NOT NULL, recv_mono_ns INTEGER NOT NULL,
      bid REAL NOT NULL,bid_qty REAL NOT NULL,ask REAL NOT NULL,ask_qty REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_bbo_symbol_exchange ON bbo(symbol,exchange_ms,id);
    CREATE TABLE IF NOT EXISTS trades(
      symbol TEXT NOT NULL, trade_id INTEGER NOT NULL,
      exchange_ms INTEGER NOT NULL, recv_wall_ns INTEGER NOT NULL, recv_mono_ns INTEGER NOT NULL,
      price REAL NOT NULL,qty REAL NOT NULL,buyer_maker INTEGER NOT NULL,is_rpi INTEGER NOT NULL,
      PRIMARY KEY(symbol,trade_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trades_symbol_exchange ON trades(symbol,exchange_ms,trade_id);
    CREATE TABLE IF NOT EXISTS polls(
      id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,
      start_wall_ns INTEGER NOT NULL,end_wall_ns INTEGER NOT NULL,start_mono_ns INTEGER NOT NULL,end_mono_ns INTEGER NOT NULL,
      http_status INTEGER,rows_seen INTEGER,new_rows INTEGER,rpi_rows INTEGER,
      used_weight_1m TEXT,error TEXT
    );
    CREATE TABLE IF NOT EXISTS clock_sync(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      request_wall_ns INTEGER NOT NULL,response_wall_ns INTEGER NOT NULL,
      request_mono_ns INTEGER NOT NULL,response_mono_ns INTEGER NOT NULL,
      server_ms INTEGER,http_status INTEGER,error TEXT
    );
    ''')
    meta={
      'symbols':','.join(SYMBOLS),'book_url':BOOK_URL,'rest_base':REST_BASE,
      'trade_poll_seconds':str(POLL_SECONDS),'started_wall_ns':str(now_wall_ns()),
      'recorder_version':'1.0','purpose':'shadow_only_no_orders'
    }
    con.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',meta.items())
    con.commit(); return con


def rest_recent(symbol):
    a_w,a_m=now_wall_ns(),now_mono_ns()
    try:
        r=session.get(REST_BASE+'/fapi/v1/trades',params={'symbol':symbol,'limit':TRADE_LIMIT},timeout=8)
        b_w,b_m=now_wall_ns(),now_mono_ns()
        data=r.json() if r.status_code==200 else []
        return r.status_code,data,r.headers.get('X-MBX-USED-WEIGHT-1M'),None,a_w,b_w,a_m,b_m
    except Exception as e:
        b_w,b_m=now_wall_ns(),now_mono_ns()
        return None,[],None,repr(e),a_w,b_w,a_m,b_m


def rest_time():
    a_w,a_m=now_wall_ns(),now_mono_ns()
    try:
        r=session.get(REST_BASE+'/fapi/v1/time',timeout=5); b_w,b_m=now_wall_ns(),now_mono_ns()
        server_ms=int(r.json()['serverTime']) if r.status_code==200 else None
        return a_w,b_w,a_m,b_m,server_ms,r.status_code,None
    except Exception as e:
        b_w,b_m=now_wall_ns(),now_mono_ns(); return a_w,b_w,a_m,b_m,None,None,repr(e)


async def book_loop(q):
    backoff=1.0
    while not stop_event.is_set():
        try:
            async with websockets.connect(BOOK_URL,ping_interval=10,ping_timeout=10,max_queue=500000) as ws:
                backoff=1.0
                while not stop_event.is_set():
                    try: raw=await asyncio.wait_for(ws.recv(),timeout=3)
                    except asyncio.TimeoutError: continue
                    rw,rm=now_wall_ns(),now_mono_ns()
                    o=json.loads(raw); d=o.get('data',o); s=d.get('s')
                    if s in SYMBOLS and all(k in d for k in ('b','B','a','A')):
                        ex=int(d.get('T',d.get('E',rw//1_000_000)))
                        await q.put(('bbo',(s,ex,rw,rm,float(d['b']),float(d['B']),float(d['a']),float(d['A']))))
        except Exception as e:
            await q.put(('meta',('last_book_error',repr(e))))
            await asyncio.sleep(backoff); backoff=min(backoff*2,15)


async def trade_loop(q):
    last_id=defaultdict(lambda:-1)
    # Seed cursor, but do not record pre-start history.
    for s in SYMBOLS:
        status,data,weight,err,*_=await asyncio.to_thread(rest_recent,s)
        if status==200 and data: last_id[s]=max(int(x['id']) for x in data)
        else: await q.put(('meta',(f'seed_error_{s}',str(err or status))))
    while not stop_event.is_set():
        cycle=time.monotonic()
        for s in SYMBOLS:
            status,data,weight,err,aw,bw,am,bm=await asyncio.to_thread(rest_recent,s)
            new=[]; rpi=0
            if status==200 and isinstance(data,list):
                for x in data:
                    tid=int(x['id'])
                    if tid<=last_id[s]: continue
                    is_rpi=bool(x.get('isRPITrade',False)); rpi+=int(is_rpi)
                    new.append((s,tid,int(x['time']),bw,bm,float(x['price']),float(x['qty']),int(bool(x['isBuyerMaker'])),int(is_rpi)))
                if data: last_id[s]=max(last_id[s],max(int(x['id']) for x in data))
            await q.put(('poll',(s,aw,bw,am,bm,status,len(data) if isinstance(data,list) else 0,len(new),rpi,weight,err)))
            for row in new: await q.put(('trade',row))
            if status in (418,429):
                await q.put(('meta',('rate_limit_event',f'{s}:{status}:{weight}')))
                await asyncio.sleep(max(2.0,POLL_SECONDS*4))
        sleep=max(0.0,POLL_SECONDS-(time.monotonic()-cycle))
        try: await asyncio.wait_for(stop_event.wait(),timeout=sleep)
        except asyncio.TimeoutError: pass


async def clock_loop(q):
    while not stop_event.is_set():
        row=await asyncio.to_thread(rest_time); await q.put(('clock',row))
        try: await asyncio.wait_for(stop_event.wait(),timeout=TIME_SYNC_SECONDS)
        except asyncio.TimeoutError: pass


async def writer_loop(q):
    con=init_db(); last_commit=time.monotonic(); counts=defaultdict(int)
    try:
        while not (stop_event.is_set() and q.empty()):
            try: kind,row=await asyncio.wait_for(q.get(),timeout=.5)
            except asyncio.TimeoutError:
                if time.monotonic()-last_commit>=COMMIT_SECONDS: con.commit(); last_commit=time.monotonic()
                continue
            if kind=='bbo': con.execute('INSERT INTO bbo(symbol,exchange_ms,recv_wall_ns,recv_mono_ns,bid,bid_qty,ask,ask_qty) VALUES(?,?,?,?,?,?,?,?)',row)
            elif kind=='trade': con.execute('INSERT OR IGNORE INTO trades(symbol,trade_id,exchange_ms,recv_wall_ns,recv_mono_ns,price,qty,buyer_maker,is_rpi) VALUES(?,?,?,?,?,?,?,?,?)',row)
            elif kind=='poll': con.execute('INSERT INTO polls(symbol,start_wall_ns,end_wall_ns,start_mono_ns,end_mono_ns,http_status,rows_seen,new_rows,rpi_rows,used_weight_1m,error) VALUES(?,?,?,?,?,?,?,?,?,?,?)',row)
            elif kind=='clock': con.execute('INSERT INTO clock_sync(request_wall_ns,response_wall_ns,request_mono_ns,response_mono_ns,server_ms,http_status,error) VALUES(?,?,?,?,?,?,?)',row)
            elif kind=='meta': con.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',row)
            counts[kind]+=1; q.task_done()
            if time.monotonic()-last_commit>=COMMIT_SECONDS: con.commit(); last_commit=time.monotonic()
    finally:
        con.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',('stopped_wall_ns',str(now_wall_ns())))
        for k,v in counts.items(): con.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',(f'count_{k}',str(v)))
        con.commit(); con.close()


async def main():
    q=asyncio.Queue(maxsize=1_000_000)
    writer=asyncio.create_task(writer_loop(q))
    tasks=[asyncio.create_task(book_loop(q)),asyncio.create_task(trade_loop(q)),asyncio.create_task(clock_loop(q))]
    if DURATION_SECONDS>0:
        asyncio.get_running_loop().call_later(DURATION_SECONDS,stop_event.set)
    await stop_event.wait()
    for t in tasks: t.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    await q.join(); await writer


def request_stop(*_): stop_event.set()

if __name__=='__main__':
    for sig in (signal.SIGINT,signal.SIGTERM):
        try: signal.signal(sig,lambda *_: request_stop())
        except Exception: pass
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
