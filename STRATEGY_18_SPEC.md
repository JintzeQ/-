# Strategy #18 — Persistent Multi-Venue Repricing

Objective: sacrifice some turnover to seek materially thicker per-trade edge while remaining a short-horizon, non-market-making strategy.

## Venues and universe

Three symmetric perpetual venues: Binance USD-M, Bybit USDT perpetual, Gate USD-M. No venue is preassigned as leader or lagger. Frozen symbols: BTCUSDT, ETHUSDT, DOGEUSDT.

## Validation blocks

- BLOCK_A_2024_DEC: 2024-12-01 through 2024-12-14.
- BLOCK_B_2026_JUN: 2026-06-01 through 2026-06-14.

All 14 days in each block are evaluation days. Dates, venue roles, symbols, thresholds, exit rule, fees, and gates are frozen before observing results.

## Signal

All venues are aligned to completed 1-second last-trade prices. For each candidate target venue at each completed second:

- The other two venues must have same-sign 10-second returns.
- Median signed leader 10-second move must be at least 25bp.
- Both leaders must still move in the same direction over the latest 5 seconds, and each 5-second move must be at least 25% of its own 10-second move.
- The candidate target must have moved less than both leaders over 10 seconds.
- Signed leader-consensus 10-second return minus target 10-second return must be at least 18bp.
- Target latest-5-second signed move must remain below leader median latest-5-second move.
- If multiple targets qualify, route to the venue with the largest current lag gap.
- Per-symbol de-overlap: 60 seconds.

This is a persistent-repricing mechanism, not a retuned version of Strategy #16/#17's 1-second/2-second rule.

## Execution and exit

Single selected execution venue only. Entry is the first raw trade after signal +100ms primary / +250ms stress. No maker assumption.

After a minimum 5-second hold, recompute the relative catch-up using the original signal-time target/leader baselines. Exit as soon as the remaining relative lag falls to <=5bp, using the first target-venue trade after the completed-second exit condition plus the same latency. Otherwise exit at max 60 seconds after executable entry.

Fixed fee-only cost: 10bp round trip. Hard stress: 12bp round trip. No market making and no fee optimization assumption.

## PASS gates

100ms per block: coverage >=95%; >=70 completed trades; all 3 symbols; >=2 execution venues; >=5 completed/day; gross mean >=15bp; fee-only mean >5bp; fee-only median >0; remove-best-5% fee-only >0; 12bp-stress mean >3bp; >=60% positive-mean days; top-symbol share <=60%; top-venue share <=70%.

250ms per block: coverage >=95%; >=70 completed; all 3 symbols; >=2 execution venues; >=5/day; fee-only mean/median/remove-best-5% all >0.

Both blocks and both latency rows must pass. No post-outcome threshold, hold, date, venue, or symbol retuning.
