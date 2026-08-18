# Inside-spread maker v7

- Uses the untouched 180s current-market capture from v6.
- Signal: visible spread >=2 ticks. After 10ms/240ms latency, post one tick inside the spread.
- Because the order creates a new best visible price, queue-ahead is modeled as zero; own $100 size must still fully execute.
- Entry quote is cancelled after 1s or immediately when the historical BBO changes. No fill = no trade.
- On maker fill, immediately taker-flatten after latency; fee hurdle = 7bp round trip.
- `protect` quotes away from dominant 5s aggressor flow; `neutral` only acts when |flow|<=0.20.
- Fixed rules; no parameter search on the sample.

## Results

| symbol   |   lat_ms | mode    |   signals |   fills |   fill_rate |   mean_gross_bp |   mean_net_bp |   total_net_bp |   win_net |   ci95_low_net |   roundtrip_volume_usd |
|:---------|---------:|:--------|----------:|--------:|------------:|----------------:|--------------:|---------------:|----------:|---------------:|-----------------------:|
| WOOUSDT  |       10 | protect |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| WOOUSDT  |       10 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| WOOUSDT  |      240 | protect |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| WOOUSDT  |      240 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| CHZUSDT  |       10 | protect |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| CHZUSDT  |       10 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| CHZUSDT  |      240 | protect |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| CHZUSDT  |      240 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| GALAUSDT |       10 | protect |         6 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| GALAUSDT |       10 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| GALAUSDT |      240 | protect |         2 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |
| GALAUSDT |      240 | neutral |         0 |       0 |       0.000 |             nan |           nan |          0.000 |       nan |            nan |                  0.000 |

## Net-positive configurations with at least one actual modeled fill

None.