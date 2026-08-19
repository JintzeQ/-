# Strategy #20 — Lifecycle-Reentry Persistent Multi-Venue Repricing

Objective: test whether Strategy #19's turnover shortfall was caused materially by the fixed 60-second per-symbol de-overlap, without loosening the alpha thresholds or adding outcome-fitted quality filters.

## Research hygiene

Strategies #18 and #19 are frozen. Their observed blocks are excluded from #20. Strategy #20 changes exactly one structural mechanism: signal eligibility after a completed trade. The 10-second formation, 5-second persistence, 25bp leader threshold, 18bp residual threshold, venue routing rule, entry latency, exit rule, fee assumptions, symbol universe, and PASS economics remain unchanged from #19.

No #18/#19 outcome is used to choose a new price/return threshold. The lifecycle rule below is derived from the pre-existing minimum-hold architecture rather than fitted from realized PnL.

## Venues and universe

Binance USD-M perpetual, Bybit USDT perpetual, and Gate USD-M perpetual remain symmetric leader/lagger candidates.

Frozen universe, unchanged from #19:

BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, SOLUSDT, LTCUSDT, LINKUSDT, TRXUSDT.

Missing venue-symbol-days count against coverage. No symbol may be removed after outcomes.

## Fresh validation blocks

- BLOCK_A_2023_FEB: 2023-02-01 through 2023-02-10 UTC.
- BLOCK_B_2025_FEB: 2025-02-01 through 2025-02-10 UTC.

These blocks are distinct from #18's 2024-12 / 2026-06 and #19's 2023-10 / 2025-11 blocks. All 10 calendar days per block are evaluation days; zero-trade days count as non-positive in the positive-day gate.

## Signal — unchanged

At each completed second and candidate target venue:

- the other two venues have same-sign 10-second returns;
- median signed leader 10-second move >=25bp;
- both leaders continue in that direction over the latest 5 seconds;
- each leader's latest-5-second move is >=25% of its own 10-second move;
- target moved less than both leaders over 10 seconds;
- signed leader-consensus return minus target return >=18bp;
- target latest-5-second signed move remains below leader median latest-5-second move;
- if multiple targets qualify at the same second, route to the largest current lagger.

No signal threshold is loosened.

## The only #20 change: lifecycle-based re-entry

The fixed 60-second signal de-overlap is removed.

For each symbol and latency row independently:

1. only one position may be open at a time;
2. while that position is open, later signals for the symbol are ignored;
3. after the executable exit, the symbol enters a fixed 5-second cooldown;
4. after that cooldown, a completely fresh signal must satisfy the unchanged #18/#19 formation and residual conditions before re-entry.

The 5-second cooldown is fixed equal to the already-frozen minimum hold; it is not selected from #18/#19 realized PnL or a parameter grid. There is no pyramiding and no overlapping same-symbol exposure.

## Execution and exit — unchanged

Entry is the first target-venue raw trade after signal +100ms primary / +250ms stress. Minimum hold is 5 seconds. Exit when the original relative residual closes to <=5bp; otherwise maximum hold is 60 seconds. Fixed taker/taker cost is 10bp round trip; hard stress is 12bp. No maker assumption, fee optimization, market making, or post-outcome execution retuning.

## PASS gates

100ms, independently in each block:

- coverage >=95%;
- >=50 completed trades and >=5.0 completed trades per calendar day;
- >=8 represented symbols;
- all 3 execution venues represented;
- gross mean >=15bp;
- fee-only mean >5bp;
- fee-only median >0;
- remove-best-5% fee-only mean >0;
- 12bp-stress mean >3bp;
- >=60% of all evaluation days have positive fee-only mean;
- top-symbol share <=25%;
- top-venue share <=70%.

250ms, independently in each block:

- coverage >=95%;
- >=50 completed trades and >=5.0 completed trades per calendar day;
- >=8 represented symbols;
- all 3 execution venues represented;
- fee-only mean >0;
- fee-only median >0;
- remove-best-5% fee-only mean >0.

Both blocks and both latency rows must pass. No post-outcome threshold changes, symbol removal, venue selection, cooldown changes, formation changes, or exit changes.

## Decision rule

PASS means lifecycle-based re-entry resolves the throughput bottleneck without destroying fee-adjusted typical-trade economics, justifying a separate executable-BBO/slippage/size validation. FAIL means #20 is rejected as specified; these blocks cannot be used to tune another cooldown value.