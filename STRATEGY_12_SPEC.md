# Strategy #12 — OI-Collapse Forced-Deleveraging Flush -> Passive Snapback

This strategy is frozen before outcome inspection.

## Data limitation
Binance historical public `liquidationSnapshot` files are no longer available, and `forceOrders` is not a historical market-wide liquidation tape. Therefore Strategy #12 uses an explicit **forced-deleveraging proxy**, not claimed direct liquidation events: a sharp fall in open interest contracts must coincide with a large price move, aligned taker flow, and elevated volume.

## Frozen signal
- USD-M perpetuals: SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH, TRX, ETC.
- 5m OI contract change <= -0.8%.
- Absolute same-window 5m price move >= 20bp.
- Taker-flow ratio aligned with price move >= 0.60.
- 5m quote volume >= 1.5x shifted trailing 6h median.
- Event timestamp is the ending 5m OI snapshot after the associated 5m kline is complete.
- Trade opposite the forced-flow direction (snapback hypothesis).
- De-overlap 10m per symbol.

## Validation blocks
- Block A: 45 return-independent SHA256-selected calendar days from 2023-01-01 through 2023-05-31.
- Block B: 45 return-independent SHA256-selected calendar days from 2026-01-01 through 2026-05-31.
- These periods are not globally pristine across all prior alpha families, but Strategy #12 OI-collapse/tick-execution outcomes, sampled days, direction, thresholds, and gates were not selected from these results.

## Frozen execution
- Primary latency 100ms; stress 250ms.
- After event+latency, observe first aggTrade and place one-sided passive fade 0.5bp away from current price.
- Fill requires opposite-aggressor trade-through >=0.1bp within 3s; touch alone does not count.
- Hold 30s from fill; aggressive exit at first aggTrade at/after target, requiring trade within 1s.
- Small-size execution proxy; queue depth is not modeled; no continuous/two-sided quoting.

## Cost model
- Optimistic: 5bp round trip (0 maker + 5 taker).
- Conservative primary: 8.5bp (2.5 maker + 5 taker + 1bp aggressive slippage).
- Hard: 12bp (5 maker + 5 taker + 2bp aggressive slippage).

## Frozen gate
For each block at 100ms: data coverage >=95%, aggTrade coverage >=95%, >=450 completed, >=8 symbols, >=10 completed/sample-day, entry fill >=20%, conservative mean >3bp, median >0, remove-best-5% mean >0, hard-stress mean >0, >=60% positive weeks, top-symbol share <=25%, long and short conservative means both >0.

At 250ms: coverage >=95%, >=450 completed, >=8 symbols, >=10/day, fill >=20%, conservative mean/median/remove-best-5% all >0.

Both blocks and both latency rows must pass. No post-outcome tuning of OI threshold, return threshold, flow threshold, volume threshold, side, passive offset, timeout, hold, universe, sampled dates, or gates.
