#!/usr/bin/env python3
from __future__ import annotations

import io, json, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

PAIR = ("FETUSDT", "GRTUSDT")
TRAIN_DAY, OOS_DAY = "2024-03-15", "2024-03-29"
GRID_MS, MAX_STALE_MS = 250, 1000
LOOKBACK_MS = [250, 500, 1000, 2000]
HOLD_MS = [250, 500, 1000, 2000]
THRESH_BPS = [0.25, 0.5, 1.0, 2.0]
FEE_SIDE_BPS = [0.0, 1.0, 2.0, 5.0]
MIN_TRAIN_TRADES = 5000

CACHE, OUT = Path("leadlag_cache"), Path("leadlag_output_v2")
CACHE.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
BASE = "https://data.binance.vision/data/futures/um/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip"
COLS = ["update_id","best_bid_price","best_bid_qty","best_ask_price","best_ask_qty","transaction_time","event_time"]


def download(s, d):
    p = CACHE / f"{s}-bookTicker-{d}.zip"
    if p.exists() and p.stat().st_size > 1000: return p
    u = BASE.format(s=s, d=d)
    print("download", u, flush=True)
    with requests.get(u, stream=True, timeout=120) as r:
        r.raise_for_status()
        with p.open("wb") as f:
            for ch in r.iter_content(4 * 1024 * 1024):
                if ch: f.write(ch)
    print(f"saved {p} bytes={p.stat().st_size:,}", flush=True)
    return p


