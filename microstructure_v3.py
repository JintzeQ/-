#!/usr/bin/env python3
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
import pandas as pd
from microstructure_v2 import (
    SYMBOLS, START, END, TRAIN_END, BIN_MS, ROUNDTRIP_FEE_BPS,
    load_symbol, make_features
)

OUT = Path("microstructure_v3_output")
OUT.mkdir(exist_ok=True)
PAIRS = [("BTCUSDT","ETHUSDT"), ("BTCUSDT","SOLUSDT"), ("ETHUSDT","SOLUSDT")]
LATENCIES = {"~250ms":1, "500ms":2}
HORIZONS = [5,15,30,60,120,300]
MIN_TRAIN = 25
TOP_PER_FAMILY = 4

def q(s,p):
    s = pd.Series(s).replace([np.inf,-np.inf],np.nan).dropna()
    return float(s.quantile(p)) if len(s) else np.nan

def load_compact():
    out={}
    for s in SYMBOLS:
        print("load",s,flush=True)
        x=make_features(load_symbol(s))
        c=x[["last_price","fi5","shock5","ret5_bps","vol60_bps","day"]].copy()
        for col in ["last_price","fi5","shock5","ret5_bps","vol60_bps"]:
            c[col]=c[col].astype("float32")
        out[s]=c
    return out

def align(a,b):
    idx=a.index.intersection(b.index)
    aa=a.loc[idx].copy(); bb=b.loc[idx].copy()
    z=pd.DataFrame(index=idx)
    z["leader_px"]=aa.last_price
    z["target_px"]=bb.last_price
    z["leader_fi5"]=aa.fi5
    z["leader_shock5"]=aa.shock5
    z["leader_ret5"]=aa.ret5_bps
    z["target_ret5"]=bb.ret5_bps
    z["target_vol60"]=bb.vol60_bps
    z["day"]=aa.day
    return z

def beta_train(t):
    a=t.leader_ret5.to_numpy(float); b=t.target_ret5.to_numpy(float)
    ok=np.isfinite(a)&np.isfinite(b)
    a=a[ok]; b=b[ok]
    if len(a)<100 or np.var(a)==0: return 1.0
    return float(np.cov(a,b,ddof=1)[0,1]/np.var(a,ddof=1))

def frozen_rules(train):
    t=train.dropna()
    beta=beta_train(t)
    rules=[]
    for pr in [0.95,0.975,0.99]:
        tr=q(t.leader_ret5.abs(),pr)
        for pf in [0.90,0.95]:
            tf=q(t.leader_fi5.abs(),pf)
            for ps in [0.75,0.90]:
                ts=q(t.leader_shock5,ps)
                d=np.sign(t.leader_ret5)
                aligned=(np.sign(t.leader_fi5)==d)
                base=(t.leader_ret5.abs()>=tr)&(t.leader_fi5.abs()>=tf)&(t.leader_shock5>=ts)&aligned&(d!=0)
                gap=d*(beta*t.leader_ret5-t.target_ret5)
                over=d*(t.target_ret5-beta*t.leader_ret5)
                gap75=q(gap[base],.75); gap90=q(gap[base],.90)
                over75=q(over[base],.75)
                rules += [
                    dict(family="leader_momentum",pr=pr,pf=pf,ps=ps,tr=tr,tf=tf,ts=ts,beta=beta),
                    dict(family="catchup75",pr=pr,pf=pf,ps=ps,tr=tr,tf=tf,ts=ts,beta=beta,gap=gap75),
                    dict(family="catchup90",pr=pr,pf=pf,ps=ps,tr=tr,tf=tf,ts=ts,beta=beta,gap=gap90),
                    dict(family="overshoot_reversal",pr=pr,pf=pf,ps=ps,tr=tr,tf=tf,ts=ts,beta=beta,over=over75),
                ]
    return rules

def signal(x,r):
    d=np.sign(x.leader_ret5)
    base=(x.leader_ret5.abs()>=r["tr"])&(x.leader_fi5.abs()>=r["tf"])&(x.leader_shock5>=r["ts"])&(np.sign(x.leader_fi5)==d)&(d!=0)
    fam=r["family"]
    if fam=="leader_momentum":
        ok=base; side=d
    elif fam.startswith("catchup"):
        gap=d*(r["beta"]*x.leader_ret5-x.target_ret5)
        ok=base&(gap>=r["gap"]); side=d
    elif fam=="overshoot_reversal":
        over=d*(x.target_ret5-r["beta"]*x.leader_ret5)
        ok=base&(over>=r["over"]); side=-d
    else: raise ValueError(fam)
    return np.where(ok.fillna(False),side,0).astype(np.int8)

def event_gross(px,sig,h,lat):
    p=px.to_numpy(float); s=np.asarray(sig,np.int8)
    hb=int(round(h*1000/BIN_MS))
    out=[]; next_allowed=0
    for i in np.flatnonzero(s!=0):
        if i<next_allowed: continue
        ent=i+lat; ex=ent+hb
        if ex>=len(p): break
        if p[ent]>0 and p[ex]>0 and np.isfinite(p[ent]) and np.isfinite(p[ex]):
            out.append(float(s[i])*math.log(p[ex]/p[ent])*1e4)
            next_allowed=ex+1
    return np.asarray(out,float)

