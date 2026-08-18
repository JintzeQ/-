# USDC turnover-first maker sweep v2 (WS tape)

- Prospective 120s; maker fee 0bp, residual taker flatten 4bp; $70 quote/$70 inventory cap.
- Objective: maximize maker volume/min subject to net bp/volume >= 0.
- Soft toxicity only changes quote size; no hard flow filter.
- qfrac=1.0 is conservative displayed-L1 primary; 0.5/0.25 sensitivity.
- aggTrade has no RPI flag. Only trades at/through visible touch deplete queue, but same-price RPI contamination cannot be ruled out.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |
|:---------|--------------:|-------------:|-------------------:|
| BTCUSDC  |           753 |            0 |             0.0156 |
| ETHUSDC  |          1413 |            0 |             0.0529 |
| SOLUSDC  |           620 |            0 |             1.3241 |
| XRPUSDC  |           457 |            0 |             1.0090 |

## Conservative q=1.0 positive EV

None.

## All q sensitivities positive EV

None.

## Highest turnover regardless of EV

No valid results.