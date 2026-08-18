# GALA sweep-flip context screen

- Exploratory post-analysis of the fresh GALA OOS capture; this is NOT a new OOS result.
- Tests whether successful round trips cluster when the fade direction is aligned with the preceding 5s/15s mid trend and/or the prospective maker-exit side has low queue-seconds based on prior 5s aggressor rate.
- No flow-entry thresholds or exit accounting are changed. Any promising context rule must be frozen and re-run on another fresh capture.

## Results (n>=2)

|   lat_ms | profile      |   n |   mean_net_bp |   total_net_bp |   mm_exit_rate |   win_net |   mean_ret5 |   mean_qsec |
|---------:|:-------------|----:|--------------:|---------------:|---------------:|----------:|------------:|------------:|
|       10 | trend15_q5   |   4 |        -1.946 |         -7.783 |          0.500 |     0.500 |       3.597 |       1.305 |
|       10 | trend15      |   5 |        -5.849 |        -29.244 |          0.400 |     0.400 |       1.433 |       2.060 |
|       10 | trend15_q2   |   3 |        -8.476 |        -25.428 |          0.333 |     0.333 |       4.796 |       0.737 |
|       10 | all          |   8 |       -13.438 |       -107.505 |          0.250 |     0.250 |       1.788 |       2.010 |
|       10 | trend5       |   6 |       -19.656 |       -117.937 |          0.167 |     0.167 |       2.390 |       1.579 |
|       10 | trend5_q5    |   6 |       -19.656 |       -117.937 |          0.167 |     0.167 |       2.390 |       1.579 |
|      240 | trend5_q2    |   4 |       -21.382 |        -85.529 |          0.000 |     0.000 |       9.008 |       0.706 |
|       10 | trend5_q2    |   4 |       -23.191 |        -92.765 |          0.000 |     0.000 |       3.586 |       0.745 |
|      240 | all          |   9 |       -25.037 |       -225.331 |          0.111 |     0.111 |       7.217 |       3.245 |
|      240 | trend15_q5   |   2 |       -25.053 |        -50.106 |          0.000 |     0.000 |      21.708 |       1.367 |
|      240 | trend5_q5    |   5 |       -25.669 |       -128.346 |          0.000 |     0.000 |       7.206 |       1.261 |
|       10 | trend15_flow |   2 |       -28.650 |        -57.301 |          0.000 |     0.000 |       7.223 |       2.793 |
|      240 | trend5       |   6 |       -30.933 |       -185.597 |          0.000 |     0.000 |       9.601 |       3.509 |
|       10 | trend5_flow  |   2 |       -32.142 |        -64.283 |          0.000 |     0.000 |      10.834 |       0.606 |
|      240 | trend15      |   4 |       -32.205 |       -128.819 |          0.000 |     0.000 |      14.442 |       5.640 |
|      240 | trend5_flow  |   4 |       -33.945 |       -135.781 |          0.000 |     0.000 |      12.617 |       3.995 |
|      240 | trend15_flow |   3 |       -35.786 |       -107.358 |          0.000 |     0.000 |      12.007 |       6.777 |

## Enriched 10ms cycles

| side   |   net_bp | exit_mode   |   ret5_bp |   ret15_bp |   flow5 |   exit_qsec_proxy | aligned5   | aligned15   | flowalign   |
|:-------|---------:|:------------|----------:|-----------:|--------:|------------------:|:-----------|:------------|:------------|
| buy    |   -7.000 | MT          |     7.140 |    -35.549 |  -0.084 |             1.588 | True       | False       | False       |
| buy    |  -28.444 | MT          |     0.000 |    -14.270 |   0.273 |             0.709 | True       | False       | True        |
| buy    |  -42.817 | MT          |     0.000 |     -7.156 |  -0.194 |             3.484 | True       | False       | False       |
| sell   |   31.894 | MM          |     7.186 |    -28.643 |   0.412 |             1.527 | False      | True        | False       |
| sell   |   17.645 | MM          |     0.000 |    -57.409 |   0.660 |             3.010 | True       | True        | False       |
| sell   |  -21.482 | MT          |   -14.467 |    -36.088 |   0.284 |             0.180 | True       | True        | False       |
| buy    |  -35.839 | MT          |    21.668 |     28.912 |   0.277 |             0.503 | True       | True        | True        |
| buy    |  -21.461 | MT          |    -7.223 |      7.233 |   0.112 |             5.083 | False      | True        | True        |