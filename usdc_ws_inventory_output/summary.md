# Robust USDC WebSocket inventory-maker probe

- Prospective capture 150s; SOL/XRP/DOGE/LTC USDC perpetuals.
- Maker fee 0bp; only residual end-of-window taker flatten charged 4bp.
- $70 order / $70 inventory cap; 10ms and 240ms order-to-exchange.
- bookTicker and aggTrade use separate Futures WebSocket endpoints.
- Any touch change cancels quote and forfeits queue progress; no cancellation credit; own size sits behind modeled queue.
- Primary conservative fill model: 1.0x displayed L1 queue and only 50% of qualifying aggTrade volume credited to depletion. 0.5x queue / 100% credit are sensitivity only.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p75_spread_bp |
|:---------|--------------:|-------------:|-------------------:|----------------:|
| SOLUSDC  |           603 |           48 |             1.3241 |          1.3241 |
| XRPUSDC  |           477 |           23 |             1.0086 |          1.0087 |
| DOGEUSDC |           384 |            5 |             1.4333 |          1.4334 |
| LTCUSDC  |           107 |            3 |             2.2586 |          2.2586 |

## PRIMARY conservative configs: net>=0, >=3 maker fills, ranked turnover

None.

## All sensitivity configs net>=0, >=3 maker fills

None.

## Best net EV configs regardless of sign

No config reached 3 maker fills.

Short prospective sample only. aggTrade does not expose RPI status here, so trade-credit haircut is used as a conservative sensitivity rather than assuming every aggressive trade depletes visible queue. Exact queue priority, market-data receive latency, funding and VIP-volume credit still require live/account validation.