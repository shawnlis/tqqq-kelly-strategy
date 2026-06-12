# Minimal TQQQ Risk-Management Baseline Report

## Executive Verdict

Partial evidence only; further research required.

Decision: HOLD

This is not a TQQQ killer result and must not be presented as paper-trading approval.
The current complex strategy remains NO-GO for paper trading.

## Scope

- Uses only QQQ, TQQQ, and cash.
- Uses raw adjusted price returns with no extra TQQQ expense deduction.
- Does not use DL, Bandit, Kelly, QQQ5, synthetic products, options, or futures.
- All MA, volatility, and drawdown signals are shifted one day before affecting returns.
- Effective leverage is capped by construction at <= 3x.
- Strict OOS slices match the prior audit where the reference files are available.

## Best Simple Strategy

Best composite strategy: `MA200 QQQ regime, risk-off QQQ`.

| slice | CAGR | MaxDD | Calmar | Sharpe_DailyExcess | AvgEffectiveLeverage | MaxEffectiveLeverage |
| --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | 93.18% | -58.67% | 1.588 | 1.362 | 2.857 | 3.000 |
| 2022_2023 | 3.56% | -56.40% | 0.063 | 0.260 | 1.994 | 3.000 |
| 2024_latest | 51.65% | -41.68% | 1.239 | 1.038 | 2.814 | 3.000 |

The best strategy is selected by a composite rank across Calmar, Sharpe_DailyExcess, MaxDD, and CAGR. It is not selected by CAGR alone.

## Comparison Against Current Complex Strategy

Best minimal strategy `MA200 QQQ regime, risk-off QQQ` beat current baseline strict strategy in 2/3 CAGR slices, 1/3 Calmar slices, 1/3 Sharpe slices, and 0/3 MaxDD slices.

The DL strict strategy remains unsupported: prior strict audit rows showed DL below baseline in all retained CAGR slices, with an initial fallback limitation.
Synthetic or hybrid QQQ5 is not used and is not part of this conclusion.

## Full Minimal Suite

