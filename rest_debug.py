import json, time, requests
out=[]
for s in ['FETUSDT','OPUSDT','WIFUSDT']:
    try:
        r=requests.get('https://fapi.binance.com/fapi/v1/trades',params={'symbol':s,'limit':10},timeout=10)
        out.append({'symbol':s,'status':r.status_code,'body':r.json() if r.ok else r.text[:500]})
    except Exception as e:
        out.append({'symbol':s,'error':repr(e)})
print(json.dumps(out,indent=2))
