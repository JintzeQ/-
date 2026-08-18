import asyncio, json, time
import websockets

async def raw(name):
    url='wss://fstream.binance.com/ws/'+name
    n=0; samples=[]; end=time.monotonic()+15
    try:
        async with websockets.connect(url,ping_interval=15,ping_timeout=15) as ws:
            while time.monotonic()<end:
                try: msg=await asyncio.wait_for(ws.recv(),timeout=2)
                except asyncio.TimeoutError: continue
                n+=1
                if len(samples)<3: samples.append(json.loads(msg))
    except Exception as e:
        return {'stream':name,'n':n,'error':repr(e),'samples':samples}
    return {'stream':name,'n':n,'samples':samples}

async def main():
    out=await asyncio.gather(raw('fetusdt@aggTrade'),raw('fetusdt@bookTicker'))
    print(json.dumps(out,indent=2))

asyncio.run(main())
