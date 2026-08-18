#!/usr/bin/env python3
from __future__ import annotations
import io, math, time, zipfile, json
from pathlib import Path
from datetime import date, timedelta
import numpy as np
import pandas as pd
import requests

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
START = "2026-08-05"
END = "2026-08-16"
TRAIN_END = "2026-08-10"
BIN_MS = 250
ROUNDTRIP_FEE_BPS = 10.0   # taker 5 bp/side
EXTRA_STRESS_BPS = [0.0, 1.0, 2.0]
LATENCY_BINS = {"~250ms": 1, "500ms": 2}
HORIZONS_S = [1, 5, 15, 30, 60, 120]
MIN_TRAIN_EVENTS = 40
TOP_PER_FAMILY = 3

BASE_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
COLS = ["agg_trade_id","price","quantity","first_trade_id","last_trade_id","transact_time","is_buyer_maker"]
CACHE = Path("cache/microstructure_v2")
OUT = Path("microstructure_v2_output")
OUT.mkdir(exist_ok=True)

def days(start, end):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)

def norm_bool(s):
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().isin(["true","1","t"])

def download(symbol, day):
    p = CACHE / symbol / f"{symbol}-aggTrades-{day}.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size > 1000:
        return p
    url = BASE_URL.format(symbol=symbol, day=day)
    for k in range(4):
        try:
            print("download", url, flush=True)
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            p.write_bytes(r.content)
            return p
        except Exception:
            if k == 3:
                raise
            time.sleep(2*(k+1))

