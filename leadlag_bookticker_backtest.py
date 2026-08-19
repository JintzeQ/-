#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

PAIR = ("FETUSDT", "GRTUSDT")
TRAIN_DAY = "2024-03-15"
OOS_DAY = "2024-03-29"
GRID_MS = 250
MAX_STALE_MS = 1000
LOOKBACK_MS = [250, 500, 1000, 2000]
HOLD_MS = [250, 500, 1000, 2000]
THRESH_BPS = [0.25, 0.50, 1.00, 2.00]
FEE_SIDE_BPS = [0.0, 1.0, 2.0, 5.0]
MIN_TRAIN_TRADES = 1000

CACHE = Path("leadlag_cache")
OUT = Path("leadlag_output")
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
BASE = "https://data.binance.vision/data/futures/um/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip"
REQ_COLS = ["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty", "transaction_time", "event_time"]


def download(symbol: str, day: str) -> Path:
    p = CACHE / f"{symbol}-bookTicker-{day}.zip"
    if p.exists() and p.stat().st_size > 1000:
        return p
    url = BASE.format(s=symbol, d=day)
    print(f"download {url}", flush=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with p.open("wb") as f:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
    print(f"saved {p} bytes={p.stat().st_size:,}", flush=True)
    return p


def read_bookticker(symbol: str, day: str) -> pd.DataFrame:
    p = download(symbol, day)
    with zipfile.ZipFile(p) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        raw = z.read(name)
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if not set(REQ_COLS).issubset(df.columns):
        df = pd.read_csv(io.BytesIO(raw), header=None, names=REQ_COLS, low_memory=False)
    for c in REQ_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["event_time", "best_bid_price", "best_ask_price", "update_id"])
    # Binance public-data has had interleaved/out-of-order futures bookTicker rows;
    # normalize chronology before any lead-lag calculation.
    df = df.sort_values(["event_time", "update_id"], kind="mergesort")
    df = df.drop_duplicates(["event_time"], keep="last")
    df = df[(df.best_bid_price > 0) & (df.best_ask_price > df.best_bid_price)]
    df["mid"] = (df.best_bid_price + df.best_ask_price) / 2.0
    df["bucket"] = (df.event_time.astype(np.int64) // GRID_MS) * GRID_MS
    # Last quote update in each fixed bucket.
    q = df.groupby("bucket", sort=True).agg(
        bid=("best_bid_price", "last"),
        ask=("best_ask_price", "last"),
        mid=("mid", "last"),
        last_event=("event_time", "last"),
    )
    start = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    end = start + 24 * 60 * 60 * 1000
    idx = np.arange(start, end, GRID_MS, dtype=np.int64)
    q = q.reindex(idx).ffill()
    q["age_ms"] = q.index.to_numpy() - q.last_event.to_numpy()
    q.loc[(q.age_ms < 0) | (q.age_ms > MAX_STALE_MS), ["bid", "ask", "mid"]] = np.nan
    print(f"{symbol} {day}: raw={len(df):,} grid={len(q):,} valid_mid={q.mid.notna().sum():,}", flush=True)
    return q[["bid", "ask", "mid"]]


def load_day(day: str) -> dict[str, pd.DataFrame]:
    return {s: read_bookticker(s, day) for s in PAIR}


def make_xy(data: dict[str, pd.DataFrame], leader: str, follower: str, lb_steps: int, h_steps: int):
    lmid = data[leader].mid.to_numpy(dtype=float)
    fmid = data[follower].mid.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lret = np.log(lmid / np.roll(lmid, lb_steps))
        fret = np.log(fmid / np.roll(fmid, lb_steps))
        y = np.log(np.roll(fmid, -h_steps) / fmid)
    lret[:lb_steps] = np.nan
    fret[:lb_steps] = np.nan
    y[-h_steps:] = np.nan
    X = np.column_stack([np.ones(len(lret)), lret, fret])
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
    return X, y, valid


def fit_model(train: dict[str, pd.DataFrame], leader: str, follower: str, lb_steps: int, h_steps: int):
    X, y, valid = make_xy(train, leader, follower, lb_steps, h_steps)
    Xv, yv = X[valid], y[valid]
    beta, *_ = np.linalg.lstsq(Xv, yv, rcond=None)
    pred = Xv @ beta
    baseX = Xv[:, [0, 2]]
    base_beta, *_ = np.linalg.lstsq(baseX, yv, rcond=None)
    base_pred = baseX @ base_beta
    ss = np.sum((yv - yv.mean()) ** 2)
    r2_full = 1 - np.sum((yv - pred) ** 2) / ss if ss > 0 else np.nan
    r2_base = 1 - np.sum((yv - base_pred) ** 2) / ss if ss > 0 else np.nan
    return beta, float(r2_full), float(r2_base), int(valid.sum())


def predictions(data, leader, follower, lb_steps, h_steps, beta):
    X, _, valid = make_xy(data, leader, follower, lb_steps, h_steps)
    p = np.full(len(X), np.nan)
    p[valid] = X[valid] @ beta
    return p, valid


def choose_nonoverlap(indices: np.ndarray, h_steps: int) -> np.ndarray:
    if len(indices) == 0:
        return indices
    out = []
    next_ok = -1
    for i in indices:
        ii = int(i)
        if ii >= next_ok:
            out.append(ii)
            next_ok = ii + h_steps
    return np.asarray(out, dtype=np.int64)


def simulate(data, follower: str, pred: np.ndarray, valid: np.ndarray, threshold_bps: float, h_steps: int):
    q = data[follower]
    bid = q.bid.to_numpy(dtype=float)
    ask = q.ask.to_numpy(dtype=float)
    mid = q.mid.to_numpy(dtype=float)
    side = np.sign(pred)
    eligible = valid & np.isfinite(pred) & (np.abs(pred) * 1e4 >= threshold_bps)
    eligible &= np.isfinite(bid) & np.isfinite(ask) & np.isfinite(mid)
    end_idx = np.arange(len(pred)) + h_steps
    ok_end = end_idx < len(pred)
    safe_end = np.minimum(end_idx, len(pred) - 1)
    eligible &= ok_end & np.isfinite(bid[safe_end]) & np.isfinite(ask[safe_end]) & np.isfinite(mid[safe_end])
    idx = choose_nonoverlap(np.flatnonzero(eligible), h_steps)
    if len(idx) == 0:
        return None
    ex = idx + h_steps
    s = side[idx]
    mid_bps = s * (mid[ex] - mid[idx]) / mid[idx] * 1e4
    entry = np.where(s > 0, ask[idx], bid[idx])
    exitp = np.where(s > 0, bid[ex], ask[ex])
    exec_bps = s * (exitp - entry) / entry * 1e4
    return idx, mid_bps, exec_bps


def max_drawdown_bps(pnl: np.ndarray) -> float:
    if len(pnl) == 0:
        return np.nan
    c = np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.r_[0.0, c])
    dd = peaks[1:] - c
    return float(np.max(dd)) if len(dd) else 0.0


