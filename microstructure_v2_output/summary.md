# Microstructure v2 — after-fee OOS screen

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT
- Data: 2026-08-05..2026-08-16; train through 2026-08-10; later days OOS
- Bucket: 250 ms
- Tested latency proxies: ~250ms, 500ms
- Taker fee: 5 bp/side = 10.0 bp round trip
- Entry/exit price proxy uses future trade-bar last price; therefore fee-only net is optimistic before spread/slippage.
- 10 ms cloud latency cannot be identified honestly from 250 ms aggTrade bars; ~250 ms is the fastest defensible screen here.

## PASS count (OOS mean net >= 0 after 10 bp fees): 0

No selected strategy remained non-negative after the 10 bp taker round-trip fee.

## Top 15 regardless of pass/fail

| symbol | family | latency | hold | n | gross bp | net bp | 95% low net |
|---|---|---:|---:|---:|---:|---:|---:|
| ETHUSDT | momentum_cont | ~250ms | 60s | 90 | 0.865 | -9.135 | -9.593 |
| ETHUSDT | momentum_cont | 500ms | 120s | 88 | 0.708 | -9.292 | -9.836 |
| ETHUSDT | momentum_cont | ~250ms | 120s | 88 | 0.703 | -9.297 | -9.843 |
| ETHUSDT | flow_extreme | ~250ms | 120s | 622 | 0.478 | -9.522 | -9.801 |
| BTCUSDT | momentum_cont | ~250ms | 60s | 157 | 0.404 | -9.596 | -9.905 |
| BTCUSDT | momentum_cont | ~250ms | 60s | 638 | 0.393 | -9.607 | -9.799 |
| BTCUSDT | momentum_cont | 500ms | 60s | 157 | 0.391 | -9.609 | -9.918 |
| ETHUSDT | flow_extreme | ~250ms | 120s | 330 | 0.385 | -9.615 | -9.964 |
| ETHUSDT | underreact_cont | 500ms | 120s | 89 | 0.360 | -9.640 | -10.311 |
| ETHUSDT | underreact_cont | ~250ms | 120s | 89 | 0.354 | -9.646 | -10.317 |
| ETHUSDT | flow_extreme | ~250ms | 120s | 917 | 0.341 | -9.659 | -9.885 |
| ETHUSDT | flow_accel | 500ms | 120s | 68 | 0.311 | -9.689 | -10.560 |
| BTCUSDT | flow_extreme | ~250ms | 120s | 1471 | 0.294 | -9.706 | -9.859 |
| BTCUSDT | flow_extreme | 500ms | 120s | 1470 | 0.279 | -9.721 | -9.873 |
| BTCUSDT | flow_extreme | ~250ms | 60s | 1845 | 0.268 | -9.732 | -9.822 |

## PASS after an additional 1 bp round-trip stress: 0

None.

## Interpretation rule

A fee-only PASS is only a candidate, not deployable proof, because historical aggTrades do not reconstruct the contemporaneous spread, taker slippage, or L2 queue. A strategy that is negative even before those costs is rejected.