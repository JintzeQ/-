#!/usr/bin/env python3
from __future__ import annotations

import io
import itertools
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BENCH = ["BTCUSDT", "ETHUSDT"]
SYMBOLS = [
    "SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","LINKUSDT",
    "AVAXUSDT","SUIUSDT","APTUSDT","OPUSDT","ARBUSDT","WIFUSDT",
    "FETUSDT","GRTUSDT","NEARUSDT","INJUSDT","SEIUSDT","LTCUSDT",
    "BCHUSDT","PEPEUSDT",
]
MONTHS = [str(p) for p in pd.period_range("2025-01", "2026-07", freq="M")]
BASE = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{m}.zip"
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]

SHOCK_BPS = [50, 75, 100]
VSHOCK = [2.0, 3.0]
FLOW_ABS = [0.50, 0.70]
EXHAUST_RATIO = [0.25, 0.50]
HORIZONS = [1, 2, 3, 5, 10, 15]
COOLDOWN_MIN = 5
BETA_WINDOW = 1440
TAKER_RT_BPS = 10.0

CACHE = Path("shock_reversal_cache")
OUT = Path("shock_reversal_output")
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

SPLITS = {
    "train": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    "validation": (pd.Timestamp("2025-07-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
    "oos": (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")),
}
SPLIT_NS = {k: (int(a.value), int(b.value)) for k, (a, b) in SPLITS.items()}


def download(symbol: str, month: str) -> Path | None:
    d = CACHE / symbol
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol}-1m-{month}.zip"
    if p.exists() and p.stat().st_size > 1000:
        return p
    url = BASE.format(s=symbol, m=month)
    for k in range(3):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code == 404:
                print(f"MISS {symbol} {month}", flush=True)
                return None
            r.raise_for_status()
            p.write_bytes(r.content)
            return p
        except Exception as e:
            if k == 2:
                print(f"FAIL {symbol} {month}: {type(e).__name__}", flush=True)
                return None
            time.sleep(1.5 * (k + 1))
    return None


def read_zip(p: Path) -> pd.DataFrame:
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            return pd.DataFrame()
        raw = z.read(names[0])
    df = pd.read_csv(io.BytesIO(raw), header=None, names=COLS, low_memory=False)
    ot = pd.to_numeric(df.open_time, errors="coerce")
    df = df[ot.notna()].copy()
    for c in ["open_time","close","quote_volume","taker_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    med = float(df.open_time.median())
    unit = "us" if med > 1e14 else "ms"
    df["timestamp"] = pd.to_datetime(df.open_time, unit=unit, utc=True)
    return df[["timestamp","close","quote_volume","taker_quote"]].dropna()


def load_symbol(symbol: str) -> pd.DataFrame:
    frames = []
    for i, m in enumerate(MONTHS, 1):
        p = download(symbol, m)
        if p is not None:
            x = read_zip(p)
            if len(x):
                frames.append(x)
        if i % 6 == 0 or i == len(MONTHS):
            print(f"[{symbol}] months {i}/{len(MONTHS)}", flush=True)
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    print(f"[{symbol}] rows={len(d):,} {d.timestamp.iloc[0]} -> {d.timestamp.iloc[-1]}", flush=True)
    return d


def build_benchmark() -> pd.DataFrame:
    parts = {}
    for s in BENCH:
        d = load_symbol(s)[["timestamp","close"]].copy()
        d[f"r_{s}"] = np.log(d.close).diff()
        parts[s] = d[["timestamp", f"r_{s}"]]
    b = parts[BENCH[0]].merge(parts[BENCH[1]], on="timestamp", how="inner")
    b["mret"] = 0.5 * (b[f"r_{BENCH[0]}"] + b[f"r_{BENCH[1]}"])
    return b[["timestamp","mret"]]


def make_features(d: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    x = d.merge(bench, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    x["aret"] = np.log(x.close).diff()
    cov = x.aret.rolling(BETA_WINDOW, min_periods=360).cov(x.mret).shift(1)
    var = x.mret.rolling(BETA_WINDOW, min_periods=360).var().shift(1)
    x["beta"] = (cov / var.replace(0, np.nan)).clip(-3, 5)
    x["resid1"] = x.aret - x.beta * x.mret
    x["resid3"] = x.resid1.rolling(3, min_periods=3).sum()

    q = x.quote_volume.clip(lower=0)
    signed = 2.0 * x.taker_quote.clip(lower=0) - q
    prev_q = q.shift(1).rolling(2, min_periods=2).sum()
    prev_s = signed.shift(1).rolling(2, min_periods=2).sum()
    x["flow_prev"] = prev_s / prev_q.replace(0, np.nan)
    x["flow_now"] = signed / q.replace(0, np.nan)

    q3 = q.rolling(3, min_periods=3).sum()
    qmed = q3.shift(1).rolling(60, min_periods=30).median()
    x["vshock"] = q3 / qmed.replace(0, np.nan)
    x["shock_dir"] = np.sign(x.resid3)
    x["side"] = -x.shock_dir
    x["nonextend"] = x.shock_dir * x.resid1 <= 0.0005
    for h in HORIZONS:
        x[f"fwd_{h}"] = x.side * (x.close.shift(-h) / x.close - 1.0) * 1e4
    return x


def cooldown_indices(mask: np.ndarray) -> np.ndarray:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx
    out = []
    next_ok = -1
    for i in idx:
        ii = int(i)
        if ii >= next_ok:
            out.append(ii)
            next_ok = ii + COOLDOWN_MIN + 1
    return np.asarray(out, dtype=np.int64)


def calc_stats(vals) -> dict:
    a = np.asarray(vals, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return {"n":0,"mean_bps":np.nan,"median_bps":np.nan,"win":np.nan,"q10_bps":np.nan,"q90_bps":np.nan,"net10_mean_bps":np.nan}
    return {
        "n": int(len(a)),
        "mean_bps": float(a.mean()),
        "median_bps": float(np.median(a)),
        "win": float((a > 0).mean()),
        "q10_bps": float(np.quantile(a, 0.10)),
        "q90_bps": float(np.quantile(a, 0.90)),
        "net10_mean_bps": float(a.mean() - TAKER_RT_BPS),
    }


def main():
    print(f"Strategy #2A symbols={len(SYMBOLS)} months={MONTHS[0]}..{MONTHS[-1]} round_trip_taker={TAKER_RT_BPS}bps", flush=True)
    bench = build_benchmark()
    configs = list(itertools.product(SHOCK_BPS, VSHOCK, FLOW_ABS, EXHAUST_RATIO))
    global_acc: dict[tuple, list] = {}
    symbol_rows = []

    for si, sym in enumerate(SYMBOLS, 1):
        print(f"\n=== {sym} {si}/{len(SYMBOLS)} ===", flush=True)
        raw = load_symbol(sym)
        if len(raw) < 10000:
            continue
        x = make_features(raw, bench)
        ts_ns = x.timestamp.astype("int64").to_numpy(dtype=np.int64)
        resid = x.resid3.to_numpy(dtype=float)
        vshock = x.vshock.to_numpy(dtype=float)
        fp = x.flow_prev.to_numpy(dtype=float)
        fn = x.flow_now.to_numpy(dtype=float)
        sd = x.shock_dir.to_numpy(dtype=float)
        nonextend = x.nonextend.to_numpy(dtype=bool)
        finite = np.isfinite(resid) & np.isfinite(vshock) & np.isfinite(fp) & np.isfinite(fn) & (sd != 0) & (np.abs(resid) <= 0.05)

        for shock_bp, vs, flow_abs, er in configs:
            mask = finite.copy()
            mask &= np.abs(resid) * 1e4 >= shock_bp
            mask &= vshock >= vs
            mask &= sd * fp >= flow_abs
            mask &= sd * fn <= er * np.abs(fp)
            mask &= nonextend
            idx = cooldown_indices(mask)
            if len(idx) == 0:
                continue

            for h in HORIZONS:
                vals = x[f"fwd_{h}"].to_numpy(dtype=float)[idx]
                valid = np.isfinite(vals)
                if not valid.any():
                    continue
                ii = idx[valid]
                vv = vals[valid]
                t = ts_ns[ii]
                for split, (a_ns, b_ns) in SPLIT_NS.items():
                    sel = (t >= a_ns) & (t < b_ns)
                    if not sel.any():
                        continue
                    sv = vv[sel]
                    key = (shock_bp, vs, flow_abs, er, h, split)
                    global_acc.setdefault(key, []).extend(sv.tolist())
                    sr = {"symbol":sym,"shock_bps":shock_bp,"vshock":vs,"flow_abs":flow_abs,"exhaust_ratio":er,"horizon_min":h,"split":split}
                    sr.update(calc_stats(sv))
                    symbol_rows.append(sr)

        del raw, x

    rows = []
    for (shock_bp, vs, flow_abs, er, h, split), vals in global_acc.items():
        row = {"shock_bps":shock_bp,"vshock":vs,"flow_abs":flow_abs,"exhaust_ratio":er,"horizon_min":h,"split":split}
        row.update(calc_stats(vals))
        row["events_per_day"] = row["n"] / max((SPLITS[split][1] - SPLITS[split][0]).days, 1)
        rows.append(row)
    res = pd.DataFrame(rows)
    sres = pd.DataFrame(symbol_rows)
    res.to_csv(OUT / "all_results.csv", index=False)
    sres.to_csv(OUT / "all_symbol_results.csv", index=False)
    if len(res) == 0:
        raise RuntimeError("No events generated")

    wide = res.pivot_table(index=["shock_bps","vshock","flow_abs","exhaust_ratio","horizon_min"], columns="split", values=["n","mean_bps","median_bps","win","net10_mean_bps","events_per_day"]).reset_index()
    wide.columns = ["_".join([str(z) for z in c if str(z)]) if isinstance(c, tuple) else str(c) for c in wide.columns]
    for c in ["n_train","n_validation","mean_bps_train","mean_bps_validation"]:
        if c not in wide:
            wide[c] = np.nan

    eligible = wide[(wide.n_train >= 200) & (wide.n_validation >= 100) & (wide.mean_bps_train > 0) & (wide.mean_bps_validation > 0)].copy()
    if len(eligible):
        eligible["robust_score"] = np.minimum(eligible.mean_bps_train, eligible.mean_bps_validation)
        if "median_bps_train" in eligible and "median_bps_validation" in eligible:
            eligible["robust_score"] += 0.25 * np.minimum(eligible.median_bps_train, eligible.median_bps_validation)
        eligible = eligible.sort_values(["robust_score","n_validation"], ascending=[False,False])
    else:
        eligible = wide[(wide.n_train >= 100) & (wide.n_validation >= 50)].copy()
        if len(eligible) == 0:
            eligible = wide.copy()
        eligible["robust_score"] = np.minimum(eligible.mean_bps_train.fillna(-1e9), eligible.mean_bps_validation.fillna(-1e9))
        eligible = eligible.sort_values("robust_score", ascending=False)
    chosen = eligible.iloc[0]
    eligible.head(50).to_csv(OUT / "ranked_train_validation.csv", index=False)

    cfg = {"shock_bps":int(chosen.shock_bps),"vshock":float(chosen.vshock),"flow_abs":float(chosen.flow_abs),"exhaust_ratio":float(chosen.exhaust_ratio),"horizon_min":int(chosen.horizon_min)}
    chosen_rows = res[
        (res.shock_bps == cfg["shock_bps"]) & (res.vshock == cfg["vshock"]) &
        (res.flow_abs == cfg["flow_abs"]) & (res.exhaust_ratio == cfg["exhaust_ratio"]) &
        (res.horizon_min == cfg["horizon_min"])
    ].copy()
    chosen_rows.to_csv(OUT / "chosen_by_split.csv", index=False)

    chosen_sym = sres[
        (sres.shock_bps == cfg["shock_bps"]) & (sres.vshock == cfg["vshock"]) &
        (sres.flow_abs == cfg["flow_abs"]) & (sres.exhaust_ratio == cfg["exhaust_ratio"]) &
        (sres.horizon_min == cfg["horizon_min"]) & (sres.split == "oos")
    ].copy().sort_values("n", ascending=False)
    chosen_sym.to_csv(OUT / "chosen_oos_by_symbol.csv", index=False)
    max_count_share = float(chosen_sym.n.max() / chosen_sym.n.sum()) if len(chosen_sym) and chosen_sym.n.sum() else np.nan

    oos = chosen_rows[chosen_rows.split == "oos"]
    if len(oos):
        r = oos.iloc[0]
        passed = bool(r.n >= 100 and r.mean_bps >= 20 and r.median_bps > 0 and r.net10_mean_bps >= 5 and (not np.isfinite(max_count_share) or max_count_share <= 0.30))
    else:
        passed = False

    summary = {
        "strategy":"residual shock + flow exhaustion reversal",
        "symbols":SYMBOLS,
        "train":"2025-H1","validation":"2025-H2","oos":"2026-01 through 2026-07",
        "chosen":cfg,
        "chosen_by_split":chosen_rows.replace({np.nan:None}).to_dict(orient="records"),
        "oos_max_symbol_event_share":None if not np.isfinite(max_count_share) else max_count_share,
        "stage2_tick_execution_candidate":passed,
        "pass_rule":"OOS n>=100, gross mean>=20bps, median>0, mean-10bps taker RT>=5bps, max symbol event share<=30%",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    show = ["split","n","events_per_day","mean_bps","median_bps","win","q10_bps","q90_bps","net10_mean_bps"]
    print("\n=== CHOSEN ON TRAIN+VALIDATION; OOS UNTOUCHED ===")
    print(cfg)
    print(chosen_rows[show].to_string(index=False))
    print(f"\nOOS max symbol event share={max_count_share:.3f}" if np.isfinite(max_count_share) else "\nOOS max symbol event share=NA")
    print(f"STAGE2_TICK_EXECUTION_CANDIDATE={passed}")
    if len(chosen_sym):
        print("\n=== CHOSEN OOS BY SYMBOL (top event counts) ===")
        print(chosen_sym[["symbol","n","mean_bps","median_bps","win","net10_mean_bps"]].head(20).to_string(index=False))
    print("\nNOTE: forward close-to-close reversal returns are NOT executable PnL. net10_mean only subtracts the user's 10bps taker round trip; spread, latency and impact are not charged yet.")


if __name__ == "__main__":
    main()
