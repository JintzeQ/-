# Active wide-tick maker probe

- Capture 180s; order size $100
- Symbols: FETUSDT, OPUSDT, WIFUSDT
- Conservative L1 queue-ahead; no cancellation credit; own order size added behind queue.
- Maker/maker = 4bp fees; maker/taker emergency unwind = 7bp.
- Activity gate uses distinct trade timestamps in prior 5s.
- Latency: 10ms and 240ms order-to-exchange; market-data receive latency not modeled.

## Diagnostics

| symbol | book events | trades | distinct trade times | median spread bp | p75 |
|---|---:|---:|---:|---:|---:|
| FETUSDT | 728 | 0 | 0 | 8.24 | 8.25 |
| OPUSDT | 1391 | 0 | 0 | 12.08 | 12.08 |
| WIFUSDT | 920 | 0 | 0 | 7.35 | 7.36 |

## Net-positive completed-cycle configs (n>=3)

None.

## Best regardless of sign

No completed cycles.

Short prospective sample only. A positive configuration is a candidate, not deployment proof; exact exchange queue priority, hidden/RPI liquidity, feed latency and our own cancel/replace behavior remain unmodeled.