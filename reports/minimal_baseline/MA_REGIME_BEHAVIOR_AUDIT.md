# MA Regime Behavior Audit

## Executive Verdict

MA regime is worth continued research as simple trend-following exposure control, but it is not paper-trading ready.

Best fixed variant: `MA150 risk-off QQQ`.

Paper-trading status: NOT READY.
This is not a TQQQ killer result. It is a simple trend-following exposure-control candidate.

## Best Variant By Slice

| slice | CAGR | MaxDD | Calmar | Sharpe_DailyExcess | time_in_risk_on | switches_per_year | worst_missed_up_month | best_avoided_down_month |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | 92.28% | -58.67% | 1.573 | 1.354 | 92.47% | 2.996 | 2019-01 | 2020-03 |
| 2022_2023 | 12.02% | -50.92% | 0.236 | 0.433 | 50.30% | 6.036 | 2022-07 | 2022-04 |
| 2024_latest | 46.24% | -40.42% | 1.144 | 0.977 | 88.07% | 6.588 | 2025-05 | 2025-03 |

## Variant Ranking

| strategy | MeanCompositeRank | MeanCalmar | MeanSharpe | MeanCAGR | WorstMaxDD | MeanRiskOn | MeanSwitchesPerYear |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MA150 risk-off QQQ | 7.833 | 0.984 | 0.921 | 50.18% | -58.67% | 76.95% | 5.207 |
| MA200 risk-off QQQ | 9.167 | 0.964 | 0.887 | 49.47% | -58.67% | 77.75% | 4.391 |
| MA200 risk-off cash | 9.167 | 0.939 | 0.858 | 44.21% | -54.81% | 77.75% | 4.391 |
| MA250 risk-off QQQ | 11.333 | 0.882 | 0.810 | 47.61% | -62.89% | 78.63% | 5.389 |
| MA200 risk-off QQQ with 5-day confirmation | 14.667 | 0.766 | 0.811 | 43.37% | -68.45% | 77.25% | 1.217 |

## Behavior Findings

- MA150 risk-off QQQ beat TQQQ CAGR in 1/3 slices, had lower drawdown in 3/3, and improved Calmar in 3/3.
- Risk-off QQQ had mean CAGR 49.47% and mean MaxDD -52.25%; risk-off cash had mean CAGR 44.21% and mean MaxDD -47.88%. QQQ risk-off keeps equity beta in recoveries; cash risk-off is cleaner defense but can miss rebounds.
- 5-day confirmation changed average switches/year from 4.391 to 1.217 and mean CAGR from 49.47% to 43.37%. It can reduce whipsaw only if this drop in switch frequency offsets slower re-entry.
- 2022 defense should be judged by MaxDD, Calmar, and down-month avoidance, not by CAGR alone.
- 2024-latest recovery behavior should be judged by missed positive months and risk-on time; slow re-entry remains a key risk.

## Comparison Hurdles

Versus 70/30 TQQQ/QQQ: CAGR wins 2/3, MaxDD wins 3/3, Calmar wins 3/3, Sharpe wins 2/3.

Versus 50/50 TQQQ/QQQ: CAGR wins 3/3, MaxDD wins 2/3, Calmar wins 3/3, Sharpe wins 2/3.

Versus current strict complex baseline: CAGR wins 3/3, MaxDD wins 0/3, Calmar wins 2/3, Sharpe wins 2/3.

Versus current best minimal baseline: CAGR wins 1/3, MaxDD wins 2/3, Calmar wins 1/3, Sharpe wins 1/3.

## Full Variant Summary

