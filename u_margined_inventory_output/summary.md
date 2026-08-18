# BTCU / ETHU RPI-aware inventory maker probe

- Fresh prospective capture: 180s.
- Current fee hypothesis: U-margined promotion maker 0bp, taker 4bp for regular/VIP1.
- Execution: 10ms order-to-exchange, symmetric BBO, $100/order, $100 inventory cap.
- Tape: REST recent market trades; `isRPITrade=true` explicitly excluded.
- Full visible L1 queue + own order must trade through. Any touch change cancels and forfeits queue progress; no cancellation credit.
- Residual inventory is taker-flattened at opposite touch and charged 4bp.

## Diagnostics

| symbol   |   book_events |   nonrpi_trades |   median_spread_bp |   p75_spread_bp |   median_bid_usd |   median_ask_usd |
|:---------|--------------:|----------------:|-------------------:|----------------:|-----------------:|-----------------:|
| BTCU     |           473 |               0 |             2.5116 |          2.6833 |         698.5461 |         673.1540 |
| ETHU     |            75 |               0 |             4.6500 |          5.2556 |         304.7263 |         302.9488 |

## Results

No valid tapes/fills.

Aggregate: volume=$0.00, net=$0.0000, net=nan bp/volume.

This is a short prospective candidate screen. Fee-promotion eligibility and volume-credit rules must be confirmed against the account/current Binance terms before deployment.