def stat(g):
    if len(g)==0: return dict(n=0,mean_gross_bps=np.nan,mean_net_bps=np.nan,ci95_low_net=np.nan,win_net=np.nan)
    net=g-ROUNDTRIP_FEE_BPS
    sd=g.std(ddof=1) if len(g)>1 else np.nan
    se=sd/math.sqrt(len(g)) if np.isfinite(sd) and sd>0 else np.nan
    return dict(n=len(g),mean_gross_bps=float(g.mean()),mean_net_bps=float(net.mean()),
                ci95_low_net=float(net.mean()-1.96*se) if np.isfinite(se) else np.nan,
                win_net=float((net>=0).mean()))

def main():
    data=load_compact()
    all_oos=[]
    for lead,target in PAIRS:
        print("pair",lead,target,flush=True)
        x=align(data[lead],data[target])
        train=x[x.day<=TRAIN_END]; test=x[x.day>TRAIN_END]
        rules=frozen_rules(train)
        rows=[]
        for rid,r in enumerate(rules):
            sig=signal(train,r)
            for lname,lb in LATENCIES.items():
                for h in HORIZONS:
                    g=event_gross(train.target_px,sig,h,lb)
                    rows.append({"leader":lead,"target":target,"rid":rid,"family":r["family"],
                                 "latency":lname,"horizon_s":h,**stat(g)})
        tr=pd.DataFrame(rows)
        tr.to_csv(OUT/f"{lead}_{target}_train.csv",index=False)
        selected=[]
        for fam,g in tr[tr.n>=MIN_TRAIN].groupby("family"):
            selected.append(g.sort_values(["mean_net_bps","n"],ascending=[False,False]).head(TOP_PER_FAMILY))
        sel=pd.concat(selected,ignore_index=True) if selected else pd.DataFrame()
        sel.to_csv(OUT/f"{lead}_{target}_selected.csv",index=False)
        o=[]
        for _,row in sel.iterrows():
            r=rules[int(row.rid)]
            g=event_gross(test.target_px,signal(test,r),int(row.horizon_s),LATENCIES[row.latency])
            o.append({"leader":lead,"target":target,"family":r["family"],"rid":int(row.rid),
                      "latency":row.latency,"horizon_s":int(row.horizon_s),
                      "train_net_bps":float(row.mean_net_bps),"train_n":int(row.n),**stat(g)})
        odf=pd.DataFrame(o)
        odf.to_csv(OUT/f"{lead}_{target}_oos.csv",index=False)
        all_oos.append(odf)
    res=pd.concat(all_oos,ignore_index=True)
    res["pass_after_taker_fees"]=res.mean_net_bps>=0
    res["pass_gross_ge_maker_rt"]=res.mean_gross_bps>=4.0
    res=res.sort_values(["pass_after_taker_fees","mean_net_bps","n"],ascending=[False,False,False])
    res.to_csv(OUT/"ranked.csv",index=False)
    p=res[res.pass_after_taker_fees]
    maker_screen=res[res.pass_gross_ge_maker_rt]
    lines=[
        "# Cross-asset lead-lag v3 — OOS after-fee screen","",
        f"- Data {START}..{END}, train through {TRAIN_END}",
        f"- Pairs: {', '.join(a+'→'+b for a,b in PAIRS)}",
        "- Entry target is the lagging futures contract; 250ms/500ms latency proxies.",
        f"- Taker/taker fee hurdle = {ROUNDTRIP_FEE_BPS:.0f} bp round trip.","",
        f"## PASS after 10 bp fees: {len(p)}",""
    ]
    if len(p):
        lines += ["| pair | family | latency | hold | n | gross bp | net bp | 95% low net |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"]
        for _,r in p.head(20).iterrows():
            lines.append(f"| {r.leader}→{r.target} | {r.family} | {r.latency} | {int(r.horizon_s)}s | {int(r.n)} | {r.mean_gross_bps:.3f} | {r.mean_net_bps:.3f} | {r.ci95_low_net:.3f} |")
    else:
        lines.append("None.")
    lines += ["","## Top 15 OOS regardless of pass","",
              "| pair | family | latency | hold | n | gross bp | net bp | 95% low net |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for _,r in res.head(15).iterrows():
        lines.append(f"| {r.leader}→{r.target} | {r.family} | {r.latency} | {int(r.horizon_s)}s | {int(r.n)} | {r.mean_gross_bps:.3f} | {r.mean_net_bps:.3f} | {r.ci95_low_net:.3f} |")
    lines += ["",f"## Gross >= 4 bp maker/maker fee hurdle (fill NOT modeled): {len(maker_screen)}",""]
    if len(maker_screen):
        for _,r in maker_screen.head(10).iterrows():
            lines.append(f"- {r.leader}→{r.target} {r.family}, {r.latency}, {int(r.horizon_s)}s: gross {r.mean_gross_bps:.3f} bp, n={int(r.n)}")
    else:
        lines.append("None.")
    lines += ["","Maker/maker comparison is only a prerequisite screen: historical L2 queue/fill is not modeled, so it is not a maker backtest."]
    txt="\n".join(lines)
    (OUT/"summary.md").write_text(txt)
    print(txt,flush=True)

if __name__=="__main__":
    main()
