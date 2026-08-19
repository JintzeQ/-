# Strategy #17 — Dynamic Multi-Venue Consensus + Smart Lagger Routing

## Objective
Test whether same-asset price discovery across multiple exchanges can identify a dynamically lagging venue with enough remaining 2-second move to clear a fixed 10 bp VIP0 taker/taker round trip, while retaining high turnover and without market making.

## Venues
- Binance USD-M perpetual
- Bybit USDT perpetual
- Gate USD-M perpetual

No venue is permanently assigned as leader or lagger. Any of the three may become the execution venue on a given signal.

## Universe
BTCUSDT, ETHUSDT, DOGEUSDT.

## Data
Tick/public trade archives from each venue, aligned into completed 1-second last-trade prices. Raw venue trades are retained for executable 100 ms / 250 ms entry and 2-second exit simulation.

## Prequential structure
Two frozen blocks: 2024-09-01..2024-09-14 and 2025-09-01..2025-09-14. First 4 days are warm-up only. Next 10 days are OOS evaluation. Each evaluation day is scored using sufficient statistics accumulated strictly from prior days; the evaluation day is added to training only after its signals have been scored and executed.

## Model
For each asset and each candidate target venue independently, an expanding ridge regression predicts the target venue's next 2-second return from current completed 1-second returns on target + the other two venues. Ridge penalty is fixed at 10,000; returns beyond +/-200 bp are excluded as data-quality/outlier protection.

## Signal
For candidate target venue j:
1. The other two venues must have same-sign current 1-second returns.
2. Median absolute leader move must be >=10 bp.
3. Target venue must have moved less in absolute value than both other venues.
4. Past-only ridge prediction for the target's next 2 seconds must have the same sign as the leader consensus and magnitude >=12 bp.
5. If multiple venues qualify, route to the target with the largest absolute predicted remaining move.
6. De-overlap 3 seconds per symbol.

## Execution and costs
Single venue only. Entry at first raw trade on selected lagger after signal +100 ms primary / +250 ms stress. Exit at first raw trade after 2 seconds. Fee-only cost 10 bp round trip. Hard stress 12 bp. No maker assumption, no fee advantage assumption, no market making.

## PASS gate
At 100 ms, per block: paired coverage >=95%; >=500 completed trades; all 3 assets; >=2 execution venues; >=50 trades/evaluation-day; fee-only mean >2 bp; fee-only median >0; remove-best-5% >0; 12 bp stress mean >0; >=60% positive-mean days; top-symbol share <=60%; top-venue share <=70%.

At 250 ms, per block: coverage >=95%; >=500 completed; all 3 assets; >=2 execution venues; >=50/day; fee-only mean/median/remove-best-5% all >0.

Both blocks and both latency rows must pass. No post-outcome retuning of model, thresholds, dates, assets, hold, venues, or gate.