def read_bookticker(s, d):
    p = download(s, d)
    with zipfile.ZipFile(p) as z:
        n = next(n for n in z.namelist() if n.endswith(".csv"))
        raw = z.read(n)
    x = pd.read_csv(io.BytesIO(raw), low_memory=False)
    x.columns = [str(c).strip().lower() for c in x.columns]
    if not set(COLS).issubset(x.columns):
        x = pd.read_csv(io.BytesIO(raw), header=None, names=COLS, low_memory=False)
    for c in COLS: x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["update_id","best_bid_price","best_ask_price","event_time"])
    # Public-data timestamps can be ms or us depending on archive generation vintage.
    et = x.event_time.to_numpy(dtype=np.float64)
    if np.nanmedian(et) > 1e14: x["event_time"] = (x.event_time // 1000).astype(np.int64)
    else: x["event_time"] = x.event_time.astype(np.int64)
    x = x.sort_values(["event_time","update_id"], kind="mergesort")
    x = x.drop_duplicates("event_time", keep="last")
    x = x[(x.best_bid_price > 0) & (x.best_ask_price > x.best_bid_price)]
    x["mid"] = (x.best_bid_price + x.best_ask_price) / 2
    x["bucket"] = (x.event_time // GRID_MS) * GRID_MS
    q = x.groupby("bucket", sort=True).agg(
        bid=("best_bid_price","last"), ask=("best_ask_price","last"),
        mid=("mid","last"), last_event=("event_time","last"))
    start = int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)
    idx = np.arange(start, start + 86400000, GRID_MS, dtype=np.int64)
    q = q.reindex(idx).ffill()
    # Decision time is the END of each bucket; last quote in the bucket is therefore observable.
    decision_ms = q.index.to_numpy() + GRID_MS - 1
    age = decision_ms - q.last_event.to_numpy()
    stale = (age < 0) | (age > MAX_STALE_MS)
    q.loc[stale, ["bid","ask","mid"]] = np.nan
    cov = q.mid.notna().mean()
    print(f"{s} {d}: raw={len(x):,} grid={len(q):,} coverage={cov:.3%}", flush=True)
    return q[["bid","ask","mid"]]


def load_day(d): return {s: read_bookticker(s, d) for s in PAIR}


def features(data, leader, follower, lb, h):
    lm = data[leader].mid.to_numpy(float); fm = data[follower].mid.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(lm / np.roll(lm, lb)); fr = np.log(fm / np.roll(fm, lb)); y = np.log(np.roll(fm, -h) / fm)
    lr[:lb] = np.nan; fr[:lb] = np.nan; y[-h:] = np.nan
    X = np.column_stack([np.ones(len(lr)), lr, fr])
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    return X, y, ok


def fit(train, leader, follower, lb, h):
    X, y, ok = features(train, leader, follower, lb, h); Xv, yv = X[ok], y[ok]
    b = np.linalg.lstsq(Xv, yv, rcond=None)[0]
    pred = Xv @ b; bx = Xv[:, [0,2]]; bb = np.linalg.lstsq(bx, yv, rcond=None)[0]; base = bx @ bb
    ss = np.sum((yv-yv.mean())**2)
    r2 = 1 - np.sum((yv-pred)**2)/ss if ss else np.nan
    r2b = 1 - np.sum((yv-base)**2)/ss if ss else np.nan
    return b, float(r2), float(r2b), int(ok.sum())


def predict(data, leader, follower, lb, h, b):
    X, _, ok = features(data, leader, follower, lb, h)
    p = np.full(len(X), np.nan); p[ok] = X[ok] @ b
    return p, ok


def nonoverlap(ix, h):
    out=[]; nxt=-1
    for i in ix:
        i=int(i)
        if i >= nxt: out.append(i); nxt=i+h
    return np.asarray(out, dtype=np.int64)


def simulate(data, follower, p, ok, th, h):
    q=data[follower]; bid=q.bid.to_numpy(float); ask=q.ask.to_numpy(float); mid=q.mid.to_numpy(float)
    side=np.sign(p); ex=np.arange(len(p))+h; safe=np.minimum(ex, len(p)-1)
    elig=ok & np.isfinite(p) & (np.abs(p)*1e4 >= th) & (ex < len(p))
    elig &= np.isfinite(bid) & np.isfinite(ask) & np.isfinite(mid)
    elig &= np.isfinite(bid[safe]) & np.isfinite(ask[safe]) & np.isfinite(mid[safe])
    ix=nonoverlap(np.flatnonzero(elig), h)
    if not len(ix): return np.array([]), np.array([])
    j=ix+h; s=side[ix]
    midp=s*(mid[j]-mid[ix])/mid[ix]*1e4
    ep=np.where(s>0, ask[ix], bid[ix]); xp=np.where(s>0, bid[j], ask[j])
    execp=s*(xp-ep)/ep*1e4
    return midp, execp


def maxdd(a):
    if not len(a): return np.nan
    c=np.cumsum(a); peak=np.maximum.accumulate(np.r_[0.,c])[1:]
    return float(np.max(peak-c))


def row(sample, leader, follower, lb, h, th, b, r2, r2b, fitn, midp, execp):
    z={"sample":sample,"leader":leader,"follower":follower,"lookback_ms":lb*GRID_MS,"hold_ms":h*GRID_MS,
       "threshold_bps":th,"beta_leader":b[1],"beta_follower":b[2],"r2_full_train":r2,"r2_self_train":r2b,
       "delta_r2_train":r2-r2b,"fit_n":fitn,"trades":len(execp),"trades_per_hour":len(execp)/24}
    if not len(execp): return z
    z.update(mean_mid_bps=float(midp.mean()), mean_exec_bps=float(execp.mean()), median_exec_bps=float(np.median(execp)),
             win_exec=float((execp>0).mean()), sum_exec_bps=float(execp.sum()), breakeven_fee_side_bps=float(execp.mean()/2), maxdd_exec_bps=maxdd(execp))
    for fee in FEE_SIDE_BPS:
        a=execp-2*fee; k=str(fee).replace(".","p")
        z[f"mean_net_{k}_bps"]=float(a.mean()); z[f"sum_net_{k}_bps"]=float(a.sum()); z[f"win_net_{k}"]=float((a>0).mean()); z[f"maxdd_net_{k}_bps"]=maxdd(a)
    return z


def main():
    print(f"PAIR={PAIR} TRAIN={TRAIN_DAY} OOS={OOS_DAY} GRID={GRID_MS}ms", flush=True)
    train, oos = load_day(TRAIN_DAY), load_day(OOS_DAY)
    rows=[]
    for leader, follower in (PAIR, PAIR[::-1]):
        for lbms in LOOKBACK_MS:
            for hms in HOLD_MS:
                lb, h = lbms//GRID_MS, hms//GRID_MS
                b,r2,r2b,fitn=fit(train,leader,follower,lb,h)
                for sample,data in (("train",train),("oos",oos)):
                    p,ok=predict(data,leader,follower,lb,h,b)
                    for th in THRESH_BPS:
                        m,e=simulate(data,follower,p,ok,th,h)
                        rows.append(row(sample,leader,follower,lb,h,th,b,r2,r2b,fitn,m,e))
    res=pd.DataFrame(rows); res.to_csv(OUT/"all_results.csv",index=False)
    tr=res[res["sample"]=="train"].copy()
    elig=tr[(tr.trades>=MIN_TRAIN_TRADES)&tr["sum_net_2p0_bps"].notna()].sort_values("sum_net_2p0_bps",ascending=False)
    if len(elig): best=elig.iloc[0]
    else: best=tr[tr.trades>0].sort_values("sum_exec_bps",ascending=False).iloc[0]
    mask=(res.leader==best.leader)&(res.follower==best.follower)&(res.lookback_ms==best.lookback_ms)&(res.hold_ms==best.hold_ms)&(res.threshold_bps==best.threshold_bps)
    chosen=res[mask].copy(); chosen.to_csv(OUT/"chosen_train_oos.csv",index=False)
    # Also retain the best pre-fee executable configuration for diagnostic comparison.
    gross=tr[tr.trades>=MIN_TRAIN_TRADES].sort_values("sum_exec_bps",ascending=False).head(20)
    stressed=elig.head(20)
    gross.to_csv(OUT/"top_train_exec.csv",index=False); stressed.to_csv(OUT/"top_train_2bps_side.csv",index=False)
    summary={"pair":PAIR,"train":TRAIN_DAY,"oos":OOS_DAY,"grid_ms":GRID_MS,"min_train_trades":MIN_TRAIN_TRADES,"chosen":chosen.replace({np.nan:None}).to_dict("records")}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    cols=["sample","leader","follower","lookback_ms","hold_ms","threshold_bps","delta_r2_train","trades","trades_per_hour","mean_mid_bps","mean_exec_bps","breakeven_fee_side_bps","mean_net_1p0_bps","mean_net_2p0_bps","mean_net_5p0_bps","sum_net_2p0_bps","maxdd_net_2p0_bps"]
    print("\n=== CHOSEN ON TRAIN; FROZEN OOS ==="); print(chosen[cols].to_string(index=False))
    print("\n=== BEST TRAIN PRE-FEE EXECUTABLE ==="); print(gross[cols].head(10).to_string(index=False))
    print("\nNOTE: exec PnL already crosses bid/ask both ways. Fee scenarios are extra per-side fees. No additional latency/impact slippage, so this is optimistic.")

if __name__=="__main__": main()
