import json,os,time,requests
OUT='u_rest_cache_probe_output';os.makedirs(OUT,exist_ok=True)
HOSTS=['https://fapi.binance.com','https://fapi1.binance.com','https://fapi2.binance.com','https://fapi3.binance.com']
s=requests.Session();s.headers.update({'User-Agent':'u-cache-probe/1.0','Cache-Control':'no-cache','Pragma':'no-cache'})
rows=[]
for k in range(30):
 for h in HOSTS:
  lim=1000-((k*7+HOSTS.index(h)*13)%97)
  try:
   r=s.get(h+'/fapi/v1/trades',params={'symbol':'BTCU','limit':lim},timeout=5)
   js=r.json();rows.append({'k':k,'host':h,'limit':lim,'status':r.status_code,'n':len(js) if isinstance(js,list) else 0,'max_id':max([int(x['id']) for x in js],default=-1) if isinstance(js,list) else -1,'max_time':max([int(x['time']) for x in js],default=-1) if isinstance(js,list) else -1,'err':None if isinstance(js,list) else str(js)[:200]})
  except Exception as e:rows.append({'k':k,'host':h,'limit':lim,'status':None,'n':0,'max_id':-1,'max_time':-1,'err':repr(e)})
 time.sleep(1)
open(f'{OUT}/rows.json','w').write(json.dumps(rows,indent=2))
lines=['# BTCU REST cache-bust probe','', '| host | successful calls | first max id | last max id | unique max ids | advanced? |','|---|---:|---:|---:|---:|---:|']
for h in HOSTS:
 x=[r for r in rows if r['host']==h and r['max_id']>=0];ids=[r['max_id'] for r in x]
 lines.append(f"| {h} | {len(x)} | {ids[0] if ids else -1} | {ids[-1] if ids else -1} | {len(set(ids))} | {bool(ids and max(ids)>min(ids))} |")
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
