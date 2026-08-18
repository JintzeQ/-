# FET/OP/WIF maker v9 — corrected-stream prospective queue-turnover probe

- Capture: 180s; order $100.
- BBO from Futures public stream; aggTrades from Futures market stream.
- Displayed L1 queue + own order must be traded through; no cancellation credit.
- Fees: maker/maker 4bp; maker/taker emergency exit 7bp.
- Latency: 10ms and 240ms. Profiles fixed ex ante.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p25_spread_bp |   p75_spread_bp |   median_bid_notional |   median_ask_notional |
|:---------|--------------:|-------------:|-------------------:|----------------:|----------------:|----------------------:|----------------------:|
| FETUSDT  |           972 |          101 |              8.330 |           8.323 |           8.337 |             10843.936 |              6972.922 |
| OPUSDT   |          3071 |          246 |             12.353 |          12.353 |          12.369 |             23343.630 |             17640.293 |
| WIFUSDT  |           601 |           34 |              7.416 |           7.416 |           7.421 |              6085.450 |              4613.341 |

## Net-positive configs (n>=2)

None.

## Best regardless of sign

| symbol   |   lat_ms | profile   |   n |   mean_net_bp |   total_net_bp |   mean_gross_bp |   mm_exit_rate |   win_net |   mean_markout1_bp |   mean_markout5_bp |   ci95_low_net |   notional_roundtrip_usd |
|:---------|---------:|:----------|----:|--------------:|---------------:|----------------:|---------------:|----------:|-------------------:|-------------------:|---------------:|-------------------------:|
| OPUSDT   |       10 | loose     |   2 |       -13.180 |        -26.361 |          -6.180 |          0.000 |     0.000 |             -6.180 |             -6.180 |        -25.294 |                  400.000 |
| OPUSDT   |      240 | loose     |   2 |       -13.180 |        -26.361 |          -6.180 |          0.000 |     0.000 |             -6.180 |             -6.180 |        -25.294 |                  400.000 |

Short prospective candidate screen only; exact queue priority, hidden/RPI liquidity, market-data receive latency and cancel/replace behavior remain unmodeled.