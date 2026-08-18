# USDC queue-cancellation credit turnover test

- Prospective 120s SOL/XRP; $70 quote/$70 inventory cap; maker 0bp; end-window taker 4bp.
- Full displayed L1 is initial queue ahead. Trades receive 50% depletion credit.
- Cancellation-credit scenarios 0/25/50/100% estimate what fraction of same-price displayed-size shrink was ahead of us.
- Soft weak filter only sizes down toxic-side quotes; no hard filtering.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |
|:---------|--------------:|-------------:|-------------------:|
| SOLUSDC  |           687 |           42 |             1.3227 |
| XRPUSDC  |           590 |           35 |             1.0083 |

## Net>=0, >=3 fills ranked turnover

None.

## All results

| symbol   |   lat_ms |   cancel_credit | filter   |   maker_fills |   quote_attempts |   maker_volume_usd |   maker_volume_per_min |   liquidation_share |   net_pnl_usd |   net_bp_per_volume |   mean_mark1_bp |   max_inventory_usd |
|:---------|---------:|----------------:|:---------|--------------:|-----------------:|-------------------:|-----------------------:|--------------------:|--------------:|--------------------:|----------------:|--------------------:|
| XRPUSDC  |       10 |          1.0000 | none     |             2 |               21 |           140.0000 |                82.8215 |              0.0001 |       -0.0141 |             -1.0091 |          0.5042 |             70.0177 |
| XRPUSDC  |      240 |          1.0000 | none     |             2 |               21 |           140.0000 |                82.8215 |              0.0001 |       -0.0141 |             -1.0091 |          0.5042 |             70.0177 |
| XRPUSDC  |       10 |          1.0000 | weak     |             1 |               59 |            70.0000 |                41.4107 |              0.5000 |       -0.0209 |             -1.4959 |          0.5042 |             70.0106 |
| XRPUSDC  |      240 |          1.0000 | weak     |             1 |               68 |            70.0000 |                41.4107 |              0.5000 |       -0.0209 |             -1.4959 |          0.5042 |             70.0106 |
| XRPUSDC  |       10 |          0.5000 | none     |             1 |               18 |            70.0000 |                41.4107 |              0.5001 |       -0.0563 |             -4.0171 |          0.5043 |             70.0247 |
| XRPUSDC  |      240 |          0.5000 | none     |             1 |               18 |            70.0000 |                41.4107 |              0.5001 |       -0.0563 |             -4.0171 |          0.5043 |             70.0247 |
| SOLUSDC  |       10 |          1.0000 | none     |             1 |               10 |            70.0000 |                36.3914 |              0.5001 |       -0.0558 |             -3.9844 |         -1.9844 |             70.0324 |
| SOLUSDC  |      240 |          1.0000 | none     |             1 |               11 |            70.0000 |                36.3914 |              0.5001 |       -0.0558 |             -3.9844 |         -1.9844 |             70.0324 |
| SOLUSDC  |       10 |          0.0000 | none     |             0 |               16 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          0.0000 | weak     |             0 |               45 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          0.2500 | none     |             0 |               16 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          0.2500 | weak     |             0 |               45 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          0.5000 | none     |             0 |               16 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          0.5000 | weak     |             0 |               45 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |       10 |          1.0000 | weak     |             0 |               45 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.0000 | none     |             0 |               18 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.0000 | weak     |             0 |               52 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.2500 | none     |             0 |               18 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.2500 | weak     |             0 |               52 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.5000 | none     |             0 |               18 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          0.5000 | weak     |             0 |               52 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| SOLUSDC  |      240 |          1.0000 | weak     |             0 |               52 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |       10 |          0.0000 | none     |             0 |               22 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |       10 |          0.0000 | weak     |             0 |               62 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |       10 |          0.2500 | none     |             0 |               22 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |       10 |          0.2500 | weak     |             0 |               62 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |       10 |          0.5000 | weak     |             0 |               62 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |      240 |          0.0000 | none     |             0 |               22 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |      240 |          0.0000 | weak     |             0 |               71 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |      240 |          0.2500 | none     |             0 |               22 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |      240 |          0.2500 | weak     |             0 |               71 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |
| XRPUSDC  |      240 |          0.5000 | weak     |             0 |               71 |             0.0000 |                 0.0000 |            nan      |        0.0000 |            nan      |        nan      |              0.0000 |