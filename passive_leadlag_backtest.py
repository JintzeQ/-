#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

LEADER = "GRTUSDT"
FOLLOWER = "FETUSDT"
TRAIN_DAY = "2024-03-15"
OOS_DAY = "2024-03-29"
GRID_MS = 250
MAX_STALE_MS = 1000
LOOKBACK_MS = 500
HOLD_MS = 2000
SIGNAL_THRESHOLD_BPS = 1.0
ENTRY_TTLS_MS = [250, 500, 1000]
LATENCIES_MS = [0, 100, 250]
EXIT_TTL_MS = 500
TAKER_FEE_BPS = 5.0
MAKER_FEE_BPS = 0.0  # unknown; 0 makes this an optimistic upper bound
BASE_QUEUE_FRACTION = 0.50
MAX_ORDERS_PER_DAY = 100000

CACHE = Path("passive_leadlag_cache")
OUT = Path("passive_leadlag_output")
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

BT_BASE = "https://data.binance.vision/data/futures/um/daily/bookTicker/{s}/{s}-bookTicker-{d}.zip"
AT_BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
BT_COLS = ["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty", "transaction_time", "event_time"]
AT_COLS = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"]


def download(url: str, name: str) -> Path:
    p = CACHE / name
    if p.exists() and p.stat().st_size > 1000:
        return p
    print(f"download {url}", flush=True)
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with p.open("wb") as f:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
    print(f"saved {p} bytes={p.stat().st_size:,}", flush=True)
    return p


def read_zip_csv(p: Path) -> bytes:
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            raise RuntimeError(f"no csv in {p}")
        return z.read(names[0])


