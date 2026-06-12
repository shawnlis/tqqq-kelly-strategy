# Minimal TQQQ Baseline Go/No-Go Hurdles

## Current Decision

HOLD.

NOT READY FOR PAPER TRADING.

The current best minimal candidate, `MA150 risk-off QQQ`, has research value as a simple TQQQ risk-management baseline. It does not yet have enough evidence for paper trading. It is not a TQQQ killer.

## Paper-Trading Entry Hurdles

Strict OOS is mandatory.

All hurdles below must be evaluated under strict OOS, raw TQQQ benchmark returns, no extra TQQQ expense deduction, no QQQ5, no DL, no Bandit, no Kelly, and no look-ahead.

1. In strict OOS multi-slice testing, `MA150 risk-off QQQ` or any later frozen strategy must beat both 70/30 TQQQ/QQQ and 50/50 TQQQ/QQQ on Calmar in at least 2/3 slices.
2. MaxDD must be better than raw TQQQ buy-and-hold in 3/3 slices.
3. UlcerIndex must be better than raw TQQQ buy-and-hold in 3/3 slices.
4. PainIndex must be better than raw TQQQ buy-and-hold in at least 2/3 slices.
5. Relative to 70/30 or 50/50 TQQQ/QQQ, the strategy cannot win only on CAGR; it must show a stable advantage in Calmar or UlcerIndex.
6. Synthetic or hybrid QQQ5 must not be used.
7. DL, Bandit, Kelly, or any complex overlay must not enter headline results.
8. The strategy must pass a slippage and cost stress test.
9. Daily artifacts must exist for behavior attribution: equity, weights/exposure, signal state, drawdown, and slice metadata.
10. The run must preserve no-lookahead timing, strict OOS slicing, raw TQQQ benchmark returns, and no extra expense deduction.

## Kill Conditions

Kill or demote the MA strategy if any of these conditions hold:

1. A simple blend is as good or better on pain and Calmar.
2. The MA strategy wins CAGR but does not improve holding pain.
3. The case depends on one isolated slice.
4. Any new variant comes from MA-window grid search or post-result parameter mining.
5. Results require synthetic/hybrid QQQ5, DL, Bandit, Kelly, or other excluded modules.
6. Daily artifacts contradict the slice-level story.

## Next Required Evidence

1. Daily-artifact-based behavior attribution for the frozen candidate and simple blends.
2. Slippage and cost stress.
3. Tax and turnover sensitivity if the strategy has material switching.
4. Frozen-rule rerun without parameter changes.
5. A written go/no-go review against the hurdles above before any paper-trading discussion.
