# Current maker v6 — prospective queue-turnover probe

- Captured 180s of current USDⓈ-M market data.
- BBO: Binance Futures `public` WebSocket; aggTrades: `market` WebSocket.
- Symbols: WOOUSDT, CHZUSDT, GALAUSDT.
- $100 per order; displayed L1 queue + own size must be traded through; **no cancellation credit**.
- Entry is maker. Exit attempts maker for 20s; failed exit is marked at opposite touch as taker.
- Fees: maker/maker 4bp; maker/taker 7bp.
- Profiles are fixed ex ante; no optimization on this sample.

## Diagnostics

| symbol   |   duration_s |   book_events |   agg_trades |   median_spread_bp |   p25_spread_bp |   p75_spread_bp |   median_bid_notional |   median_ask_notional |
|:---------|-------------:|--------------:|-------------:|-------------------:|----------------:|----------------:|----------------------:|----------------------:|
| WOOUSDT  |      179.039 |           264 |           22 |              9.799 |           9.799 |           9.799 |               536.795 |               729.208 |
| CHZUSDT  |      178.965 |           853 |           95 |              8.536 |           8.529 |           8.536 |              4477.354 |              4209.590 |
| GALAUSDT |      179.530 |          5305 |          778 |              6.866 |           6.824 |           6.894 |              2034.265 |              2067.783 |

## Net-positive completed-cycle configs (n>=2)

None.

## Best configs regardless of sign

| symbol   |   lat_ms | profile   |   n |   mean_net_bp |   total_net_bp |   mean_gross_bp |   mm_exit_rate |   win_net |   mean_markout1_bp |   mean_markout5_bp |   ci95_low_net |   notional_roundtrip_usd |
|:---------|---------:|:----------|----:|--------------:|---------------:|----------------:|---------------:|----------:|-------------------:|-------------------:|---------------:|-------------------------:|
| CHZUSDT  |       10 | loose     |   3 |        -5.998 |        -17.993 |           0.002 |          0.333 |     0.333 |             -1.421 |             -4.265 |        -17.397 |                  600.000 |
| CHZUSDT  |      240 | loose     |   3 |        -8.844 |        -26.532 |          -2.844 |          0.333 |     0.000 |             -1.421 |             -4.265 |        -15.615 |                  600.000 |
| GALAUSDT |      240 | loose     |  14 |       -16.080 |       -225.123 |         -10.794 |          0.571 |     0.143 |             -2.446 |             -0.492 |        -28.021 |                 2800.000 |
| GALAUSDT |       10 | loose     |  15 |       -19.391 |       -290.864 |         -14.191 |          0.600 |     0.067 |             -5.956 |             -3.888 |        -30.784 |                 3000.000 |
| GALAUSDT |       10 | balanced  |  15 |       -19.868 |       -298.022 |         -14.668 |          0.600 |     0.267 |             -4.337 |             -3.427 |        -34.324 |                 3000.000 |
| GALAUSDT |       10 | strict    |  10 |       -20.207 |       -202.068 |         -14.407 |          0.400 |     0.300 |             -2.049 |             -7.537 |        -35.581 |                 2000.000 |
| GALAUSDT |      240 | strict    |  10 |       -25.143 |       -251.426 |         -19.943 |          0.600 |     0.100 |             -8.222 |             -7.538 |        -41.840 |                 2000.000 |
| GALAUSDT |      240 | balanced  |  11 |       -26.527 |       -291.795 |         -20.618 |          0.364 |     0.091 |             -7.172 |             -7.168 |        -42.118 |                 2200.000 |

This 3-minute prospective probe is a candidate screen, not deployment proof. Longer capture across regimes is required before risking capital.