def read_bookticker(symbol: str, day: str) -> pd.DataFrame:
    url = BT_BASE.format(s=symbol, d=day)
    raw = read_zip_csv(download(url, f"{symbol}-bookTicker-{day}.zip"))
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if not set(BT_COLS).issubset(df.columns):
        df = pd.read_csv(io.BytesIO(raw), header=None, names=BT_COLS, low_memory=False)
    for c in BT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["event_time", "best_bid_price", "best_ask_price", "best_bid_qty", "best_ask_qty", "update_id"])
    df = df.sort_values(["event_time", "update_id"], kind="mergesort")
    df = df.drop_duplicates("event_time", keep="last")
    df = df[(df.best_bid_price > 0) & (df.best_ask_price > df.best_bid_price)]
    df["mid"] = (df.best_bid_price + df.best_ask_price) / 2.0
    df["bucket"] = (df.event_time.astype(np.int64) // GRID_MS) * GRID_MS
    q = df.groupby("bucket", sort=True).agg(
        bid=("best_bid_price", "last"),
        bid_qty=("best_bid_qty", "last"),
        ask=("best_ask_price", "last"),
        ask_qty=("best_ask_qty", "last"),
        mid=("mid", "last"),
        last_event=("event_time", "last"),
    )
    start = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    end = start + 24 * 60 * 60 * 1000
    idx = np.arange(start, end, GRID_MS, dtype=np.int64)
    q = q.reindex(idx).ffill()
    bucket_end = q.index.to_numpy(dtype=np.int64) + GRID_MS - 1
    q["age_ms"] = bucket_end - q.last_event.to_numpy(dtype=float)
    stale = (q.age_ms < 0) | (q.age_ms > MAX_STALE_MS)
    q.loc[stale, ["bid", "bid_qty", "ask", "ask_qty", "mid"]] = np.nan
    coverage = q.mid.notna().mean()
    print(f"BOOK {symbol} {day}: raw={len(df):,} coverage={coverage:.3%}", flush=True)
    return q[["bid", "bid_qty", "ask", "ask_qty", "mid"]]


def parse_bool_series(s: pd.Series) -> np.ndarray:
    if s.dtype == bool:
        return s.to_numpy(dtype=bool)
    x = s.astype(str).str.strip().str.lower()
    return x.isin(["true", "1", "t", "yes"]).to_numpy(dtype=bool)


def read_aggtrades(symbol: str, day: str) -> dict[str, np.ndarray]:
    url = AT_BASE.format(s=symbol, d=day)
    raw = read_zip_csv(download(url, f"{symbol}-aggTrades-{day}.zip"))
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    aliases = {
        "aggtradeid": "agg_trade_id", "aggregatetradeid": "agg_trade_id", "a": "agg_trade_id",
        "p": "price", "q": "quantity", "f": "first_trade_id", "l": "last_trade_id",
        "t": "transact_time", "time": "transact_time", "m": "is_buyer_maker",
        "is_buyer_maker": "is_buyer_maker", "buyer_was_maker": "is_buyer_maker",
    }
    df = df.rename(columns={c: aliases.get(c, c) for c in df.columns})
    if not {"price", "quantity", "transact_time", "is_buyer_maker"}.issubset(df.columns):
        df = pd.read_csv(io.BytesIO(raw), header=None, names=AT_COLS, low_memory=False)
    for c in ["price", "quantity", "transact_time"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["price", "quantity", "transact_time"])
    df = df[(df.price > 0) & (df.quantity > 0)]
    df["is_buyer_maker_bool"] = parse_bool_series(df.is_buyer_maker)
    df = df.sort_values("transact_time", kind="mergesort")
    ts = df.transact_time.to_numpy(dtype=np.int64)
    px = df.price.to_numpy(dtype=float)
    qty = df.quantity.to_numpy(dtype=float)
    bm = df.is_buyer_maker_bool.to_numpy(dtype=bool)
    gaps = np.diff(ts)
    p99 = float(np.quantile(gaps, 0.99)) if len(gaps) else np.nan
    maxgap = int(gaps.max()) if len(gaps) else -1
    start = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    end = start + 24 * 60 * 60 * 1000
    in_day = (ts >= start) & (ts < end)
    if not in_day.all():
        ts, px, qty, bm = ts[in_day], px[in_day], qty[in_day], bm[in_day]
    print(f"TRADES {symbol} {day}: n={len(ts):,} p99_gap_ms={p99:.1f} max_gap_ms={maxgap:,}", flush=True)
    return {"ts": ts, "px": px, "qty": qty, "buyer_maker": bm}


def load_day(day: str):
    books = {LEADER: read_bookticker(LEADER, day), FOLLOWER: read_bookticker(FOLLOWER, day)}
    trades = read_aggtrades(FOLLOWER, day)
    return books, trades


def make_xy(books: dict[str, pd.DataFrame]):
    lb = LOOKBACK_MS // GRID_MS
    h = HOLD_MS // GRID_MS
    lmid = books[LEADER].mid.to_numpy(dtype=float)
    fmid = books[FOLLOWER].mid.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lret = np.log(lmid / np.roll(lmid, lb))
        fret = np.log(fmid / np.roll(fmid, lb))
        y = np.log(np.roll(fmid, -h) / fmid)
    lret[:lb] = np.nan
    fret[:lb] = np.nan
    y[-h:] = np.nan
    X = np.column_stack([np.ones(len(lret)), lret, fret])
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
    return X, y, valid


def fit_signal(train_books):
    X, y, valid = make_xy(train_books)
    Xv, yv = X[valid], y[valid]
    beta, *_ = np.linalg.lstsq(Xv, yv, rcond=None)
    pred = Xv @ beta
    baseX = Xv[:, [0, 2]]
    b0, *_ = np.linalg.lstsq(baseX, yv, rcond=None)
    ss = np.sum((yv - yv.mean()) ** 2)
    r2 = 1 - np.sum((yv - pred) ** 2) / ss
    r2_base = 1 - np.sum((yv - baseX @ b0) ** 2) / ss
    print(f"signal beta={beta.tolist()} r2={r2:.6f} self_only_r2={r2_base:.6f} delta={r2-r2_base:.6f} n={len(yv):,}", flush=True)
    return beta, float(r2), float(r2_base)


def predict(books, beta):
    X, _, valid = make_xy(books)
    p = np.full(len(X), np.nan)
    p[valid] = X[valid] @ beta
    return p


def grid_index(book: pd.DataFrame, ts_ms: int) -> int | None:
    start = int(book.index[0])
    i = (int(ts_ms) - start) // GRID_MS
    if i < 0 or i >= len(book):
        return None
    return int(i)


def quote_at(book: pd.DataFrame, ts_ms: int):
    i = grid_index(book, ts_ms)
    if i is None:
        return None
    r = book.iloc[i]
    if not np.isfinite(r.bid) or not np.isfinite(r.ask):
        return None
    return i, float(r.bid), float(r.bid_qty), float(r.ask), float(r.ask_qty), float(r.mid)


def first_signal_invalidation(pred: np.ndarray, book: pd.DataFrame, start_ts: int, end_ts: int, side: int) -> int:
    i0 = grid_index(book, start_ts)
    i1 = grid_index(book, end_ts)
    if i0 is None:
        return start_ts
    if i1 is None:
        i1 = len(pred) - 1
    threshold = SIGNAL_THRESHOLD_BPS / 1e4
    for i in range(i0, min(i1 + 1, len(pred))):
        x = pred[i]
        if not np.isfinite(x) or int(np.sign(x)) != side or abs(x) < threshold:
            return int(book.index[i])
    return end_ts


def fill_check(trades, start_ts: int, end_ts: int, side: int, price: float, displayed_qty: float, model: str):
    ts, px, qty, bm = trades["ts"], trades["px"], trades["qty"], trades["buyer_maker"]
    a = int(np.searchsorted(ts, start_ts, side="left"))
    b = int(np.searchsorted(ts, end_ts, side="right"))
    if b <= a:
        return None
    t = ts[a:b]
    p = px[a:b]
    q = qty[a:b]
    m = bm[a:b]
    if side > 0:
        aggressor = m  # seller aggressor; buyer is maker
        touch = aggressor & (p <= price + 1e-12)
        through = aggressor & (p < price - 1e-12)
    else:
        aggressor = ~m  # buyer aggressor; seller is maker
        touch = aggressor & (p >= price - 1e-12)
        through = aggressor & (p > price + 1e-12)
    if model == "optimistic":
        idx = np.flatnonzero(touch)
        return int(t[idx[0]]) if len(idx) else None
    if model == "conservative":
        idx = np.flatnonzero(through)
        return int(t[idx[0]]) if len(idx) else None
    if model != "base":
        raise ValueError(model)
    idx = np.flatnonzero(touch)
    if not len(idx):
        return None
    need = max(float(displayed_qty) * BASE_QUEUE_FRACTION, 1e-12)
    cum = 0.0
    for j in idx:
        cum += float(q[j])
        if cum >= need:
            return int(t[j])
    return None


def passive_exit_or_taker(book, trades, side: int, trigger_ts: int, fill_model: str):
    q = quote_at(book, trigger_ts)
    if q is None:
        return None
    _, bid, bidq, ask, askq, _ = q
    exit_side = -side
    passive_px = ask if side > 0 else bid
    displayed = askq if side > 0 else bidq
    end_ts = trigger_ts + EXIT_TTL_MS
    fill_ts = fill_check(trades, trigger_ts, end_ts, exit_side, passive_px, displayed, fill_model)
    if fill_ts is not None:
        return {"exit_ts": fill_ts, "exit_px": passive_px, "taker": False}
    q2 = quote_at(book, end_ts)
    if q2 is None:
        return None
    _, bid2, _, ask2, _, _ = q2
    taker_px = bid2 if side > 0 else ask2
    return {"exit_ts": end_ts, "exit_px": taker_px, "taker": True}


def simulate_day(tag, books, trades, pred, latency_ms: int, entry_ttl_ms: int, fill_model: str):
    book = books[FOLLOWER]
    threshold = SIGNAL_THRESHOLD_BPS / 1e4
    signal_idx = np.flatnonzero(np.isfinite(pred) & (np.abs(pred) >= threshold))
    rows = []
    next_free_ts = -1
    attempts = 0
    for si in signal_idx:
        signal_ts = int(book.index[si] + GRID_MS - 1)
        if signal_ts < next_free_ts:
            continue
        side = int(np.sign(pred[si]))
        if side == 0:
            continue
        attempts += 1
        if attempts > MAX_ORDERS_PER_DAY:
            break
        place_ts = signal_ts + latency_ms
        q = quote_at(book, place_ts)
        if q is None:
            next_free_ts = place_ts + GRID_MS
            continue
        _, bid, bidq, ask, askq, mid0 = q
        entry_px = bid if side > 0 else ask
        displayed = bidq if side > 0 else askq
        natural_end = place_ts + entry_ttl_ms
        cancel_ts = first_signal_invalidation(pred, book, place_ts, natural_end, side)
        fill_ts = fill_check(trades, place_ts, cancel_ts, side, entry_px, displayed, fill_model)
        if fill_ts is None:
            next_free_ts = max(cancel_ts, place_ts + 1)
            rows.append({"filled": False, "signal_ts": signal_ts, "place_ts": place_ts, "side": side})
            continue
        fill_q = quote_at(book, fill_ts)
        if fill_q is None:
            next_free_ts = fill_ts + GRID_MS
            continue
        fill_mid = fill_q[-1]
        planned_trigger = fill_ts + HOLD_MS
        emergency_trigger = first_signal_invalidation(pred, book, fill_ts, planned_trigger, side)
        trigger_ts = min(planned_trigger, emergency_trigger)
        emergency = trigger_ts < planned_trigger
        ex = passive_exit_or_taker(book, trades, side, trigger_ts, fill_model)
        if ex is None:
            next_free_ts = trigger_ts + EXIT_TTL_MS
            continue
        gross_bps = side * (ex["exit_px"] - entry_px) / entry_px * 1e4
        fee_bps = MAKER_FEE_BPS + (TAKER_FEE_BPS if ex["taker"] else MAKER_FEE_BPS)
        net_bps = gross_bps - fee_bps
        end_q = quote_at(book, ex["exit_ts"])
        end_mid = end_q[-1] if end_q else np.nan
        mid_move_bps = side * (end_mid - fill_mid) / fill_mid * 1e4 if np.isfinite(end_mid) else np.nan
        rows.append({
            "filled": True,
            "signal_ts": signal_ts,
            "place_ts": place_ts,
            "fill_ts": fill_ts,
            "exit_ts": ex["exit_ts"],
            "side": side,
            "entry_px": entry_px,
            "exit_px": ex["exit_px"],
            "taker_exit": ex["taker"],
            "emergency_trigger": emergency,
            "gross_bps": gross_bps,
            "net_bps": net_bps,
            "post_fill_mid_bps": mid_move_bps,
        })
        next_free_ts = int(ex["exit_ts"] + 1)
    df = pd.DataFrame(rows)
    filled = df[df.filled == True].copy() if len(df) else pd.DataFrame()
    n_attempts = int(len(df))
    n_fill = int(len(filled))
    out = {
        "sample": tag,
        "latency_ms": latency_ms,
        "entry_ttl_ms": entry_ttl_ms,
        "fill_model": fill_model,
        "attempts": n_attempts,
        "fills": n_fill,
        "fill_rate": n_fill / n_attempts if n_attempts else np.nan,
        "fills_per_hour": n_fill / 24.0,
    }
    if n_fill:
        out.update({
            "mean_post_fill_mid_bps": float(filled.post_fill_mid_bps.mean()),
            "mean_gross_bps": float(filled.gross_bps.mean()),
            "mean_net_bps": float(filled.net_bps.mean()),
            "median_net_bps": float(filled.net_bps.median()),
            "win_net": float((filled.net_bps > 0).mean()),
            "taker_exit_rate": float(filled.taker_exit.mean()),
            "emergency_trigger_rate": float(filled.emergency_trigger.mean()),
            "sum_net_bps": float(filled.net_bps.sum()),
            "breakeven_extra_maker_fee_bps_roundtrip": float(filled.net_bps.mean()),
        })
    return out, df


def summarize_unfilled_alpha(books, pred, latency_ms: int, entry_ttl_ms: int, fill_model: str, trades):
    book = books[FOLLOWER]
    threshold = SIGNAL_THRESHOLD_BPS / 1e4
    sig = np.flatnonzero(np.isfinite(pred) & (np.abs(pred) >= threshold))
    vals_fill, vals_no = [], []
    for si in sig[::max(1, len(sig)//25000 or 1)]:
        side = int(np.sign(pred[si]))
        signal_ts = int(book.index[si] + GRID_MS - 1)
        place_ts = signal_ts + latency_ms
        q = quote_at(book, place_ts)
        if q is None:
            continue
        _, bid, bidq, ask, askq, mid0 = q
        price = bid if side > 0 else ask
        displayed = bidq if side > 0 else askq
        cancel_ts = first_signal_invalidation(pred, book, place_ts, place_ts + entry_ttl_ms, side)
        ft = fill_check(trades, place_ts, cancel_ts, side, price, displayed, fill_model)
        eval_ts = place_ts + HOLD_MS
        q2 = quote_at(book, eval_ts)
        if q2 is None:
            continue
        mid1 = q2[-1]
        val = side * (mid1 - mid0) / mid0 * 1e4
        (vals_fill if ft is not None else vals_no).append(val)
    return {
        "filled_signal_future_mid_bps": float(np.mean(vals_fill)) if vals_fill else np.nan,
        "unfilled_signal_future_mid_bps": float(np.mean(vals_no)) if vals_no else np.nan,
        "diag_filled_n": len(vals_fill),
        "diag_unfilled_n": len(vals_no),
    }


def main():
    print(f"PASSIVE {LEADER}->{FOLLOWER} train={TRAIN_DAY} oos={OOS_DAY} maker_fee={MAKER_FEE_BPS}bps taker_fee={TAKER_FEE_BPS}bps", flush=True)
    train_books, train_trades = load_day(TRAIN_DAY)
    oos_books, oos_trades = load_day(OOS_DAY)
    beta, r2, r2base = fit_signal(train_books)
    train_pred = predict(train_books, beta)
    oos_pred = predict(oos_books, beta)

    metrics = []
    detailed_saved = False
    for latency in LATENCIES_MS:
        for ttl in ENTRY_TTLS_MS:
            for model in ["optimistic", "base", "conservative"]:
                for tag, books, trades, pred in [
                    ("train", train_books, train_trades, train_pred),
                    ("oos", oos_books, oos_trades, oos_pred),
                ]:
                    m, detail = simulate_day(tag, books, trades, pred, latency, ttl, model)
                    m.update({"signal_r2_train": r2, "signal_delta_r2_train": r2-r2base})
                    metrics.append(m)
                    if tag == "oos" and latency == 100 and ttl == 500 and model == "base":
                        detail.to_csv(OUT / "oos_base_trade_log.csv", index=False)
                        detailed_saved = True
                print(f"done latency={latency} ttl={ttl} model={model}", flush=True)

    res = pd.DataFrame(metrics)
    res.to_csv(OUT / "all_metrics.csv", index=False)

    # Selection is deliberately conservative: choose on train BASE fill model, requiring throughput.
    tr = res[(res["sample"] == "train") & (res.fill_model == "base") & (res.fills >= 500)].copy()
    if len(tr):
        best = tr.sort_values(["mean_net_bps", "fills"], ascending=[False, False]).iloc[0]
    else:
        tr = res[(res["sample"] == "train") & (res.fill_model == "base")].copy()
        best = tr.sort_values("mean_net_bps", ascending=False).iloc[0]
    chosen = res[(res.latency_ms == best.latency_ms) & (res.entry_ttl_ms == best.entry_ttl_ms)].copy()
    chosen = chosen.sort_values(["sample", "fill_model"])
    chosen.to_csv(OUT / "chosen_execution_stress.csv", index=False)

    # Fixed diagnostic requested: base fill at realistic delay.
    diag = summarize_unfilled_alpha(oos_books, oos_pred, 100, 500, "base", oos_trades)
    (OUT / "adverse_selection_diag.json").write_text(json.dumps(diag, indent=2), encoding="utf-8")

    oos = res[res["sample"] == "oos"].copy()
    robust = oos[(oos.fill_model == "conservative") & (oos.mean_net_bps > 0)].sort_values("mean_net_bps", ascending=False)
    summary = {
        "leader": LEADER,
        "follower": FOLLOWER,
        "train_day": TRAIN_DAY,
        "oos_day": OOS_DAY,
        "signal": {"lookback_ms": LOOKBACK_MS, "hold_ms": HOLD_MS, "threshold_bps": SIGNAL_THRESHOLD_BPS, "beta": beta.tolist(), "delta_r2_train": r2-r2base},
        "fees_bps": {"maker_assumed": MAKER_FEE_BPS, "taker_actual": TAKER_FEE_BPS},
        "chosen_train_base": best.replace({np.nan: None}).to_dict(),
        "adverse_selection_diag_oos_base_100ms_500ms": diag,
        "n_positive_conservative_oos": int(len(robust)),
        "note": "Maker fee assumed zero; therefore results are optimistic upper bounds. Queue position is not observed; three fill models bound uncertainty.",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    cols = ["sample","latency_ms","entry_ttl_ms","fill_model","attempts","fills","fill_rate","fills_per_hour","mean_post_fill_mid_bps","mean_gross_bps","mean_net_bps","taker_exit_rate","emergency_trigger_rate","sum_net_bps"]
    print("\n=== TRAIN-CHOSEN EXECUTION, ALL FILL MODELS ===")
    print(chosen[cols].to_string(index=False))
    print("\n=== OOS TOP BY NET EV ===")
    print(oos.sort_values("mean_net_bps", ascending=False)[cols].head(15).to_string(index=False))
    print("\n=== OOS ADVERSE-SELECTION DIAGNOSTIC (100ms latency, 500ms TTL, base fill) ===")
    print(json.dumps(diag, indent=2))
    print(f"\npositive conservative OOS configs: {len(robust)}")
    print("IMPORTANT: maker fee=0 assumed; any real maker fee must be subtracted. Taker exits pay 5bps. No extra market impact is charged.")


if __name__ == "__main__":
    main()
