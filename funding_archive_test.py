import io,zipfile,requests,pandas as pd
url='https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-01.zip'
r=requests.get(url,timeout=60)
print('status',r.status_code,'bytes',len(r.content))
r.raise_for_status()
with zipfile.ZipFile(io.BytesIO(r.content)) as z:
 print('files',z.namelist())
 raw=z.read(z.namelist()[0])
 print(raw[:1000].decode('utf-8',errors='replace'))
