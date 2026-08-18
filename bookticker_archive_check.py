import requests
symbols=['FETUSDT','OPUSDT','WIFUSDT','GRTUSDT','CHZUSDT','APEUSDT','WOOUSDT','BLURUSDT','IMXUSDT','ARKMUSDT','GMTUSDT','SANDUSDT','MASKUSDT','API3USDT','C98USDT','IDUSDT','FLOWUSDT','ONEUSDT']
days=['2024-03-15','2024-03-29','2024-03-31']
print('symbol,day,status,bytes')
for s in symbols:
  for d in days:
    u=f'https://data.binance.vision/data/futures/um/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip'
    try:
      r=requests.get(u,stream=True,timeout=15)
      print(f"{s},{d},{r.status_code},{r.headers.get('content-length','')}")
      r.close()
    except Exception as e:
      print(f'{s},{d},ERR,{type(e).__name__}')
