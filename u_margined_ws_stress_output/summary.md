# BTCU / ETHU aggTrade queue-stress maker probe

- Fresh prospective capture: 180s.
- Fee hypothesis from current U-margined promotion: maker 0bp, taker 4bp.
- 10ms order-to-exchange, symmetric BBO, $100/order, $100 inventory cap.
- BBO from Futures public stream; trade tape from Futures market aggTrade stream.
- Because aggTrade cannot identify RPI prints, fill credit requires trade-through of 1.25x or 1.50x displayed L1 queue + own size. This is an explicit conservative contamination stress, not exact RPI reconstruction.
- Any touch change cancels the resting quote and forfeits all queue progress. No cancellation credit.
- Residual inventory taker-flattened at opposite touch and charged 4bp.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p75_spread_bp |   median_bid_usd |   median_ask_usd |
|:---------|--------------:|-------------:|-------------------:|----------------:|-----------------:|-----------------:|
| BTCU     |            70 |            0 |             2.1850 |          2.5597 |         691.9052 |         698.5112 |
| ETHU     |            60 |            0 |             4.1751 |          5.2719 |         312.0719 |         308.4204 |

## Results

No valid results.

**qmult=1.25 aggregate:** volume=$0.00, net=$0.0000, net=nan bp/volume.

**qmult=1.50 aggregate:** volume=$0.00, net=$0.0000, net=nan bp/volume.

If qmult=1.50 remains nonnegative with multiple maker fills, the next step is a fresh fixed-qmult=1.50 replication; otherwise this route is not promoted.