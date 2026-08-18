# Sweep-exhaustion maker exploratory screen

- Uses three previously untouched-to-this-rule prospective captures: v6 WOO/CHZ/GALA, v7 BB/ROBO/RARE, v9 FET/OP/WIF.
- Signal after an extreme prior 1s aggressor burst (|flow|>=0.70): final 250ms flow must stop being toxic and mid must stop extending adversely.
- `refill` additionally requires >=20% same-side displayed queue replenishment; `flip` requires current 250ms flow to flip direction.
- $100 maker quote; full displayed L1 queue + own size must trade through within 3s; no cancellation credit.
- Reported value is future side-adjusted mid markout minus 2bp maker entry fee. This is exploratory rule discovery, not OOS proof.

## Positive 5s value configs (>=2 fills)

| symbol   | capture              |   lat_ms | profile   |   attempts |   fills |   fill_rate |   value1_bp |   value5_bp |   value15_bp |   positive5_rate |
|:---------|:---------------------|---------:|:----------|-----------:|--------:|------------:|------------:|------------:|-------------:|-----------------:|
| GALAUSDT | maker_live_v6_output |       10 | flip      |         40 |      16 |       0.400 |      -2.859 |       4.024 |        2.280 |            0.750 |
| GALAUSDT | maker_live_v6_output |      240 | flip      |         48 |      15 |       0.312 |      -2.678 |       2.380 |        0.071 |            0.467 |

## Best regardless of sign

| symbol   | capture              |   lat_ms | profile   |   attempts |   fills |   fill_rate |   value1_bp |   value5_bp |   value15_bp |   positive5_rate |
|:---------|:---------------------|---------:|:----------|-----------:|--------:|------------:|------------:|------------:|-------------:|-----------------:|
| GALAUSDT | maker_live_v6_output |       10 | flip      |         40 |      16 |       0.400 |      -2.859 |       4.024 |        2.280 |            0.750 |
| GALAUSDT | maker_live_v6_output |      240 | flip      |         48 |      15 |       0.312 |      -2.678 |       2.380 |        0.071 |            0.467 |
| GALAUSDT | maker_live_v6_output |       10 | halt      |         56 |      16 |       0.286 |      -5.641 |      -1.556 |        1.040 |            0.438 |
| GALAUSDT | maker_live_v6_output |      240 | refill    |         34 |       9 |       0.265 |      -8.896 |      -1.649 |        2.201 |            0.333 |
| GALAUSDT | maker_live_v6_output |      240 | halt      |         56 |      16 |       0.286 |      -7.566 |      -3.687 |       -4.138 |            0.375 |
| GALAUSDT | maker_live_v6_output |       10 | refill    |         33 |       9 |       0.273 |      -9.663 |      -4.710 |        1.412 |            0.333 |
| OPUSDT   | maker_live_v9_output |       10 | halt      |         66 |       2 |       0.030 |      -8.180 |      -8.180 |       -2.000 |            0.000 |
| OPUSDT   | maker_live_v9_output |      240 | halt      |         66 |       2 |       0.030 |      -8.180 |      -8.180 |       -2.000 |            0.000 |