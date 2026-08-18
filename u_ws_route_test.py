import asyncio,json,os,time
import websockets
OUT='u_ws_route_output';os.makedirs(OUT,exist_ok=True)
URLS={
 'standard_raw':'wss://fstream.binance.com/ws/btcu@aggTrade',
 'standard_combined':'wss://fstream.binance.com/stream?streams=btcu@aggTrade',
 'market_raw':'wss://fstream.binance.com/market/ws/btcu@aggTrade',
 'public_raw':'wss://fstream.binance.com/public/ws/btcu@aggTrade',
}
async def test(name,url):
 out={'name':name,'url':url,'connected':False,'messages':0,'aggtrade':0,'errors':[],'sample':None}
 stop=time.monotonic()+35
 try:
  async with websockets.connect(url,ping_interval=10,ping_timeout=10,max_queue=10000) as ws:
   out['connected']=True
   while time.monotonic()<stop:
    try:raw=await asyncio.wait_for(ws.recv(),timeout=3)
    except asyncio.TimeoutError:continue
    out['messages']+=1
    try:o=json.loads(raw);d=o.get('data',o)
    except Exception:d={}
    if d.get('e')=='aggTrade':
     out['aggtrade']+=1
     if out['sample'] is None:out['sample']=d
 except Exception as e:out['errors'].append(repr(e))
 return out
async def main():return await asyncio.gather(*(test(k,v) for k,v in URLS.items()))
r=asyncio.run(main());open(f'{OUT}/routes.json','w').write(json.dumps(r,indent=2));
lines=['# BTCU WebSocket route test','', '| route | connected | messages | aggTrade | error |','|---|---:|---:|---:|---|']
for x in r:lines.append(f"| {x['name']} | {x['connected']} | {x['messages']} | {x['aggtrade']} | {str(x['errors'][:1])} |")
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
