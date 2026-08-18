import os,requests
OUT='u_vision_probe_output';os.makedirs(OUT,exist_ok=True)
rows=[]
for s in ['BTCU','ETHU']:
 for d in ['2026-08-15','2026-08-16','2026-08-17']:
  for kind in ['aggTrades','bookTicker']:
   u=f'https://data.binance.vision/data/futures/um/daily/{kind}/{s}/{s}-{kind}-{d}.zip'
   try:
    r=requests.get(u,timeout=20,stream=True); first=next(r.iter_content(1024),b'') if r.status_code==200 else b''
    rows.append((s,d,kind,r.status_code,r.headers.get('Content-Length'),len(first)))
   except Exception as e:rows.append((s,d,kind,'ERR',repr(e),0))
lines=['# U-margined Binance Vision probe','','| symbol | date | kind | status | content-length | first bytes |','|---|---|---|---:|---:|---:|']
for x in rows:lines.append(f'| {x[0]} | {x[1]} | {x[2]} | {x[3]} | {x[4]} | {x[5]} |')
open(f'{OUT}/summary.md','w').write('\n'.join(lines));print('\n'.join(lines))
