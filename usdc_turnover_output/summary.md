# USDC turnover-first maker sweep

- Prospective capture: 180s; BTC/ETH/SOL/XRP USDC perpetuals.
- Objective: maximize genuine modeled maker volume/min subject to net EV >= 0.
- Maker fee = 0bp; only residual end-of-sample taker flatten pays 4bp.
- $70 nominal quote, $70 inventory cap; 10ms and 240ms order-to-exchange.
- Soft toxicity: quotes remain active; exposed-side size is reduced, never hard-filtered solely by flow.
- Queue brackets: 1.0x displayed L1 is conservative primary; 0.5x/0.25x are sensitivity only.
- RPI trades are excluded from ordinary visible-queue depletion. Any BBO touch change cancels/requotes and forfeits queue progress.

## Diagnostics

| symbol   |   book_events |   nonrpi_trades |   median_spread_bp |   p75_spread_bp |
|:---------|--------------:|----------------:|-------------------:|----------------:|
| BTCUSDC  |          1539 |               0 |             0.0156 |          0.0156 |
| ETHUSDC  |          2523 |               0 |             0.0528 |          0.0528 |
| SOLUSDC  |           991 |               0 |             1.3235 |          1.3237 |
| XRPUSDC  |           885 |               0 |             1.0088 |          1.0092 |

## Conservative queue (1.0x): net>=0 ranked by maker volume/min

None.

## All queue sensitivities: net>=0 ranked by maker volume/min

None.

## Best turnover regardless of EV

No valid results.

Short prospective sample only. Positive EV is a research candidate, not deployment proof; market-data receive latency, exact exchange queue priority/cancellations ahead, funding, and account-specific fee/VIP-volume eligibility remain outside this replay.