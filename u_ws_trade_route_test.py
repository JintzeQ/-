import asyncio,json,os,time
import websockets
OUT='u_ws_trade_route_output';os.makedirs(OUT,exist_ok=True)
URLS={'standard_raw':'wss://fstream.binance.com/ws/btcu@trade','standard_combined':'wss://fstream.binance.com/stream?streams=btcu@trade','market_raw':'wss://fstream.binance.com/market/ws/btcu@trade','public_raw':'wss://fstream.binance.com/public/ws/btcu@trade'}
async def test(name,url):
 out={'name':name,'url':url,'connected':False,'messages':0,'trade':0,'errors':[],'sample':None};stop=time.monotonic()+35
 try:
  async with websockets.connect(url,ping_interval=10,ping_timeout=10,max_queue=10000) as ws:
   out['connected']=True
   while time.monotonic()<stop:
    try:raw=await asyncio.wait_for(ws.recv(),3)
    except asyncio.TimeoutError:continue
    out['messages']+=1
    try:o=json.loads(raw);d=o.get('data',o)
    except Exception:d={}
    if d.get('e')=='trade':out['trade']+=1;out['sample']=out['sample'] or d
 except Exception as e:out['errors'].append(repr(e))
 return out
async def main():return await asyncio.gather(*(test(k,v) for k,v in URLS.items()))
r=asyncio.run(main());open(f'{OUT}/routes.json','w').write(json.dumps(r,indent=2));lines=['# BTCU raw trade WebSocket route test','','| route | connected | messages | trade | error |','|---|---:|---:|---:|---|']
for x in r:lines.append(f"| {x['name']} | {x['connected']} | {x['messages']} | {x['trade']} | {str(x['errors'][:1])} |")
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
