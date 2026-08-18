# BB/ROBO/RARE maker v7 — prospective queue-turnover probe

- Capture: 180s; order $100.
- BBO from Futures public stream; aggTrades from Futures market stream.
- Displayed L1 queue + own order must be traded through; no cancellation credit.
- Fees: maker/maker 4bp; maker/taker emergency exit 7bp.
- Latency: 10ms and 240ms. Profiles fixed ex ante.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p25_spread_bp |   p75_spread_bp |   median_bid_notional |   median_ask_notional |
|:---------|--------------:|-------------:|-------------------:|----------------:|----------------:|----------------------:|----------------------:|
| BBUSDT   |           630 |           41 |              9.456 |           9.456 |          18.868 |               536.119 |               740.918 |
| ROBOUSDT |           617 |          106 |              7.493 |           7.488 |           7.499 |               756.677 |               813.137 |
| RAREUSDT |           292 |           25 |              8.167 |           8.167 |           8.173 |               762.736 |              1473.519 |

## Net-positive configs (n>=2)

None.

## Best regardless of sign

| symbol   |   lat_ms | profile   |   n |   mean_net_bp |   total_net_bp |   mean_gross_bp |   mm_exit_rate |   win_net |   mean_markout1_bp |   mean_markout5_bp |   ci95_low_net |   notional_roundtrip_usd |
|:---------|---------:|:----------|----:|--------------:|---------------:|----------------:|---------------:|----------:|-------------------:|-------------------:|---------------:|-------------------------:|
| ROBOUSDT |       10 | balanced  |   2 |        -9.248 |        -18.496 |          -3.748 |          0.500 |     0.000 |              0.003 |              0.003 |        -19.534 |                  400.000 |
| ROBOUSDT |      240 | balanced  |   2 |        -9.248 |        -18.496 |          -3.748 |          0.500 |     0.000 |              0.003 |              0.003 |        -19.534 |                  400.000 |
| ROBOUSDT |       10 | loose     |   4 |       -11.871 |        -47.483 |          -5.621 |          0.250 |     0.000 |             -1.872 |             -1.872 |        -17.013 |                  800.000 |
| ROBOUSDT |      240 | loose     |   4 |       -11.871 |        -47.483 |          -5.621 |          0.250 |     0.000 |             -1.872 |             -1.872 |        -17.013 |                  800.000 |

Short prospective candidate screen only; exact queue priority, hidden/RPI liquidity, market-data receive latency and cancel/replace behavior remain unmodeled.