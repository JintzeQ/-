import asyncio, json, time
import websockets

ROUTES=[
 ('legacy_raw','wss://fstream.binance.com/ws/woousdt@aggTrade'),
 ('public_raw','wss://fstream.binance.com/public/ws/woousdt@aggTrade'),
 ('public_combined','wss://fstream.binance.com/public/stream?streams=woousdt@aggTrade/woousdt@bookTicker'),
 ('market_raw','wss://fstream.binance.com/market/ws/woousdt@aggTrade'),
 ('market_combined','wss://fstream.binance.com/market/stream?streams=woousdt@aggTrade/woousdt@bookTicker'),
]

async def test(label,url):
    n=0; samples=[]; end=time.monotonic()+12
    try:
        async with websockets.connect(url,ping_interval=10,ping_timeout=10,open_timeout=8) as ws:
            while time.monotonic()<end:
                try: msg=await asyncio.wait_for(ws.recv(),timeout=2)
                except asyncio.TimeoutError: continue
                n+=1
                try: obj=json.loads(msg)
                except Exception: obj=str(msg)[:500]
                if len(samples)<3:samples.append(obj)
    except Exception as e:
        return {'route':label,'url':url,'n':n,'error':repr(e),'samples':samples}
    return {'route':label,'url':url,'n':n,'samples':samples}

async def main():
    out=await asyncio.gather(*(test(*x) for x in ROUTES))
    print(json.dumps(out,indent=2))

asyncio.run(main())
