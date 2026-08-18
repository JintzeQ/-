import io, json, zipfile, requests
out=[]
for kind in ['bookTicker','aggTrades']:
    u=f'https://data.binance.vision/data/futures/um/daily/{kind}/WOOUSDT/WOOUSDT-{kind}-2024-03-29.zip'
    try:
        r=requests.get(u,timeout=60)
        item={'kind':kind,'status':r.status_code,'bytes':len(r.content)}
        if r.ok:
            z=zipfile.ZipFile(io.BytesIO(r.content)); name=z.namelist()[0]; raw=z.read(name)
            item['name']=name; item['first_lines']=raw[:1000].decode('utf-8','replace').splitlines()[:4]
        else:item['body']=r.text[:300]
        out.append(item)
    except Exception as e:out.append({'kind':kind,'error':repr(e)})
print(json.dumps(out,indent=2))
