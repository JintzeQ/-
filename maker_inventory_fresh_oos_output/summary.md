# Fresh locked inventory-maker OOS

- Fresh prospective capture: 300s; symbols GALAUSDT + IMXUSDT.
- Configuration frozen before capture: 10ms order-to-exchange, symmetric BBO, $100/order, $100 inventory cap, min spread 4.25bp.
- Trades are REST market trades; RPI trades are excluded.
- Full displayed L1 queue + own size must trade through. Any touch change cancels quote and forfeits queue progress; no cancellation credit.
- Maker fee 2bp/fill; all residual inventory taker-flattened at opposite touch with 5bp fee.

## Diagnostics

| symbol   |   book_events |   non_rpi_market_trades |   median_spread_bp |   p75_spread_bp |
|:---------|--------------:|------------------------:|-------------------:|----------------:|
| GALAUSDT |          4883 |                       0 |             7.2020 |          7.2228 |
| IMXUSDT  |          1366 |                       0 |            10.0251 |         10.0351 |

## Results

| symbol   |   maker_fills |   quote_attempts |   maker_volume_usd |   liquidation_volume_usd |   total_volume_usd |   liquidation_share |   gross_cash_pnl |   fees_usd |   net_pnl_usd |   net_bp_per_volume |   max_inventory_usd |
|:---------|--------------:|-----------------:|-------------------:|-------------------------:|-------------------:|--------------------:|-----------------:|-----------:|--------------:|--------------------:|--------------------:|
| GALAUSDT |             0 |                0 |             0.0000 |                   0.0000 |             0.0000 |                 nan |           0.0000 |     0.0000 |        0.0000 |                 nan |              0.0000 |
| IMXUSDT  |             0 |                0 |             0.0000 |                   0.0000 |             0.0000 |                 nan |           0.0000 |     0.0000 |        0.0000 |                 nan |              0.0000 |

Aggregate net bp / genuine volume: **nan bp**
GALA minimum screen (>=5 maker fills and net>=0): **FAIL**.