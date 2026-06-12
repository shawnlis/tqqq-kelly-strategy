# Underwater / Pain Audit for MA150 Risk-Off QQQ

## Executive Verdict

MA150 improves pain versus raw TQQQ and simple blends on several path metrics, but it is still not paper-trading ready.

This audit measures investor tolerance metrics, not just CAGR or Calmar. It does not approve paper trading.

## Data Availability

- TQQQ, 70/30, 50/50, and MA150 rows use recomputed daily OOS equity from QQQ/TQQQ prices.
- Current strict complex baseline has only retained summary CSV rows; daily underwater metrics are marked unavailable and are not invented.
- Worst 1M/3M/6M returns use 21/63/126 trading-day rolling returns.

## MA150 Pain Summary

| slice | CAGR | MaxDD | UlcerIndex | PainIndex | LongestUnderwaterDays | TimeToRecoveryAfterMaxDDDays | PctTimeBelowPreviousHigh | BestMissed1MReturnVsTQQQ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | 92.28% | -58.67% | 17.17% | 11.09% | 134.0 | 80.000 | 80.32% | 43.88% |
| 2022_2023 | 12.02% | -50.92% | 32.43% | 28.97% | 362.0 | 115.000 | 97.21% | 35.07% |
| 2024_latest | 46.24% | -40.42% | 16.72% | 12.77% | 159.0 | 84.000 | 89.05% | 43.62% |

## Comparison

Versus TQQQ buy-and-hold: CAGR wins 1/3, MaxDD wins 3/3, UlcerIndex wins 3/3, PainIndex wins 2/3, less time underwater wins 1/3.

Versus 70/30 TQQQ/QQQ: CAGR wins 2/3, MaxDD wins 3/3, UlcerIndex wins 1/3, PainIndex wins 1/3, less time underwater wins 1/3.

Versus 50/50 TQQQ/QQQ: CAGR wins 3/3, MaxDD wins 2/3, UlcerIndex wins 1/3, PainIndex wins 1/3, less time underwater wins 1/3.

Current strict complex baseline daily equity was unavailable, so underwater duration, UlcerIndex, PainIndex, rolling-loss, and recovery metrics are not computed for it. Summary-only comparison: MA150 wins CAGR 3/3, MaxDD 0/3, Calmar 2/3.

## Behavior Interpretation

- MA150 is simple trend-following exposure control: it helps primarily by spending less time in fully levered TQQQ during sustained downtrends.
- If MaxDD improves but recovery days or missed positive months worsen, the strategy may feel safer but still frustrate investors during recoveries.
- Compared with simple blends, the key question is whether lower UlcerIndex/PainIndex compensates for missed rebounds and higher switching uncertainty.
- Compared with the current complex baseline, this audit cannot prove daily pain superiority because the complex daily equity artifacts were not retained.

## Paper-Trading Readiness

NOT READY. The MA150 baseline has useful path-dependent evidence, but paper trading still requires retained daily artifacts, slippage/tax stress, and a frozen go/no-go hurdle.

## Full Summary

| slice | strategy | CAGR | MaxDD | Calmar | UlcerIndex | PainIndex | LongestUnderwaterDays | AverageUnderwaterDays | TimeToRecoveryAfterMaxDDDays | Worst1MReturn | Worst3MReturn | Worst6MReturn | BestMissed1MReturnVsTQQQ | RecoveryParticipationAfterDrawdown | NewEquityHighs | PctTimeBelowPreviousHigh | data_availability |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | TQQQ buy-and-hold | 106.73% | -69.92% | 1.526 | 17.91% | 11.15% | 98.0 | 9.540 | 77.000 | -69.04% | -57.65% | -45.75% | 0.00% | 1.000 | 156.000 | 79.39% | daily_equity_recomputed |
| 2019_2021 | 70/30 TQQQ/QQQ | 87.96% | -59.78% | 1.471 | 14.03% | 8.53% | 94.0 | 8.866 | 73.000 | -58.85% | -46.96% | -35.09% | 16.11% | 0.730 | 163.000 | 78.47% | daily_equity_recomputed |
| 2019_2021 | 50/50 TQQQ/QQQ | 74.15% | -51.88% | 1.429 | 11.50% | 6.86% | 92.0 | 8.821 | 71.000 | -50.95% | -39.28% | -27.97% | 26.61% | 0.585 | 166.000 | 78.07% | daily_equity_recomputed |
| 2019_2021 | MA150 risk-off QQQ | 92.28% | -58.67% | 1.573 | 17.17% | 11.09% | 134.0 | 10.133 | 80.000 | -57.11% | -41.46% | -26.82% | 43.88% | 0.692 | 149.000 | 80.32% | daily_equity_recomputed |
| 2022_2023 | TQQQ buy-and-hold | -22.29% | -81.02% | -0.275 | 60.82% | 58.96% | 500.0 | 500.000 | n/a | -43.88% | -60.45% | -69.45% | 0.00% | 1.000 | 1.000 | 99.80% | daily_equity_recomputed |
| 2022_2023 | 70/30 TQQQ/QQQ | -13.33% | -71.30% | -0.187 | 50.08% | 47.99% | 500.0 | 500.000 | n/a | -36.15% | -51.04% | -59.44% | 13.39% | 0.740 | 1.000 | 99.80% | daily_equity_recomputed |
| 2022_2023 | 50/50 TQQQ/QQQ | -8.05% | -62.96% | -0.128 | 42.12% | 39.86% | 500.0 | 500.000 | n/a | -30.67% | -43.97% | -51.61% | 21.83% | 0.586 | 1.000 | 99.80% | daily_equity_recomputed |
| 2022_2023 | MA150 risk-off QQQ | 12.02% | -50.92% | 0.236 | 32.43% | 28.97% | 362.0 | 69.571 | 115.000 | -22.63% | -28.66% | -40.23% | 35.07% | 0.710 | 13.000 | 97.21% | daily_equity_recomputed |
| 2024_latest | TQQQ buy-and-hold | 56.07% | -58.04% | 0.966 | 16.90% | 12.20% | 161.0 | 16.781 | 86.000 | -38.63% | -54.21% | -45.61% | 0.00% | 1.000 | 75.000 | 87.75% | daily_equity_recomputed |
| 2024_latest | 70/30 TQQQ/QQQ | 48.83% | -49.05% | 0.996 | 13.24% | 9.25% | 148.0 | 15.143 | 73.000 | -31.64% | -45.51% | -36.92% | 16.32% | 0.757 | 82.000 | 86.60% | daily_equity_recomputed |
| 2024_latest | 50/50 TQQQ/QQQ | 43.00% | -42.29% | 1.017 | 10.83% | 7.42% | 143.0 | 13.075 | 68.000 | -26.75% | -39.06% | -30.78% | 26.43% | 0.610 | 89.000 | 85.46% | daily_equity_recomputed |
| 2024_latest | MA150 risk-off QQQ | 46.24% | -40.42% | 1.144 | 16.72% | 12.77% | 159.0 | 20.185 | 84.000 | -35.02% | -34.98% | -22.76% | 43.62% | 0.513 | 67.000 | 89.05% | daily_equity_recomputed |
| 2019_2021 | current strict complex baseline | 35.28% | -22.17% | 1.591 | n/a | n/a | nan | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | summary_only_no_daily_equity |
| 2022_2023 | current strict complex baseline | 6.70% | -33.14% | 0.202 | n/a | n/a | nan | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | summary_only_no_daily_equity |
| 2024_latest | current strict complex baseline | 17.89% | -22.11% | 0.809 | n/a | n/a | nan | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | summary_only_no_daily_equity |