| slice | strategy | CAGR | Benchmark_TQQQ_CAGR | MaxDD | Benchmark_TQQQ_MaxDD | Calmar | Sharpe_DailyExcess | Final_Equity | time_in_risk_on | switches_per_year | avg_days_in_risk_on | avg_days_in_risk_off | worst_missed_up_month | worst_missed_up_month_gap | best_avoided_down_month | best_avoided_down_month_gain |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | MA200 risk-off QQQ | 93.18% | 106.73% | -58.67% | -69.92% | 1.588 | 1.362 | 7.228 | 92.87% | 3.662 | 117.167 | 9.000 | 2019-01 | -17.91% | 2020-03 | 9.54% |
| 2019_2021 | MA200 risk-off cash | 78.80% | 106.73% | -54.81% | -69.92% | 1.438 | 1.254 | 5.730 | 92.87% | 3.662 | 117.167 | 9.000 | 2019-01 | -26.92% | 2020-03 | 7.42% |
| 2019_2021 | MA150 risk-off QQQ | 92.28% | 106.73% | -58.67% | -69.92% | 1.573 | 1.354 | 7.127 | 92.47% | 2.996 | 140.000 | 11.400 | 2019-01 | -17.91% | 2020-03 | 9.54% |
| 2019_2021 | MA250 risk-off QQQ | 100.50% | 106.73% | -58.67% | -69.92% | 1.713 | 1.421 | 8.083 | 93.79% | 2.996 | 142.000 | 9.400 | 2019-01 | -17.91% | 2020-03 | 9.54% |
| 2019_2021 | MA200 risk-off QQQ with 5-day confirmation | 81.50% | 106.73% | -68.45% | -69.92% | 1.191 | 1.205 | 5.994 | 92.73% | 0.999 | 351.000 | 27.500 | 2020-04 | -22.25% | 2019-05 | 0.00% |
| 2022_2023 | MA200 risk-off QQQ | 3.56% | -22.29% | -56.40% | -81.02% | 0.063 | 0.260 | 1.072 | 49.70% | 7.042 | 31.125 | 36.000 | 2022-07 | -26.45% | 2022-09 | 19.99% |
| 2022_2023 | MA200 risk-off cash | 8.37% | -22.29% | -51.46% | -81.02% | 0.163 | 0.351 | 1.173 | 49.70% | 7.042 | 31.125 | 36.000 | 2022-07 | -39.00% | 2022-04 | 30.60% |
| 2022_2023 | MA150 risk-off QQQ | 12.02% | -22.29% | -50.92% | -81.02% | 0.236 | 0.433 | 1.253 | 50.30% | 6.036 | 36.000 | 41.500 | 2022-07 | -26.45% | 2022-04 | 23.62% |
| 2022_2023 | MA250 risk-off QQQ | -7.00% | -22.29% | -62.89% | -81.02% | -0.111 | 0.019 | 0.866 | 48.30% | 9.054 | 24.200 | 28.778 | 2022-07 | -26.45% | 2022-04 | 22.92% |
| 2022_2023 | MA200 risk-off QQQ with 5-day confirmation | 6.96% | -22.29% | -53.71% | -81.02% | 0.130 | 0.327 | 1.143 | 48.50% | 1.006 | 121.500 | 258.000 | 2022-07 | -26.45% | 2022-04 | 23.62% |
| 2024_latest | MA200 risk-off QQQ | 51.65% | 56.07% | -41.68% | -58.04% | 1.239 | 1.038 | 2.749 | 90.69% | 2.471 | 138.750 | 19.000 | 2025-05 | -14.46% | 2025-04 | 5.62% |
| 2024_latest | MA200 risk-off cash | 45.46% | 56.07% | -37.37% | -58.04% | 1.216 | 0.970 | 2.485 | 90.69% | 2.471 | 138.750 | 19.000 | 2025-05 | -21.67% | 2025-03 | 6.54% |
| 2024_latest | MA150 risk-off QQQ | 46.24% | 56.07% | -40.42% | -58.04% | 1.144 | 0.977 | 2.517 | 88.07% | 6.588 | 59.889 | 9.125 | 2025-05 | -14.46% | 2025-03 | 6.64% |
| 2024_latest | MA250 risk-off QQQ | 49.32% | 56.07% | -47.27% | -58.04% | 1.043 | 0.990 | 2.648 | 93.79% | 4.118 | 95.667 | 7.600 | 2025-05 | -9.65% | 2025-04 | 5.62% |
| 2024_latest | MA200 risk-off QQQ with 5-day confirmation | 41.64% | 56.07% | -42.56% | -58.04% | 0.978 | 0.900 | 2.329 | 90.52% | 1.647 | 184.667 | 29.000 | 2026-04 | -22.88% | 2025-04 | 5.62% |

## Decision

MA regime is worth continued research only as a minimal, explainable risk-management baseline. It does not have paper-trading qualification yet.

## Next Research Questions

1. Does MA regime still beat simple blends after adding retained daily OOS equity and weight artifacts for each slice?
2. Is the 2024-latest re-entry lag acceptable versus a fixed 70/30 or 50/50 blend?
3. Does the same MA regime survive a volatility-matched benchmark and tax/slippage stress?
