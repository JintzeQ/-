# Locked conservative inventory-maker replay

- Configuration frozen: 10ms, symmetric BBO quoting, $100/order, $100 inventory cap, minimum spread 4.25bp.
- No flow filter and no parameter search.
- Full displayed L1 queue + own size must trade through. Any BBO touch change while resting cancels the order and forfeits all queue progress. No cancellation credit.
- Maker fee 2bp/fill; residual inventory always taker-flattened at end with 5bp fee.

| capture   |   maker_fills |   quote_attempts |   maker_volume_usd |   liquidation_volume_usd |   total_volume_usd |   liquidation_share |   gross_cash_pnl |   fees_usd |   net_pnl_usd |   net_bp_per_volume |   max_inventory_usd |
|:----------|--------------:|-----------------:|-------------------:|-------------------------:|-------------------:|--------------------:|-----------------:|-----------:|--------------:|--------------------:|--------------------:|
| gala_v6   |             5 |              174 |           500.0000 |                  99.1645 |           599.1645 |              0.1655 |           0.8355 |     0.1496 |        0.6859 |             11.4481 |            101.2078 |
| gala_oos  |            11 |              289 |          1100.0000 |                  98.2849 |          1198.2849 |              0.0820 |           1.7151 |     0.2691 |        1.4459 |             12.0668 |            101.8307 |
| op_v9     |             0 |               14 |             0.0000 |                   0.0000 |             0.0000 |            nan      |           0.0000 |     0.0000 |        0.0000 |            nan      |              0.0000 |
| robo_v7   |             1 |               14 |           100.0000 |                 100.0750 |           200.0750 |              0.5002 |          -0.0750 |     0.0700 |       -0.1450 |             -7.2473 |            100.1874 |

Aggregate net bp per genuine volume: **9.9467 bp**
Aggregate volume: **$1997.52**; aggregate net PnL: **$1.9869**.