def metric_row(tag, leader, follower, lb, hold, th, beta, r2f, r2b, fit_n, sim):
    row = {
        "sample": tag,
        "leader": leader,
        "follower": follower,
        "lookback_ms": lb,
        "hold_ms": hold,
        "threshold_bps": th,
        "beta_intercept": beta[0],
        "beta_leader": beta[1],
        "beta_follower": beta[2],
        "r2_full_train": r2f,
        "r2_self_only_train": r2b,
        "delta_r2_train": r2f - r2b,
        "fit_n": fit_n,
    }
    if sim is None:
        row.update({"trades": 0})
        return row
    _, mid_bps, exec_bps = sim
    row.update({
        "trades": len(exec_bps),
        "trades_per_hour": len(exec_bps) / 24.0,
        "mean_mid_bps": float(np.mean(mid_bps)),
        "mean_exec_bps": float(np.mean(exec_bps)),
        "median_exec_bps": float(np.median(exec_bps)),
        "win_exec": float(np.mean(exec_bps > 0)),
        "sum_exec_bps": float(np.sum(exec_bps)),
        "maxdd_exec_bps": max_drawdown_bps(exec_bps),
        "breakeven_fee_side_bps": float(np.mean(exec_bps) / 2.0),
    })
    for fee in FEE_SIDE_BPS:
        net = exec_bps - 2 * fee
        k = str(fee).replace(".", "p")
        row[f"mean_net_{k}_bps"] = float(np.mean(net))
        row[f"sum_net_{k}_bps"] = float(np.sum(net))
        row[f"win_net_{k}"] = float(np.mean(net > 0))
        row[f"maxdd_net_{k}_bps"] = max_drawdown_bps(net)
    return row


