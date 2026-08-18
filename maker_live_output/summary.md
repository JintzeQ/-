# Live maker microstructure probe

- Capture: 180s exchange-time BBO bookTicker + aggTrade
- Symbols: 4USDT, PTBUSDT, BBUSDT
- Fees: maker 2.0 bp/side; emergency taker 5.0 bp/side
- Fill model: displayed L1 queue ahead; no cancellation credit; aggressive trade-through required.
- Latency scenarios: 10ms and 240ms order-to-exchange. Market-data delivery latency is NOT modeled.

## Market diagnostics

| symbol | book events | trades | median spread bp | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| 4USDT | 371 | 0 | 8.82 | 13.22 | 14.14 |
| PTBUSDT | 437 | 0 | 14.04 | 15.21 | 16.39 |
| BBUSDT | 292 | 0 | 18.67 | 18.67 | 18.67 |

## Net-positive completed-cycle configurations

None in this short probe.

## Best configurations regardless of sign

No completed cycles.

This is a prospective microstructure probe, not deployment proof. Short sample size, hidden/RPI liquidity, own-order size, market-data receive latency, and exact exchange queue priority remain unmodeled.