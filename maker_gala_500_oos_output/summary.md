# Fresh GALA $500 locked inventory-maker OOS

- Fresh prospective capture: 300s; GALAUSDT only.
- Rule frozen: 10ms, symmetric BBO, $500/order, $500 inventory cap, min spread 4.25bp.
- BBO from Futures public stream; trades from Futures market aggTrade stream. Queue depletion counts only trades at/through our visible touch.
- Full displayed L1 queue + own order must trade through. Any touch change cancels quote and forfeits queue progress; no cancellation credit.
- Maker fee 2bp/fill; residual inventory taker-flattened at opposite touch with 5bp fee.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p75_spread_bp |
|:---------|--------------:|-------------:|-------------------:|----------------:|
| GALAUSDT |          4153 |          656 |             7.1968 |          7.2176 |

## Results

| symbol   |   maker_fills |   quote_attempts |   maker_volume_usd |   liquidation_volume_usd |   total_volume_usd |   liquidation_share |   gross_cash_pnl |   fees_usd |   net_pnl_usd |   net_bp_per_volume |   max_inventory_usd |
|:---------|--------------:|-----------------:|-------------------:|-------------------------:|-------------------:|--------------------:|-----------------:|-----------:|--------------:|--------------------:|--------------------:|
| GALAUSDT |             3 |              105 |          1500.0000 |                 498.9003 |          1998.9003 |              0.2496 |           1.0997 |     0.5495 |        0.5503 |              2.7528 |            503.0753 |

Aggregate net bp / genuine volume: **2.7528 bp**
GALA predeclared screen (>=5 maker fills and net>=0): **FAIL**.