# Benchmark Comparison

## Executive Verdict

Current strategy complexity is not yet justified.

The strict baseline strategy remains useful as a drawdown-control candidate, but the retained strict OOS evidence does not show that the complex strategy is clearly superior to fixed TQQQ/QQQ/cash blends or simple timing benchmarks across CAGR, Calmar, Sharpe_DailyExcess, and MaxDD.

Strategy has evidence of risk-management value, but not TQQQ-killer evidence.

## Data and Run Conditions

- Data source: public QQQ/TQQQ adjusted prices via the project yfinance loader.
- Download time: `2026-06-11T22:16:07+08:00`.
- Loaded price range: `2015-01-02` to `2026-06-10`.
- RF fallback: `2.0000%`, matching the existing strict audit summary rows.
- Strict strategy rows: reused from `reports/strict_audit/strict_audit_summary.csv`.
- QQQ5: disabled for the strict strategy; no simple benchmark uses QQQ5.
- TQQQ benchmark: raw adjusted price returns; no extra expense deduction.
- Effective leverage cap: strict strategy used `max_effective_leverage=3.0`; simple benchmarks are also capped at or below 3x nominal effective exposure.
- No parameter search: fixed blends, fixed 35% vol target with 20-day shifted realized vol, and fixed 50/200 MA crossover.

## Method

- Each benchmark is evaluated on the same strict OOS slices as the prior audit.
- Test-period equity is normalized to 1.0 at the first OOS date, matching the strict OOS reporting convention.
- Cash earns 0 in simple blend benchmarks.
- Vol-targeted TQQQ uses prior-day 20-day realized volatility and caps TQQQ weight at 100%, so effective exposure is never above 3x.
- MA crossover uses TQQQ 50/200 moving averages with the position shifted one day to avoid same-day signal leakage.

## Slice Summary

| slice | benchmark | CAGR | MaxDD | Calmar | Sharpe_DailyExcess | Final_Equity | AvgEffectiveLeverage | MaxEffectiveLeverage | Rank_Calmar_In_Slice | Rank_Sharpe_In_Slice | Rank_CAGR_In_Slice |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | TQQQ buy-and-hold | 106.73% | -69.92% | 1.526 | 1.343 | 8.861 | 3.000 | 3.000 | 3 | 3 | 1 |
| 2019_2021 | 80% TQQQ + 20% cash | 87.16% | -59.86% | 1.456 | 1.337 | 6.572 | 2.400 | 2.400 | 5 | 6 | 3 |
| 2019_2021 | 70% TQQQ + 30% QQQ | 87.96% | -59.78% | 1.471 | 1.342 | 6.658 | 2.400 | 2.400 | 4 | 4 | 2 |
| 2019_2021 | 50% TQQQ + 50% QQQ | 74.15% | -51.88% | 1.429 | 1.341 | 5.293 | 2.000 | 2.000 | 6 | 5 | 4 |
| 2019_2021 | Vol-targeted TQQQ 35% ann vol | 60.08% | -30.00% | 2.003 | 1.402 | 4.110 | 2.035 | 3.000 | 1 | 2 | 5 |
| 2019_2021 | MA crossover TQQQ 50/200 | 50.95% | -69.92% | 0.729 | 0.924 | 3.445 | 2.517 | 3.000 | 8 | 8 | 6 |
| 2022_2023 | TQQQ buy-and-hold | -22.29% | -81.02% | -0.275 | 0.039 | 0.606 | 3.000 | 3.000 | 7 | 6 | 8 |
| 2022_2023 | 80% TQQQ + 20% cash | -14.18% | -71.50% | -0.198 | 0.033 | 0.738 | 2.400 | 2.400 | 6 | 7 | 7 |
| 2022_2023 | 70% TQQQ + 30% QQQ | -13.33% | -71.30% | -0.187 | 0.049 | 0.752 | 2.400 | 2.400 | 5 | 5 | 6 |
| 2022_2023 | 50% TQQQ + 50% QQQ | -8.05% | -62.96% | -0.128 | 0.059 | 0.846 | 2.000 | 2.000 | 4 | 4 | 4 |
| 2022_2023 | Vol-targeted TQQQ 35% ann vol | 7.82% | -45.26% | 0.173 | 0.334 | 1.161 | 1.586 | 3.000 | 2 | 1 | 1 |
| 2022_2023 | MA crossover TQQQ 50/200 | 5.11% | -47.24% | 0.108 | 0.272 | 1.104 | 1.329 | 3.000 | 3 | 3 | 3 |
| 2024_latest | TQQQ buy-and-hold | 56.07% | -58.04% | 0.966 | 0.998 | 2.948 | 3.000 | 3.000 | 3 | 3 | 1 |
| 2024_latest | 80% TQQQ + 20% cash | 47.11% | -49.24% | 0.957 | 0.990 | 2.554 | 2.400 | 2.400 | 4 | 4 | 3 |
| 2024_latest | 70% TQQQ + 30% QQQ | 48.83% | -49.05% | 0.996 | 1.013 | 2.627 | 2.400 | 2.400 | 2 | 2 | 2 |
| 2024_latest | 50% TQQQ + 50% QQQ | 43.00% | -42.29% | 1.017 | 1.028 | 2.384 | 2.000 | 2.000 | 1 | 1 | 4 |
| 2024_latest | Vol-targeted TQQQ 35% ann vol | 33.38% | -38.32% | 0.871 | 0.892 | 2.013 | 2.103 | 3.000 | 5 | 5 | 5 |
| 2024_latest | MA crossover TQQQ 50/200 | 3.15% | -51.33% | 0.061 | 0.281 | 1.078 | 2.544 | 3.000 | 8 | 8 | 8 |
| 2019_2021 | Current baseline strict strategy | 35.28% | -22.17% | 1.591 | 1.449 | 2.479 | 1.131 | 1.716 | 2 | 1 | 7 |
| 2022_2023 | Current baseline strict strategy | 6.70% | -33.14% | 0.202 | 0.304 | 1.138 | 1.043 | 1.650 | 1 | 2 | 2 |
| 2024_latest | Current baseline strict strategy | 17.89% | -22.11% | 0.809 | 0.806 | 1.491 | 1.151 | 1.560 | 6 | 6 | 6 |
| 2019_2021 | Current DL strict strategy | 20.93% | -22.40% | 0.934 | 1.068 | 1.770 | 0.651 | 1.792 | 7 | 7 | 8 |
| 2022_2023 | Current DL strict strategy | -12.57% | -41.00% | -0.307 | -0.681 | 0.766 | 0.687 | 2.303 | 8 | 8 | 5 |
| 2024_latest | Current DL strict strategy | 16.15% | -29.26% | 0.552 | 0.709 | 1.438 | 1.007 | 2.485 | 7 | 7 | 7 |

