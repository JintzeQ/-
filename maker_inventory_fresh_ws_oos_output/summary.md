# Fresh WebSocket locked inventory-maker OOS

- Fresh prospective capture: 180s; GALAUSDT + IMXUSDT.
- Rule frozen: 10ms, symmetric BBO, $100/order, $100 inventory cap, min spread 4.25bp.
- BBO from Futures public stream; trades from Futures market aggTrade stream. Queue depletion counts only trades at/through our visible touch.
- Full displayed L1 queue + own order must trade through. Any touch change cancels quote and forfeits queue progress; no cancellation credit.
- Maker fee 2bp/fill; residual inventory taker-flattened at opposite touch with 5bp fee.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p75_spread_bp |
|:---------|--------------:|-------------:|-------------------:|----------------:|
| GALAUSDT |          4034 |          456 |             7.2020 |          7.2280 |
| IMXUSDT  |           854 |          140 |            10.0251 |         10.0251 |

## Results

| symbol   |   maker_fills |   quote_attempts |   maker_volume_usd |   liquidation_volume_usd |   total_volume_usd |   liquidation_share |   gross_cash_pnl |   fees_usd |   net_pnl_usd |   net_bp_per_volume |   max_inventory_usd |
|:---------|--------------:|-----------------:|-------------------:|-------------------------:|-------------------:|--------------------:|-----------------:|-----------:|--------------:|--------------------:|--------------------:|
| GALAUSDT |             2 |              105 |           200.0000 |                   0.5040 |           200.5040 |              0.0025 |           0.5040 |     0.0403 |        0.4637 |             23.1272 |            100.3957 |
| IMXUSDT  |             1 |                2 |           100.0000 |                 100.0000 |           200.0000 |              0.5000 |           0.0000 |     0.0700 |       -0.0700 |             -3.5000 |             99.9499 |

Aggregate net bp / genuine volume: **9.8303 bp**
GALA predeclared screen (>=5 maker fills and net>=0): **FAIL**.