| slice | strategy | family | CAGR | MaxDD | Calmar | Sharpe_DailyExcess | Final_Equity | AvgEffectiveLeverage | MaxEffectiveLeverage | Rank_Calmar_In_Slice | Rank_Sharpe_In_Slice | Rank_CAGR_In_Slice |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | Static 100% TQQQ | static | 106.73% | -69.92% | 1.526 | 1.343 | 8.861 | 3.000 | 3.000 | 3 | 3 | 1 |
| 2019_2021 | Static 80% TQQQ + 20% cash | static | 87.16% | -59.86% | 1.456 | 1.337 | 6.572 | 2.400 | 2.400 | 5 | 7 | 4 |
| 2019_2021 | Static 70% TQQQ + 30% QQQ | static | 87.96% | -59.78% | 1.471 | 1.342 | 6.658 | 2.400 | 2.400 | 4 | 4 | 3 |
| 2019_2021 | Static 50% TQQQ + 50% QQQ | static | 74.15% | -51.88% | 1.429 | 1.341 | 5.293 | 2.000 | 2.000 | 7 | 5 | 7 |
| 2019_2021 | Static 30% TQQQ + 70% QQQ | static | 59.73% | -43.03% | 1.388 | 1.339 | 4.083 | 1.600 | 1.600 | 8 | 6 | 9 |
| 2019_2021 | MA200 QQQ regime, risk-off cash | ma_regime | 78.80% | -54.81% | 1.438 | 1.254 | 5.730 | 2.786 | 3.000 | 6 | 9 | 6 |
| 2019_2021 | MA200 QQQ regime, risk-off QQQ | ma_regime | 93.18% | -58.67% | 1.588 | 1.362 | 7.228 | 2.857 | 3.000 | 2 | 2 | 2 |
| 2019_2021 | Vol target TQQQ 35%, 20d | vol_target | 60.08% | -30.00% | 2.003 | 1.402 | 4.110 | 2.035 | 3.000 | 1 | 1 | 8 |
| 2019_2021 | Vol target TQQQ 35%, 60d | vol_target | 48.73% | -42.23% | 1.154 | 1.193 | 3.295 | 1.870 | 3.000 | 10 | 10 | 10 |
| 2019_2021 | QQQ drawdown control 20%, risk-off QQQ | drawdown_control | 85.23% | -63.80% | 1.336 | 1.273 | 6.371 | 2.881 | 3.000 | 9 | 8 | 5 |
| 2022_2023 | Static 100% TQQQ | static | -22.29% | -81.02% | -0.275 | 0.039 | 0.606 | 3.000 | 3.000 | 9 | 8 | 9 |
| 2022_2023 | Static 80% TQQQ + 20% cash | static | -14.18% | -71.50% | -0.198 | 0.033 | 0.738 | 2.400 | 2.400 | 8 | 9 | 8 |
| 2022_2023 | Static 70% TQQQ + 30% QQQ | static | -13.33% | -71.30% | -0.187 | 0.049 | 0.752 | 2.400 | 2.400 | 7 | 7 | 7 |
| 2022_2023 | Static 50% TQQQ + 50% QQQ | static | -8.05% | -62.96% | -0.128 | 0.059 | 0.846 | 2.000 | 2.000 | 6 | 6 | 6 |
| 2022_2023 | Static 30% TQQQ + 70% QQQ | static | -3.50% | -52.98% | -0.066 | 0.074 | 0.932 | 1.600 | 1.600 | 5 | 5 | 5 |
| 2022_2023 | MA200 QQQ regime, risk-off cash | ma_regime | 8.37% | -51.46% | 0.163 | 0.351 | 1.173 | 1.491 | 3.000 | 2 | 1 | 1 |
| 2022_2023 | MA200 QQQ regime, risk-off QQQ | ma_regime | 3.56% | -56.40% | 0.063 | 0.260 | 1.072 | 1.994 | 3.000 | 3 | 3 | 3 |
| 2022_2023 | Vol target TQQQ 35%, 20d | vol_target | 7.82% | -45.26% | 0.173 | 0.334 | 1.161 | 1.586 | 3.000 | 1 | 2 | 2 |
| 2022_2023 | Vol target TQQQ 35%, 60d | vol_target | 1.73% | -46.86% | 0.037 | 0.168 | 1.035 | 1.519 | 2.293 | 4 | 4 | 4 |
| 2022_2023 | QQQ drawdown control 20%, risk-off QQQ | drawdown_control | -27.76% | -71.69% | -0.387 | -0.486 | 0.524 | 1.830 | 3.000 | 10 | 10 | 10 |
| 2024_latest | Static 100% TQQQ | static | 56.07% | -58.04% | 0.966 | 0.998 | 2.948 | 3.000 | 3.000 | 6 | 5 | 1 |
| 2024_latest | Static 80% TQQQ + 20% cash | static | 47.11% | -49.24% | 0.957 | 0.990 | 2.554 | 2.400 | 2.400 | 7 | 6 | 4 |
| 2024_latest | Static 70% TQQQ + 30% QQQ | static | 48.83% | -49.05% | 0.996 | 1.013 | 2.627 | 2.400 | 2.400 | 5 | 4 | 3 |
| 2024_latest | Static 50% TQQQ + 50% QQQ | static | 43.00% | -42.29% | 1.017 | 1.028 | 2.384 | 2.000 | 2.000 | 4 | 3 | 6 |
| 2024_latest | Static 30% TQQQ + 70% QQQ | static | 36.50% | -34.87% | 1.047 | 1.050 | 2.129 | 1.600 | 1.600 | 3 | 1 | 8 |
| 2024_latest | MA200 QQQ regime, risk-off cash | ma_regime | 45.46% | -37.37% | 1.216 | 0.970 | 2.485 | 2.721 | 3.000 | 2 | 7 | 5 |
| 2024_latest | MA200 QQQ regime, risk-off QQQ | ma_regime | 51.65% | -41.68% | 1.239 | 1.038 | 2.749 | 2.814 | 3.000 | 1 | 2 | 2 |
| 2024_latest | Vol target TQQQ 35%, 20d | vol_target | 33.38% | -38.32% | 0.871 | 0.892 | 2.013 | 2.103 | 3.000 | 8 | 8 | 9 |
| 2024_latest | Vol target TQQQ 35%, 60d | vol_target | 29.18% | -38.11% | 0.766 | 0.806 | 1.862 | 2.011 | 3.000 | 9 | 10 | 10 |
| 2024_latest | QQQ drawdown control 20%, risk-off QQQ | drawdown_control | 42.35% | -56.37% | 0.751 | 0.882 | 2.357 | 2.938 | 3.000 | 10 | 9 | 7 |

## Decision

HOLD

GO FOR FURTHER RESEARCH means the simple baseline is worth studying under stricter daily artifact capture. It does not mean paper trading approval.
HOLD means keep the result as a benchmark but do not allocate further implementation effort until a clearer hurdle is met.
KILL would mean no simple baseline showed useful strict OOS evidence.

## Next Research Controls

1. Freeze this minimal suite as the benchmark hurdle for future complex modules.
2. Add daily OOS equity, weights, notes, and trades for attribution before changing any rules.
3. Compare any future simplified candidate against this suite and the current complex strategy before considering paper trading.

CSV output: `reports/minimal_baseline/minimal_tqqq_summary.csv`