## Complex Strategy vs Simple Benchmarks

### 2019_2021

- Baseline strict: CAGR 35.28%, MaxDD -22.17%, Calmar 1.591, Sharpe 1.449, avg exposure 1.131x.
- DL strict: CAGR 20.93%, MaxDD -22.40%, Calmar 0.934, Sharpe 1.068, avg exposure 0.651x.
- Best simple Calmar: `Vol-targeted TQQQ 35% ann vol` with Calmar 2.003, CAGR 60.08%, MaxDD -30.00%.
- Best simple Sharpe: `Vol-targeted TQQQ 35% ann vol` with Sharpe 1.402.
- Best simple CAGR: `TQQQ buy-and-hold` with CAGR 106.73%.

### 2022_2023

- Baseline strict: CAGR 6.70%, MaxDD -33.14%, Calmar 0.202, Sharpe 0.304, avg exposure 1.043x.
- DL strict: CAGR -12.57%, MaxDD -41.00%, Calmar -0.307, Sharpe -0.681, avg exposure 0.687x.
- Best simple Calmar: `Vol-targeted TQQQ 35% ann vol` with Calmar 0.173, CAGR 7.82%, MaxDD -45.26%.
- Best simple Sharpe: `Vol-targeted TQQQ 35% ann vol` with Sharpe 0.334.
- Best simple CAGR: `Vol-targeted TQQQ 35% ann vol` with CAGR 7.82%.

### 2024_latest

- Baseline strict: CAGR 17.89%, MaxDD -22.11%, Calmar 0.809, Sharpe 0.806, avg exposure 1.151x.
- DL strict: CAGR 16.15%, MaxDD -29.26%, Calmar 0.552, Sharpe 0.709, avg exposure 1.007x.
- Best simple Calmar: `50% TQQQ + 50% QQQ` with Calmar 1.017, CAGR 43.00%, MaxDD -42.29%.
- Best simple Sharpe: `50% TQQQ + 50% QQQ` with Sharpe 1.028.
- Best simple CAGR: `TQQQ buy-and-hold` with CAGR 56.07%.

## Interpretation

The strict baseline strategy often reduced drawdown versus TQQQ, but simple lower-beta blends also reduce drawdown with far less model and state-machine complexity. The baseline strategy only has a clear slice-level advantage in the 2022-2023 bear/recovery period. In strong bull slices, the fixed blends and raw TQQQ generally explain most of the performance gap.

The DL strict strategy does not show stable incremental value over the baseline or simple benchmarks. The prior strict audit also flagged the first DL OOS slice as having rule-fallback limitations, so it should not be cited as evidence of DL alpha.

## Answer to Core Question

Current complex strategy is not yet proven superior to simple TQQQ/QQQ/cash risk-reduction benchmarks. It should be treated as a risk-management research candidate, not as a validated TQQQ replacement or TQQQ killer.

## Next Research

- Preserve this benchmark comparison as a required hurdle for future strategy changes.
- Add daily strict OOS equity/weights/notes artifacts so future benchmark attribution can measure capture, re-risking speed, cooldown drag, and turnover directly.
- Compare against volatility-matched static blends, not only raw TQQQ.
- Run ablations that remove DL, Bandit, risk gate, and Kelly components one at a time under the same strict slices.
- Add tax, slippage, and execution stress before any paper-trading claim.

