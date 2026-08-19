# Strategy #19 — Broad-Universe Persistent Multi-Venue Repricing

Objective: test whether Strategy #18's thicker persistent cross-venue repricing mechanism generalizes beyond BTC/ETH/DOGE and can raise turnover without changing the signal thresholds, formation horizon, exit rule, fee model, or latency assumptions that produced the #18 edge.

## Research hygiene

Strategy #18 is frozen and is not retuned. The #19 dates, universe, thresholds, exits, costs, latency rows, and PASS gates below are fixed before #19 outcomes are observed. The two #18 evaluation blocks (2024-12-01..14 and 2026-06-01..14) are excluded.

## Venues and universe

Three symmetric perpetual venues: Binance USD-M, Bybit USDT perpetual, Gate USD-M. No venue is preassigned as leader or lagger.

Frozen broad universe of mature contracts intended to have long public-history coverage on all three venues:

- BTCUSDT
- ETHUSDT
- BNBUSDT
- XRPUSDT
- ADAUSDT
- DOGEUSDT
- SOLUSDT
- LTCUSDT
- LINKUSDT
- TRXUSDT

Missing venue-symbol-days count against the coverage gate; symbols are not dropped after outcomes.

## Untouched validation blocks

- BLOCK_A_2023_OCT: 2023-10-01 through 2023-10-10 UTC.
- BLOCK_B_2025_NOV: 2025-11-01 through 2025-11-10 UTC.

All 10 calendar days in each block are evaluation days. Zero-trade days remain in the positive-day denominator.

## Signal — unchanged from #18

All venues are aligned to completed 1-second last-trade prices. For each candidate target venue at each completed second:

- The other two venues must have same-sign 10-second returns.
- Median signed leader 10-second move must be at least 25bp.
- Both leaders must still move in the same direction over the latest 5 seconds, and each 5-second move must be at least 25% of its own 10-second move.
- The candidate target must have moved less than both leaders over 10 seconds.
- Signed leader-consensus 10-second return minus target 10-second return must be at least 18bp.
- Target latest-5-second signed move must remain below leader median latest-5-second move.
- If multiple targets qualify, route to the venue with the largest current lag gap.
- Per-symbol de-overlap remains 60 seconds.

No threshold is loosened to manufacture turnover.

## Execution and exit — unchanged from #18

Single selected execution venue only. Entry is the first raw trade after signal +100ms primary / +250ms stress. No maker assumption.

After a minimum 5-second hold, recompute the relative catch-up using the original signal-time target/leader baselines. Exit as soon as the remaining relative lag falls to <=5bp, using the first target-venue trade after the completed-second exit condition plus the same latency. Otherwise exit at max 60 seconds after executable entry.

Fixed fee-only cost: 10bp round trip. Hard stress: 12bp round trip. No market making and no fee optimization assumption.

## PASS gates

100ms per block:

- coverage >=95%
- >=50 completed trades and >=5.0 completed trades per calendar day
- >=8 represented symbols
- all 3 execution venues represented
- gross mean >=15bp
- fee-only mean >5bp
- fee-only median >0
- remove-best-5% fee-only mean >0
- 12bp-stress mean >3bp
- >=60% of all 10 evaluation days have positive fee-only mean; zero-trade days are non-positive for this gate
- top-symbol share <=25%
- top-venue share <=70%

250ms per block:

- coverage >=95%
- >=50 completed trades and >=5.0 completed trades per calendar day
- >=8 represented symbols
- all 3 execution venues represented
- fee-only mean >0
- fee-only median >0
- remove-best-5% fee-only mean >0

Both blocks and both latency rows must pass. No post-outcome symbol removal, venue selection, threshold change, formation shortening, de-overlap change, or exit/hold retuning.

## Decision rule

PASS means the #18 mechanism has shown broader-universe generalization and sufficient turnover to justify a separate #20 executable-BBO/slippage/size validation. FAIL means #19 is rejected as specified; observed #19 blocks cannot be used to retune this frozen design.