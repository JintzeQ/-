# BTCU / ETHU maker shadow validation

This path exists because exact 10ms maker fills cannot be honestly reconstructed from the currently available Binance Vision archives, and hosted GitHub runners may receive HTTP 451 from Binance Futures REST market-data endpoints.

The recorder is **public-data only** and submits **no orders**.

## 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install requests websockets pandas numpy tabulate
```

## 2. Record on the low-latency cloud host

Default symbols are `BTCU,ETHU`, REST trade polling is 500ms, and the recorder runs until Ctrl-C:

```bash
python u_shadow_recorder.py
```

For a fixed 30-minute window:

```bash
SHADOW_DURATION_SECONDS=1800 \
SHADOW_TRADE_POLL_SECONDS=0.5 \
SHADOW_DB=u_shadow_30m.sqlite3 \
python u_shadow_recorder.py
```

The recorder stores:

- ordinary Futures `bookTicker` BBO (RPI-excluded), including exchange timestamp and local receive wall/monotonic timestamps;
- recent market trades and `isRPITrade`;
- REST poll duration/status/`X-MBX-USED-WEIGHT-1M` when present;
- periodic `/fapi/v1/time` clock-offset samples;
- metadata in SQLite WAL mode.

It intentionally seeds the current trade ID at startup so earlier history is not mixed into a prospective window.

## 3. Analyze the same window

Conservative zero-cancellation-credit analysis at 10ms order-to-exchange:

```bash
python u_shadow_analyzer.py \
  --db u_shadow_30m.sqlite3 \
  --out u_shadow_30m_analysis \
  --latency-ms 10 \
  --order-usd 100 \
  --inventory-cap-usd 100 \
  --min-spread-bp 0 \
  --cancel-credit 0
```

The analyzer scores the **same modeled fills** under two fee books:

- `u_promo_conditional`: maker 0bp / taker 4bp;
- `standard_user_fee`: maker 2bp / taker 5bp.

The conditional U fee book is not a substitute for checking the actual effective fee shown by the account before deployment.

## Fill model

A quote is only admitted if:

1. the BBO was already received locally;
2. the order's estimated arrival time is `local_receive + order_to_exchange_latency` after clock correction;
3. the observed price is still the ordinary RPI-excluded touch at that arrival time.

Queue-ahead starts at the visible L1 quantity at arrival. `isRPITrade=true` prints never deplete this queue. At-price non-RPI trades consume queue ahead; a non-RPI trade through the order price is treated as a full sweep/fill. By default no cancellation credit is granted. Residual inventory is always flattened at the final ordinary touch and charged the scenario's taker fee.

## What constitutes a candidate

Do not promote a setting from one recording window. The intended gate is:

- multiple independent windows;
- net bp per genuine filled volume >= 0 after the applicable fees;
- enough maker fills/turnover to matter;
- low forced-liquidation share;
- stable 1s/5s/15s post-fill markout;
- acceptable p50/p90/p99 market-data receive latency.

If the zero-cancellation-credit model is positive, rerun the exact locked settings on new windows before changing order size or inventory cap.