def aggregate_day(symbol, day, bin_ms=BIN_MS, chunksize=1_000_000):
    p = download(symbol, day)
    parts = []
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"Expected one CSV in {p}, got {names}")
        raw = z.open(names[0])
        first = raw.readline().decode("utf-8", errors="ignore").strip()
        raw.seek(0)
        has_header = not first.split(",")[0].strip().lstrip("-").isdigit()
        reader = pd.read_csv(raw, names=None if has_header else COLS,
                             header=0 if has_header else None,
                             chunksize=chunksize, low_memory=False)
        for chunk in reader:
            if has_header:
                ren = {}
                for c in chunk.columns:
                    lc = str(c).strip().lower()
                    if lc in {"agg_trade_id","aggtradeid","a"}: ren[c] = "agg_trade_id"
                    elif lc in {"price","p"}: ren[c] = "price"
                    elif lc in {"quantity","qty","q"}: ren[c] = "quantity"
                    elif lc in {"first_trade_id","firsttradeid","f"}: ren[c] = "first_trade_id"
                    elif lc in {"last_trade_id","lasttradeid","l"}: ren[c] = "last_trade_id"
                    elif lc in {"transact_time","timestamp","time","t"}: ren[c] = "transact_time"
                    elif lc in {"is_buyer_maker","isbuyermaker","m"}: ren[c] = "is_buyer_maker"
                chunk = chunk.rename(columns=ren)
            missing = [c for c in COLS if c not in chunk.columns]
            if missing:
                raise RuntimeError(f"Missing columns {missing}; got {list(chunk.columns)}")
            px = pd.to_numeric(chunk.price, errors="coerce")
            qty = pd.to_numeric(chunk.quantity, errors="coerce")
            ts = pd.to_numeric(chunk.transact_time, errors="coerce")
            bm = norm_bool(chunk.is_buyer_maker)
            ok = px.notna() & qty.notna() & ts.notna()
            px = px[ok].astype(float)
            qty = qty[ok].astype(float)
            ts = ts[ok].astype("int64")
            bm = bm[ok]
            if float(ts.median()) > 1e14:
                ts = ts // 1000
            sign = np.where(bm.to_numpy(), -1.0, 1.0)
            notion = px.to_numpy() * qty.to_numpy()
            b = (ts.to_numpy() // bin_ms) * bin_ms
            a = pd.DataFrame({
                "bin_ms": b,
                "price": px.to_numpy(),
                "signed_notional": notion * sign,
                "total_notional": notion,
                "signed_count": sign,
                "trade_count": 1.0,
            })
            parts.append(
                a.groupby("bin_ms", sort=False).agg(
                    last_price=("price","last"),
                    signed_notional=("signed_notional","sum"),
                    total_notional=("total_notional","sum"),
                    signed_count=("signed_count","sum"),
                    trade_count=("trade_count","sum"),
                )
            )
    d = pd.concat(parts)
    return d.groupby(level=0).agg(
        last_price=("last_price","last"),
        signed_notional=("signed_notional","sum"),
        total_notional=("total_notional","sum"),
        signed_count=("signed_count","sum"),
        trade_count=("trade_count","sum"),
    ).sort_index()

def load_symbol(symbol):
    frames = []
    for d in days(START, END):
        x = aggregate_day(symbol, d)
        print(symbol, d, len(x), flush=True)
        frames.append(x)
    d = pd.concat(frames).sort_index()
    idx = np.arange(int(d.index.min()), int(d.index.max()) + BIN_MS, BIN_MS, dtype=np.int64)
    x = d.reindex(idx)
    x["last_price"] = x.last_price.ffill()
    for c in ["signed_notional","total_notional","signed_count","trade_count"]:
        x[c] = x[c].fillna(0.0)
    return x

def make_features(x):
    x = x.copy()
    bps = int(round(1000 / BIN_MS))
    w1, w5, w15, w60, w300 = bps, 5*bps, 15*bps, 60*bps, 300*bps
    eps = 1e-12
    logp = np.log(x.last_price)
    x["rbar"] = logp.diff()
    for sec, w in [(1,w1),(5,w5),(15,w15)]:
        sn = x.signed_notional.rolling(w, min_periods=w).sum()
        tn = x.total_notional.rolling(w, min_periods=w).sum()
        sc = x.signed_count.rolling(w, min_periods=w).sum()
        tc = x.trade_count.rolling(w, min_periods=w).sum()
        x[f"fi{sec}"] = sn/(tn+eps)
        x[f"ci{sec}"] = sc/(tc+eps)
        x[f"n{sec}"] = tn
        x[f"ret{sec}_bps"] = (logp - logp.shift(w))*1e4
    base5 = x.n5.shift(w5).ewm(span=w300, min_periods=w60, adjust=False).mean()
    base1 = x.n1.shift(w1).ewm(span=w300, min_periods=w60, adjust=False).mean()
    x["shock5"] = x.n5/(base5+eps)
    x["shock1"] = x.n1/(base1+eps)
    x["vol60_bps"] = x.rbar.rolling(w60, min_periods=w60).std()*1e4
    s1 = np.sign(x.fi1)
    x["persist3"] = s1.ne(0) & s1.eq(s1.shift(w1)) & s1.eq(s1.shift(2*w1))
    x["same15"] = np.sign(x.fi1).eq(np.sign(x.fi5)) & np.sign(x.fi5).eq(np.sign(x.fi15))
    x["flow_accel"] = x.fi1.abs() - x.fi5.abs()
    x["day"] = pd.Series(pd.to_datetime(x.index, unit="ms", utc=True).date, index=x.index).astype(str)
    return x

def q(s, p):
    s = pd.Series(s).replace([np.inf,-np.inf], np.nan).dropna()
    return float(s.quantile(p)) if len(s) else np.nan

def frozen_rules(train):
    t = train.dropna(subset=["fi1","fi5","fi15","shock1","shock5","ret5_bps","n5"])
    absfi5 = t.fi5.abs()
    absfi1 = t.fi1.abs()
    rules = []
    qf_vals = [(p, q(absfi5, p)) for p in [0.95, 0.975, 0.99]]
    qv_vals = [(p, q(t.shock5, p)) for p in [0.75, 0.90]]
    qf1_99 = q(absfi1, 0.99)
    for pf, tf in qf_vals:
        for pv, tv in qv_vals:
            base = (absfi5 >= tf) & (t.shock5 >= tv)
            aligned = np.sign(t.fi5) * t.ret5_bps
            vals = aligned[base]
            lo = q(vals, 0.25)
            hi = q(vals, 0.75)
            rules += [
                dict(family="flow_extreme", pf=pf, pv=pv, tf=tf, tv=tv),
                dict(family="underreact_cont", pf=pf, pv=pv, tf=tf, tv=tv, impact=lo),
                dict(family="momentum_cont", pf=pf, pv=pv, tf=tf, tv=tv, impact=hi),
                dict(family="exhaustion_rev", pf=pf, pv=pv, tf=tf, tv=tv, impact=hi),
                dict(family="flow_accel", pf=pf, pv=pv, tf=tf, tv=tv, tf1=qf1_99),
            ]
    for pf in [0.95, 0.975, 0.99]:
        tf1 = q(absfi1, pf)
        for pv in [0.75, 0.90]:
            tv1 = q(t.shock1, pv)
            rules.append(dict(family="persistent_childflow", pf=pf, pv=pv, tf1=tf1, tv1=tv1))
    return rules

def rule_signal(x, r):
    fam = r["family"]
    if fam == "persistent_childflow":
        base = x.persist3.fillna(False) & (x.fi1.abs() >= r["tf1"]) & (x.shock1 >= r["tv1"])
        return np.where(base, np.sign(x.fi1), 0).astype(np.int8)
    base = (x.fi5.abs() >= r["tf"]) & (x.shock5 >= r["tv"])
    d = np.sign(x.fi5)
    aligned = d * x.ret5_bps
    if fam == "flow_extreme":
        ok, side = base, d
    elif fam == "underreact_cont":
        ok, side = base & (aligned <= r["impact"]), d
    elif fam == "momentum_cont":
        ok, side = base & (aligned >= r["impact"]), d
    elif fam == "exhaustion_rev":
        flip_or_fade = (np.sign(x.fi1) != d) | (x.fi1.abs() < x.fi5.abs()*0.25)
        ok, side = base & (aligned >= r["impact"]) & flip_or_fade, -d
    elif fam == "flow_accel":
        ok = base & (np.sign(x.fi1) == d) & (x.fi1.abs() >= r["tf1"]) & (x.flow_accel > 0)
        side = d
    else:
        raise ValueError(fam)
    return np.where(ok.fillna(False), side, 0).astype(np.int8)

def event_gross(price, sig, horizon_s, latency_bins):
    p = price.to_numpy(dtype=float)
    s = np.asarray(sig, dtype=np.int8)
    hb = int(round(horizon_s*1000/BIN_MS))
    valid = np.flatnonzero(s != 0)
    out = []
    next_allowed = 0
    for i in valid:
        if i < next_allowed:
            continue
        ent = i + latency_bins
        ex = ent + hb
        if ex >= len(p):
            break
        if not np.isfinite(p[ent]) or not np.isfinite(p[ex]) or p[ent] <= 0 or p[ex] <= 0:
            continue
        gross = float(s[i]) * math.log(p[ex]/p[ent])*1e4
        out.append((i, gross))
        next_allowed = ex + 1
    return out

def stats(ev, fee_bps=ROUNDTRIP_FEE_BPS, extra_bps=0.0):
    g = np.array([v for _, v in ev], dtype=float)
    if len(g) == 0:
        return dict(n=0, mean_gross_bps=np.nan, mean_net_bps=np.nan, win_net=np.nan,
                    t_gross=np.nan, ci95_low_net=np.nan, median_gross_bps=np.nan,
                    p10_gross_bps=np.nan, p90_gross_bps=np.nan)
    net = g - fee_bps - extra_bps
    sd = g.std(ddof=1) if len(g) > 1 else np.nan
    se = sd/math.sqrt(len(g)) if np.isfinite(sd) and sd > 0 else np.nan
    ci = net.mean() - 1.96*se if np.isfinite(se) else np.nan
    return dict(
        n=len(g),
        mean_gross_bps=float(g.mean()),
        mean_net_bps=float(net.mean()),
        win_net=float((net >= 0).mean()),
        t_gross=float(g.mean()/se) if np.isfinite(se) and se > 0 else np.nan,
        ci95_low_net=float(ci) if np.isfinite(ci) else np.nan,
        median_gross_bps=float(np.median(g)),
        p10_gross_bps=float(np.quantile(g, .10)),
        p90_gross_bps=float(np.quantile(g, .90)),
    )

def evaluate_candidates(symbol, x):
    train = x[x.day <= TRAIN_END].copy()
    test = x[x.day > TRAIN_END].copy()
    rules = frozen_rules(train)
    train_rows = []
    for rid, r in enumerate(rules):
        sig = rule_signal(train, r)
        for lat_name, lb in LATENCY_BINS.items():
            for h in HORIZONS_S:
                ev = event_gross(train.last_price, sig, h, lb)
                st = stats(ev)
                train_rows.append({"symbol": symbol, "rid": rid, **r,
                                   "latency": lat_name, "horizon_s": h, **st})
    tr = pd.DataFrame(train_rows)
    tr.to_csv(OUT/f"{symbol}_train_grid.csv", index=False)
    selected = []
    eligible = tr[tr.n >= MIN_TRAIN_EVENTS].copy()
    for fam, g in eligible.groupby("family"):
        g = g.sort_values(["mean_net_bps","n","t_gross"], ascending=[False,False,False])
        selected.append(g.head(TOP_PER_FAMILY))
    sel = pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()
    sel.to_csv(OUT/f"{symbol}_selected_train.csv", index=False)
    oos_rows = []
    for _, row in sel.iterrows():
        rid = int(row.rid)
        r = rules[rid]
        sig = rule_signal(test, r)
        lb = LATENCY_BINS[row.latency]
        ev = event_gross(test.last_price, sig, int(row.horizon_s), lb)
        for stress in EXTRA_STRESS_BPS:
            st = stats(ev, extra_bps=stress)
            oos_rows.append({"symbol": symbol, "family": r["family"], "rid": rid,
                             "latency": row.latency, "horizon_s": int(row.horizon_s),
                             "extra_stress_bps": stress, "train_mean_net_bps": float(row.mean_net_bps),
                             "train_n": int(row.n), **st})
    oos = pd.DataFrame(oos_rows)
    oos.to_csv(OUT/f"{symbol}_oos.csv", index=False)
    return tr, sel, oos

def main():
    all_oos = []
    for symbol in SYMBOLS:
        print("=== LOAD", symbol, "===", flush=True)
        raw = load_symbol(symbol)
        x = make_features(raw)
        _, _, oos = evaluate_candidates(symbol, x)
        all_oos.append(oos)
        del raw, x
    oos = pd.concat(all_oos, ignore_index=True)
    oos.to_csv(OUT/"all_oos.csv", index=False)
    base = oos[oos.extra_stress_bps == 0].copy()
    base["pass_net_ge_0"] = base.mean_net_bps >= 0
    oos_days = max(1, len(list(days((date.fromisoformat(TRAIN_END)+timedelta(days=1)).isoformat(), END))))
    base["events_per_oos_day"] = base.n / oos_days
    base = base.sort_values(["pass_net_ge_0","mean_net_bps","n"], ascending=[False,False,False])
    base.to_csv(OUT/"ranked_after_fee.csv", index=False)
    stress1 = oos[oos.extra_stress_bps == 1].copy()
    passes = base[base.pass_net_ge_0]
    robust = stress1[stress1.mean_net_bps >= 0].sort_values(["mean_net_bps","n"], ascending=[False,False])
    robust.to_csv(OUT/"robust_plus1bp.csv", index=False)
    lines = [
        "# Microstructure v2 — after-fee OOS screen", "",
        f"- Symbols: {', '.join(SYMBOLS)}",
        f"- Data: {START}..{END}; train through {TRAIN_END}; later days OOS",
        f"- Bucket: {BIN_MS} ms",
        f"- Tested latency proxies: {', '.join(LATENCY_BINS.keys())}",
        f"- Taker fee: 5 bp/side = {ROUNDTRIP_FEE_BPS:.1f} bp round trip",
        "- Entry/exit price proxy uses future trade-bar last price; therefore fee-only net is optimistic before spread/slippage.",
        "- 10 ms cloud latency cannot be identified honestly from 250 ms aggTrade bars; ~250 ms is the fastest defensible screen here.", "",
        f"## PASS count (OOS mean net >= 0 after {ROUNDTRIP_FEE_BPS:.0f} bp fees): {len(passes)}", "",
    ]
    if len(passes):
        lines += ["| symbol | family | latency | hold | n | events/day | gross bp | net bp | 95% low net | win net |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in passes.head(20).iterrows():
            lines.append(f"| {r.symbol} | {r.family} | {r.latency} | {int(r.horizon_s)}s | {int(r.n)} | {r.events_per_oos_day:.1f} | {r.mean_gross_bps:.3f} | {r.mean_net_bps:.3f} | {r.ci95_low_net:.3f} | {r.win_net:.3f} |")
    else:
        lines.append("No selected strategy remained non-negative after the 10 bp taker round-trip fee.")
    lines += ["", "## Top 15 regardless of pass/fail", "",
              "| symbol | family | latency | hold | n | gross bp | net bp | 95% low net |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in base.head(15).iterrows():
        lines.append(f"| {r.symbol} | {r.family} | {r.latency} | {int(r.horizon_s)}s | {int(r.n)} | {r.mean_gross_bps:.3f} | {r.mean_net_bps:.3f} | {r.ci95_low_net:.3f} |")
    lines += ["", f"## PASS after an additional 1 bp round-trip stress: {len(robust)}", ""]
    if len(robust):
        for _, r in robust.head(10).iterrows():
            lines.append(f"- {r.symbol} {r.family}, {r.latency}, {int(r.horizon_s)}s: net {r.mean_net_bps:.3f} bp, n={int(r.n)}")
    else:
        lines.append("None.")
    lines += ["", "## Interpretation rule", "",
              "A fee-only PASS is only a candidate, not deployable proof, because historical aggTrades do not reconstruct the contemporaneous spread, taker slippage, or L2 queue. A strategy that is negative even before those costs is rejected."]
    text = "\n".join(lines)
    (OUT/"summary.md").write_text(text)
    print(text, flush=True)

if __name__ == "__main__":
    main()
