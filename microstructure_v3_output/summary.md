# Cross-asset lead-lag v3 — OOS after-fee screen

- Data 2026-08-05..2026-08-16, train through 2026-08-10
- Pairs: BTCUSDT→ETHUSDT, BTCUSDT→SOLUSDT, ETHUSDT→SOLUSDT
- Entry target is the lagging futures contract; 250ms/500ms latency proxies.
- Taker/taker fee hurdle = 10 bp round trip.

## PASS after 10 bp fees: 0

None.

## Top 15 OOS regardless of pass

| pair | family | latency | hold | n | gross bp | net bp | 95% low net |
|---|---|---:|---:|---:|---:|---:|---:|
| ETHUSDT→SOLUSDT | catchup90 | ~250ms | 300s | 22 | 3.549 | -6.451 | -11.157 |
| ETHUSDT→SOLUSDT | catchup90 | 500ms | 300s | 22 | 3.131 | -6.869 | -11.470 |
| ETHUSDT→SOLUSDT | catchup90 | ~250ms | 300s | 27 | 2.871 | -7.129 | -12.028 |
| ETHUSDT→SOLUSDT | catchup90 | 500ms | 300s | 27 | 2.482 | -7.518 | -12.220 |
| BTCUSDT→SOLUSDT | overshoot_reversal | 500ms | 300s | 46 | 1.840 | -8.160 | -11.685 |
| BTCUSDT→SOLUSDT | catchup90 | ~250ms | 300s | 63 | 1.663 | -8.337 | -10.556 |
| BTCUSDT→SOLUSDT | overshoot_reversal | ~250ms | 300s | 46 | 1.611 | -8.389 | -11.961 |
| BTCUSDT→SOLUSDT | catchup90 | ~250ms | 300s | 147 | 1.257 | -8.743 | -10.395 |
| BTCUSDT→SOLUSDT | catchup75 | ~250ms | 300s | 161 | 1.247 | -8.753 | -10.461 |
| BTCUSDT→ETHUSDT | catchup90 | ~250ms | 60s | 30 | 1.227 | -8.773 | -9.917 |
| BTCUSDT→ETHUSDT | catchup75 | ~250ms | 60s | 24 | 1.209 | -8.791 | -10.103 |
| BTCUSDT→ETHUSDT | catchup75 | 500ms | 60s | 24 | 1.171 | -8.829 | -10.122 |
| BTCUSDT→ETHUSDT | catchup90 | ~250ms | 120s | 30 | 1.141 | -8.859 | -10.552 |
| BTCUSDT→ETHUSDT | catchup90 | 500ms | 120s | 30 | 1.094 | -8.906 | -10.595 |
| BTCUSDT→ETHUSDT | catchup90 | ~250ms | 30s | 18 | 1.069 | -8.931 | -9.948 |

## Gross >= 4 bp maker/maker fee hurdle (fill NOT modeled): 0

None.

Maker/maker comparison is only a prerequisite screen: historical L2 queue/fill is not modeled, so it is not a maker backtest.