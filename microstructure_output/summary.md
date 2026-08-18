# BTCUSDT microstructure pilot

- Data: 2026-08-10..2026-08-16; train through 2026-08-13; remaining days OOS
- Grid: 500 ms; entry latency proxy: 500 ms
- Price proxy: last trade price; no historical L2 queue reconstruction.

## OOS gross

| strategy | n | mean gross bp | t-stat | break-even cost/side bp |
|---|---:|---:|---:|---:|
| flow_persistence_30s | 3597 | 0.1019 | 4.49 | 0.0509 |
| flow_cont_30s | 4720 | 0.0950 | 5.72 | 0.0475 |
| flow_persistence_5s | 6494 | 0.0419 | 6.71 | 0.0209 |
| flow_cont_5s | 11178 | 0.0365 | 9.51 | 0.0182 |
| flow_persistence_1s | 7945 | 0.0110 | 4.71 | 0.0055 |
| flow_cont_1s | 18848 | 0.0085 | 7.25 | 0.0042 |
| absorption_rev_1s | 1432 | -0.0109 | -2.54 | -0.0054 |
| absorption_rev_5s | 1039 | -0.0365 | -2.53 | -0.0182 |
| absorption_rev_30s | 905 | -0.0948 | -2.35 | -0.0474 |

## OOS at 1 bp/side

| strategy | n | mean net bp | win rate | total net bp |
|---|---:|---:|---:|---:|
| flow_persistence_30s | 3597 | -1.8981 | 0.064 | -6827.5 |
| flow_cont_30s | 4720 | -1.9050 | 0.050 | -8991.5 |
| flow_persistence_5s | 6494 | -1.9581 | 0.008 | -12716.2 |
| flow_cont_5s | 11178 | -1.9635 | 0.004 | -21948.2 |
| flow_persistence_1s | 7945 | -1.9890 | 0.001 | -15802.7 |
| flow_cont_1s | 18848 | -1.9915 | 0.000 | -37536.6 |
| absorption_rev_1s | 1432 | -2.0109 | 0.001 | -2879.6 |
| absorption_rev_5s | 1039 | -2.0365 | 0.006 | -2115.9 |
| absorption_rev_30s | 905 | -2.0948 | 0.048 | -1895.8 |

## Flow footprint

- strong_flow_next_same_sign_prob: 0.853048
- all_flow_next_same_sign_prob: 0.752712
- fi1_autocorr_0.5s: 0.503256
- fi1_autocorr_1s: 0.159525
- fi1_autocorr_2.5s: 0.157948
- fi1_autocorr_5s: 0.152576
- fi1_autocorr_10s: 0.148839

Positive gross edge is not deployable unless break-even per-side cost exceeds realistic all-in execution cost. Footprint persistence does not identify a specific bot.