def main():
    print(f"PAIR={PAIR} TRAIN={TRAIN_DAY} OOS={OOS_DAY} GRID_MS={GRID_MS}", flush=True)
    train = load_day(TRAIN_DAY)
    oos = load_day(OOS_DAY)
    rows = []
    model_cache = {}
    for leader, follower in (PAIR, PAIR[::-1]):
        for lb in LOOKBACK_MS:
            for hold in HOLD_MS:
                lbs, hs = lb // GRID_MS, hold // GRID_MS
                beta, r2f, r2b, fit_n = fit_model(train, leader, follower, lbs, hs)
                model_cache[(leader, follower, lb, hold)] = (beta, r2f, r2b, fit_n)
                for tag, data in (("train", train), ("oos", oos)):
                    pred, valid = predictions(data, leader, follower, lbs, hs, beta)
                    for th in THRESH_BPS:
                        sim = simulate(data, follower, pred, valid, th, hs)
                        rows.append(metric_row(tag, leader, follower, lb, hold, th, beta, r2f, r2b, fit_n, sim))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "all_results.csv", index=False)

    tr = res[res.sample == "train"].copy()
    # 2 bps/side is the ranking stress case; spread crossing is already embedded in exec PnL.
    score_col = "sum_net_2p0_bps"
    eligible = tr[(tr.trades >= MIN_TRAIN_TRADES) & tr[score_col].notna()].sort_values(score_col, ascending=False)
    if len(eligible):
        best = eligible.iloc[0]
    else:
        best = tr.sort_values("sum_exec_bps", ascending=False).iloc[0]
    mask = (
        (res.leader == best.leader) & (res.follower == best.follower) &
        (res.lookback_ms == best.lookback_ms) & (res.hold_ms == best.hold_ms) &
        (res.threshold_bps == best.threshold_bps)
    )
    chosen = res[mask].sort_values("sample", ascending=False)
    chosen.to_csv(OUT / "chosen_train_oos.csv", index=False)

    top = eligible.head(20) if len(eligible) else tr.sort_values("sum_exec_bps", ascending=False).head(20)
    top.to_csv(OUT / "top_train.csv", index=False)

    summary = {
        "pair": list(PAIR),
        "train_day": TRAIN_DAY,
        "oos_day": OOS_DAY,
        "grid_ms": GRID_MS,
        "selection_rule": "maximize train sum_net_2bps_per_side among >=1000 non-overlapping trades",
        "chosen": chosen.replace({np.nan: None}).to_dict(orient="records"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    cols = [
        "sample","leader","follower","lookback_ms","hold_ms","threshold_bps","delta_r2_train","trades","trades_per_hour",
        "mean_mid_bps","mean_exec_bps","breakeven_fee_side_bps","mean_net_1p0_bps","mean_net_2p0_bps","mean_net_5p0_bps",
        "sum_net_2p0_bps","maxdd_net_2p0_bps"
    ]
    print("\n=== CHOSEN ON TRAIN, THEN FROZEN OOS ===")
    print(chosen[cols].to_string(index=False))
    print("\n=== TOP TRAIN CONFIGS ===")
    print(top[cols].head(10).to_string(index=False))
    print("\nInterpretation: executable PnL crosses the follower spread on entry and exit. Fee scenarios are additional per-side fees; no extra latency/impact slippage is charged, so results are optimistic.")


if __name__ == "__main__":
    main()
