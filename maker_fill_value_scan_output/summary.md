# Current maker fill-value scan

- Prospective capture: 120s across 20 USDⓈ-M contracts.
- $100 passive quote. Full displayed L1 queue + own size must trade through within 3s; no cancellation credit.
- Fill value = side-adjusted future mid markout from maker fill price minus the 2bp maker fee.
- This deliberately tests one passive fill before building a round-trip strategy. A positive 5s fill value is necessary evidence, not sufficient deployment proof.
- `protected`: bid only under nonnegative 5s flow / ask only under nonpositive flow. `neutral`: |5s flow|<=0.20.

## Diagnostics

| symbol   |   book_events |   agg_trades |   median_spread_bp |   p75_spread_bp |   median_bid_usd |   median_ask_usd |
|:---------|--------------:|-------------:|-------------------:|----------------:|-----------------:|-----------------:|
| FETUSDT  |           626 |           72 |              8.337 |           8.344 |         8179.944 |         7379.580 |
| OPUSDT   |          2199 |          165 |             12.353 |          12.369 |        21670.804 |        20857.970 |
| WIFUSDT  |           375 |           13 |              7.421 |           7.421 |         6773.355 |         4613.341 |
| IMXUSDT  |           588 |           61 |              9.985 |           9.985 |          766.430 |          931.486 |
| ARKMUSDT |           138 |            7 |             11.608 |          11.608 |          637.226 |         2663.063 |
| JUPUSDT  |           412 |           18 |              6.077 |           6.081 |         3389.029 |         3198.590 |
| WOOUSDT  |           177 |           14 |              9.761 |           9.770 |         1439.284 |           76.304 |
| CHZUSDT  |           246 |           23 |              8.543 |           8.543 |         4190.215 |         4658.420 |
| GALAUSDT |          2578 |          399 |              6.961 |           6.966 |         1922.182 |         2564.180 |
| BBUSDT   |           234 |           24 |              9.447 |           9.447 |          402.372 |          175.074 |
| ROBOUSDT |           771 |           95 |              7.482 |           7.488 |          568.802 |          849.188 |
| RAREUSDT |           245 |           38 |              8.167 |           8.167 |         1302.654 |         1025.350 |
| APEUSDT  |           219 |           12 |              8.207 |           8.207 |         3382.751 |          918.516 |
| BLURUSDT |           311 |           19 |              7.803 |           7.803 |          691.740 |          373.344 |
| GMTUSDT  |           238 |           39 |              3.388 |           3.392 |           54.094 |           41.636 |
| SANDUSDT |           738 |           59 |              2.644 |           2.647 |          373.208 |          301.337 |
| MASKUSDT |           183 |           15 |              2.910 |           2.910 |           96.236 |          105.860 |
| API3USDT |           156 |            9 |              5.242 |           5.242 |          212.322 |          258.620 |
| C98USDT  |           101 |           10 |              7.181 |           7.181 |          236.292 |          116.037 |
| ONEUSDT  |           564 |           44 |              4.308 |           5.728 |           66.741 |           48.198 |

## Positive 5s after-fee passive fill value (>=3 modeled fills)

None.

## Top candidates regardless of sign

| symbol   |   lat_ms | profile   |   attempts |   fills |   fill_rate |   mean_spread_bp |   value1_bp |   value5_bp |   value15_bp |   positive5_rate |
|:---------|---------:|:----------|-----------:|--------:|------------:|-----------------:|------------:|------------:|-------------:|-----------------:|
| IMXUSDT  |       10 | all       |         70 |       6 |       0.086 |            9.978 |      -3.661 |      -1.999 |       -0.336 |            0.500 |
| IMXUSDT  |      240 | all       |         70 |       5 |       0.071 |            9.977 |      -4.991 |      -2.997 |       -1.001 |            0.400 |
| GALAUSDT |      240 | neutral   |         26 |       4 |       0.154 |            6.963 |     -10.704 |      -3.740 |      -12.441 |            0.250 |
| GALAUSDT |       10 | protected |         44 |       9 |       0.205 |            6.956 |      -6.253 |      -3.930 |       -2.386 |            0.444 |
| GALAUSDT |       10 | neutral   |         25 |       5 |       0.200 |            6.959 |      -6.876 |      -4.087 |       -4.092 |            0.400 |
| IMXUSDT  |       10 | protected |         47 |       4 |       0.085 |            9.978 |      -4.494 |      -4.494 |       -1.999 |            0.250 |
| GALAUSDT |      240 | protected |         44 |       7 |       0.159 |            6.956 |      -6.473 |      -5.475 |       -3.488 |            0.286 |
| GALAUSDT |      240 | all       |         59 |      14 |       0.237 |            6.960 |      -9.966 |      -5.490 |       -6.490 |            0.500 |
| GALAUSDT |       10 | all       |         57 |      15 |       0.263 |            6.960 |     -11.064 |      -5.493 |       -8.283 |            0.467 |
| IMXUSDT  |      240 | protected |         48 |       4 |       0.083 |            9.980 |      -9.488 |      -6.990 |       -4.495 |            0.000 |
| ROBOUSDT |      240 | all       |         68 |       6 |       0.088 |            7.475 |     -11.961 |     -11.966 |      -14.462 |            0.333 |
| ROBOUSDT |       10 | all       |         69 |       5 |       0.072 |            7.472 |     -14.702 |     -14.707 |      -14.708 |            0.200 |