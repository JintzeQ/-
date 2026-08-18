# Cancel-requote maker v8

- Uses the same untouched 180s current-market capture as v6.
- Every entry/exit maker quote lives for only 250ms, then is cancelled and all flow/queue conditions are recomputed.
- Full displayed L1 queue + own $100 size must trade through inside that 250ms window; no cancellation credit.
- Maker exit is accepted only if its locked gross spread is >=4bp.
- Residual inventory is never ignored: hard-stop/end liquidation uses taker, total fee 7bp.
- 10ms vs 240ms order-to-exchange latency tested explicitly.

## Results

| symbol   |   lat_ms | profile    |   quote_attempts |   cycles |   mean_net_bp |   total_net_bp |   mean_gross_bp |   mm_exit_rate |   win_net |   ci95_low |   roundtrip_volume_usd |
|:---------|---------:|:-----------|-----------------:|---------:|--------------:|---------------:|----------------:|---------------:|----------:|-----------:|-----------------------:|
| WOOUSDT  |       10 | protective |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| WOOUSDT  |       10 | neutral    |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| WOOUSDT  |       10 | strict     |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| WOOUSDT  |      240 | protective |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| WOOUSDT  |      240 | neutral    |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| WOOUSDT  |      240 | strict     |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |       10 | protective |                3 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |       10 | neutral    |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |       10 | strict     |                3 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |      240 | protective |                3 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |      240 | neutral    |                0 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| CHZUSDT  |      240 | strict     |                3 |        0 |       nan     |          0.000 |         nan     |        nan     |   nan     |    nan     |                  0.000 |
| GALAUSDT |       10 | protective |               66 |        4 |       -36.221 |       -144.883 |         -29.221 |          0.000 |     0.000 |    -67.126 |                800.000 |
| GALAUSDT |       10 | neutral    |               16 |        2 |       -12.420 |        -24.840 |          -6.920 |          0.500 |     0.500 |    -42.321 |                400.000 |
| GALAUSDT |       10 | strict     |               21 |        2 |       -34.530 |        -69.059 |         -27.530 |          0.000 |     0.000 |    -75.127 |                400.000 |
| GALAUSDT |      240 | protective |               77 |        2 |       -20.746 |        -41.492 |         -13.746 |          0.000 |     0.000 |    -34.328 |                400.000 |
| GALAUSDT |      240 | neutral    |               16 |        1 |       -27.675 |        -27.675 |         -20.675 |          0.000 |     0.000 |    nan     |                200.000 |
| GALAUSDT |      240 | strict     |               18 |        2 |       -31.084 |        -62.167 |         -24.084 |          0.000 |     0.000 |    -64.927 |                400.000 |

## Net-positive configurations with completed cycles

None.

## Cycles

| symbol   |   lat_ms | profile    | side   |     signal_ts |       fill_ts |   entry |   qsec_entry |   flow5_entry |   flow1_entry |   spread_entry_bp |      close_ts |   exit_px |   gross_bp |   fees_bp |   net_bp | exit_mode   |
|:---------|---------:|:-----------|:-------|--------------:|--------------:|--------:|-------------:|--------------:|--------------:|------------------:|--------------:|----------:|-----------:|----------:|---------:|:------------|
| GALAUSDT |       10 | protective | buy    | 1787022624500 | 1787022624589 |  0.0015 |       0.7678 |        0.0084 |        0.0731 |            6.8894 | 1787022654760 |    0.0014 |   -20.6754 |    7.0000 | -27.6754 | MT_stop     |
| GALAUSDT |       10 | protective | sell   | 1787022668750 | 1787022668830 |  0.0015 |       1.2923 |       -0.6408 |       -0.5731 |            6.8894 | 1787022699010 |    0.0015 |   -75.7576 |    7.0000 | -82.7576 | MT_stop     |
| GALAUSDT |       10 | protective | sell   | 1787022749000 | 1787022749105 |  0.0015 |       0.3140 |       -0.7525 |       -1.0000 |            6.8190 | 1787022779260 |    0.0015 |   -13.6333 |    7.0000 | -20.6333 | MT_stop     |
| GALAUSDT |       10 | protective | sell   | 1787022782500 | 1787022782669 |  0.0015 |       1.2757 |       -0.5284 |       -0.8461 |            6.8190 | 1787022783709 |    0.0015 |    -6.8166 |    7.0000 | -13.8166 | MT_end      |
| GALAUSDT |       10 | neutral    | buy    | 1787022624500 | 1787022624589 |  0.0015 |       0.7678 |        0.0084 |        0.0731 |            6.8894 | 1787022654760 |    0.0014 |   -20.6754 |    7.0000 | -27.6754 | MT_stop     |
| GALAUSDT |       10 | neutral    | buy    | 1787022714250 | 1787022714364 |  0.0015 |       0.1764 |       -0.1091 |        0.2992 |            6.8329 | 1787022720551 |    0.0015 |     6.8353 |    4.0000 |   2.8353 | MM          |
| GALAUSDT |       10 | strict     | buy    | 1787022624500 | 1787022624589 |  0.0015 |       0.7678 |        0.0084 |        0.0731 |            6.8894 | 1787022644760 |    0.0014 |   -48.2426 |    7.0000 | -55.2426 | MT_stop     |
| GALAUSDT |       10 | strict     | sell   | 1787022749000 | 1787022749105 |  0.0015 |       0.3140 |       -0.7525 |       -1.0000 |            6.8190 | 1787022769260 |    0.0015 |    -6.8166 |    7.0000 | -13.8166 | MT_stop     |
| GALAUSDT |      240 | protective | buy    | 1787022624250 | 1787022624589 |  0.0015 |       0.1995 |        0.0084 |        0.2373 |            6.8894 | 1787022654990 |    0.0014 |   -20.6754 |    7.0000 | -27.6754 | MT_stop     |
| GALAUSDT |      240 | protective | sell   | 1787022782250 | 1787022782669 |  0.0015 |       0.3228 |       -0.5345 |       -0.8760 |            6.8190 | 1787022783709 |    0.0015 |    -6.8166 |    7.0000 | -13.8166 | MT_end      |
| GALAUSDT |      240 | neutral    | buy    | 1787022624250 | 1787022624589 |  0.0015 |       0.1995 |        0.0084 |        0.2373 |            6.8894 | 1787022654990 |    0.0014 |   -20.6754 |    7.0000 | -27.6754 | MT_stop     |
| GALAUSDT |      240 | strict     | buy    | 1787022624250 | 1787022624589 |  0.0015 |       0.1995 |        0.0084 |        0.2373 |            6.8894 | 1787022644990 |    0.0014 |   -41.3508 |    7.0000 | -48.3508 | MT_stop     |
| GALAUSDT |      240 | strict     | sell   | 1787022782250 | 1787022782669 |  0.0015 |       0.3228 |       -0.5345 |       -0.8760 |            6.8190 | 1787022783709 |    0.0015 |    -6.8166 |    7.0000 | -13.8166 | MT_end      |