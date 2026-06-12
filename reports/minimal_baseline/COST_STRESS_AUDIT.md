# Cost Stress Audit for MA150 Risk-Off QQQ

## Executive Verdict

MA150 remains a research candidate under moderate costs, but cost/delay stress is mixed and it is still NOT READY FOR PAPER TRADING.

Current decision: HOLD. Paper-trading status: NOT READY FOR PAPER TRADING.

## Execution Assumptions

- Costs are one-way transaction costs applied only to changes in TQQQ and QQQ weights.
- Costs are not applied to buy-and-hold daily returns and do not double-count TQQQ fund expense.
- Static blends are buy-and-hold; no periodic rebalance is modeled, so they only incur initial OOS allocation cost.
- One-day execution delay shifts already-lagged strategy weights by one more trading day; day t close signal can affect returns no earlier than day t+2.

## MA150 Base Case

| slice | CAGR | MaxDD | Calmar | Final_Equity | total_turnover | trade_days |
| --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | 92.28% | -58.67% | 1.573 | 7.127 | 19.000 | 10.000 |
| 2022_2023 | 12.02% | -50.92% | 0.236 | 1.253 | 25.000 | 13.000 |
| 2024_latest | 46.24% | -40.42% | 1.144 | 2.517 | 33.000 | 17.000 |

## Cost Hurdles

cost_10bps_delay0d versus 70/30 TQQQ/QQQ: CAGR wins 2/3, Calmar wins 3/3, MaxDD wins 3/3, Final equity wins 2/3.
cost_10bps_delay0d versus 50/50 TQQQ/QQQ: CAGR wins 3/3, Calmar wins 3/3, MaxDD wins 2/3, Final equity wins 3/3.
cost_25bps_delay0d versus 70/30 TQQQ/QQQ: CAGR wins 2/3, Calmar wins 3/3, MaxDD wins 3/3, Final equity wins 2/3.
cost_25bps_delay0d versus 50/50 TQQQ/QQQ: CAGR wins 2/3, Calmar wins 2/3, MaxDD wins 2/3, Final equity wins 2/3.
cost_50bps_delay0d versus 70/30 TQQQ/QQQ: CAGR wins 1/3, Calmar wins 1/3, MaxDD wins 2/3, Final equity wins 1/3.
cost_50bps_delay0d versus 50/50 TQQQ/QQQ: CAGR wins 2/3, Calmar wins 2/3, MaxDD wins 1/3, Final equity wins 2/3.

## Execution Delay Stress

10 bps + one-day delay: final equity worsened in 2/3 slices; mean CAGR delta -2.99%, mean Calmar delta -0.012, mean final-equity delta -0.182.
25 bps + one-day delay: final equity worsened in 2/3 slices; mean CAGR delta -2.96%, mean Calmar delta -0.013, mean final-equity delta -0.179.

## Turnover Attribution

MA turnover comes from regime switches plus the initial OOS allocation. Static blends are modeled as buy-and-hold with no periodic rebalance, so they only incur initial allocation cost.

### MA150 Turnover By Scenario

| scenario | MeanAnnualizedTurnover | MeanTradeDays | MeanCostDrag |
| --- | --- | --- | --- |
| base_0bps | 10.829 | 13.333 | 0.00% |
| cost_10bps_delay0d | 10.829 | 13.333 | 8.23% |
| cost_25bps_delay0d | 10.829 | 13.333 | 20.24% |
| cost_50bps_delay0d | 10.829 | 13.333 | 39.38% |
| cost_5bps_delay0d | 10.829 | 13.333 | 4.14% |
| stress_10bps_delay1d | 10.829 | 13.333 | 7.97% |
| stress_25bps_delay1d | 10.829 | 13.333 | 19.59% |

### Static Strategy Turnover

| strategy | MeanTradeDays | MeanAnnualizedTurnover |
| --- | --- | --- |
| 50/50 TQQQ/QQQ | 1.000 | 0.416 |
| 70/30 TQQQ/QQQ | 1.000 | 0.416 |
| TQQQ buy-and-hold | 1.000 | 0.416 |

## Interpretation

- If MA150 loses to simple blends after moderate costs, its research value should be reduced.
- If one-day execution delay materially worsens results, implementation timing risk is a blocker.
- Cost drag is mainly a function of switch frequency and whether the strategy moves between TQQQ and QQQ or cash.
- This audit is not paper-trading approval; it is a hurdle check for continued research.

## Full Summary

| slice | strategy | scenario | cost_bps | execution_delay_days | CAGR | MaxDD | Calmar | Final_Equity | total_turnover | annualized_turnover | trade_days | final_cost_drag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | TQQQ buy-and-hold | base_0bps | 0.0 | 0 | 106.73% | -69.92% | 1.526 | 8.861 | 1.000 | 0.333 | 1.000 | 0.00% |
| 2019_2021 | TQQQ buy-and-hold | cost_5bps_delay0d | 5.0 | 0 | 106.73% | -69.92% | 1.526 | 8.856 | 1.000 | 0.333 | 1.000 | 0.44% |
| 2019_2021 | TQQQ buy-and-hold | cost_10bps_delay0d | 10.0 | 0 | 106.73% | -69.92% | 1.526 | 8.852 | 1.000 | 0.333 | 1.000 | 0.89% |
| 2019_2021 | TQQQ buy-and-hold | cost_25bps_delay0d | 25.0 | 0 | 106.73% | -69.92% | 1.526 | 8.839 | 1.000 | 0.333 | 1.000 | 2.22% |
| 2019_2021 | TQQQ buy-and-hold | cost_50bps_delay0d | 50.0 | 0 | 106.73% | -69.92% | 1.526 | 8.816 | 1.000 | 0.333 | 1.000 | 4.43% |
| 2019_2021 | TQQQ buy-and-hold | stress_10bps_delay1d | 10.0 | 1 | 106.73% | -69.92% | 1.526 | 8.852 | 1.000 | 0.333 | 1.000 | 0.89% |
| 2019_2021 | TQQQ buy-and-hold | stress_25bps_delay1d | 25.0 | 1 | 106.73% | -69.92% | 1.526 | 8.839 | 1.000 | 0.333 | 1.000 | 2.22% |
| 2019_2021 | 70/30 TQQQ/QQQ | base_0bps | 0.0 | 0 | 87.96% | -59.78% | 1.471 | 6.658 | 1.000 | 0.333 | 1.000 | 0.00% |
| 2019_2021 | 70/30 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | 87.96% | -59.78% | 1.471 | 6.654 | 1.000 | 0.333 | 1.000 | 0.33% |
| 2019_2021 | 70/30 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | 87.96% | -59.78% | 1.471 | 6.651 | 1.000 | 0.333 | 1.000 | 0.67% |
| 2019_2021 | 70/30 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | 87.96% | -59.78% | 1.471 | 6.641 | 1.000 | 0.333 | 1.000 | 1.66% |
| 2019_2021 | 70/30 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | 87.96% | -59.78% | 1.471 | 6.624 | 1.000 | 0.333 | 1.000 | 3.33% |
| 2019_2021 | 70/30 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | 87.96% | -59.78% | 1.471 | 6.651 | 1.000 | 0.333 | 1.000 | 0.67% |
| 2019_2021 | 70/30 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | 87.96% | -59.78% | 1.471 | 6.641 | 1.000 | 0.333 | 1.000 | 1.66% |
| 2019_2021 | 50/50 TQQQ/QQQ | base_0bps | 0.0 | 0 | 74.15% | -51.88% | 1.429 | 5.293 | 1.000 | 0.333 | 1.000 | 0.00% |
| 2019_2021 | 50/50 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | 74.15% | -51.88% | 1.429 | 5.290 | 1.000 | 0.333 | 1.000 | 0.26% |
| 2019_2021 | 50/50 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | 74.15% | -51.88% | 1.429 | 5.288 | 1.000 | 0.333 | 1.000 | 0.53% |
| 2019_2021 | 50/50 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | 74.15% | -51.88% | 1.429 | 5.280 | 1.000 | 0.333 | 1.000 | 1.32% |
| 2019_2021 | 50/50 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | 74.15% | -51.88% | 1.429 | 5.267 | 1.000 | 0.333 | 1.000 | 2.65% |
| 2019_2021 | 50/50 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | 74.15% | -51.88% | 1.429 | 5.288 | 1.000 | 0.333 | 1.000 | 0.53% |
| 2019_2021 | 50/50 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | 74.15% | -51.88% | 1.429 | 5.280 | 1.000 | 0.333 | 1.000 | 1.32% |
| 2019_2021 | MA150 risk-off QQQ | base_0bps | 0.0 | 0 | 92.28% | -58.67% | 1.573 | 7.127 | 19.000 | 6.325 | 10.000 | 0.00% |
| 2019_2021 | MA150 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 91.70% | -58.80% | 1.560 | 7.060 | 19.000 | 6.325 | 10.000 | 6.74% |
| 2019_2021 | MA150 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 91.13% | -58.92% | 1.547 | 6.993 | 19.000 | 6.325 | 10.000 | 13.43% |
| 2019_2021 | MA150 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | 89.41% | -59.29% | 1.508 | 6.796 | 19.000 | 6.325 | 10.000 | 33.14% |
| 2019_2021 | MA150 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | 86.58% | -59.90% | 1.445 | 6.478 | 19.000 | 6.325 | 10.000 | 64.89% |
| 2019_2021 | MA150 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | 85.36% | -60.51% | 1.411 | 6.377 | 19.000 | 6.325 | 10.000 | 12.24% |
| 2019_2021 | MA150 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | 83.69% | -60.86% | 1.375 | 6.198 | 19.000 | 6.325 | 10.000 | 30.22% |
| 2019_2021 | MA200 risk-off QQQ | base_0bps | 0.0 | 0 | 93.18% | -58.67% | 1.588 | 7.228 | 23.000 | 7.657 | 12.000 | 0.00% |
| 2019_2021 | MA200 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 92.47% | -58.80% | 1.573 | 7.145 | 23.000 | 7.657 | 12.000 | 8.27% |
| 2019_2021 | MA200 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 91.77% | -58.92% | 1.557 | 7.063 | 23.000 | 7.657 | 12.000 | 16.45% |
| 2019_2021 | MA200 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | 89.66% | -59.29% | 1.512 | 6.823 | 23.000 | 7.657 | 12.000 | 40.48% |
| 2019_2021 | MA200 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | 86.20% | -59.90% | 1.439 | 6.439 | 23.000 | 7.657 | 12.000 | 78.88% |
| 2019_2021 | MA200 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | 88.50% | -60.51% | 1.463 | 6.708 | 23.000 | 7.657 | 12.000 | 15.62% |
| 2019_2021 | MA200 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | 86.44% | -60.86% | 1.420 | 6.480 | 23.000 | 7.657 | 12.000 | 38.45% |
| 2019_2021 | MA150 risk-off cash | base_0bps | 0.0 | 0 | 77.66% | -54.81% | 1.417 | 5.620 | 9.000 | 2.996 | 9.000 | 0.00% |
| 2019_2021 | MA150 risk-off cash | cost_5bps_delay0d | 5.0 | 0 | 77.39% | -54.87% | 1.410 | 5.595 | 9.000 | 2.996 | 9.000 | 2.52% |
| 2019_2021 | MA150 risk-off cash | cost_10bps_delay0d | 10.0 | 0 | 77.13% | -54.94% | 1.404 | 5.570 | 9.000 | 2.996 | 9.000 | 5.04% |
| 2019_2021 | MA150 risk-off cash | cost_25bps_delay0d | 25.0 | 0 | 76.33% | -55.14% | 1.384 | 5.495 | 9.000 | 2.996 | 9.000 | 12.52% |
| 2019_2021 | MA150 risk-off cash | cost_50bps_delay0d | 50.0 | 0 | 75.01% | -55.48% | 1.352 | 5.372 | 9.000 | 2.996 | 9.000 | 24.79% |
| 2019_2021 | MA150 risk-off cash | stress_10bps_delay1d | 10.0 | 1 | 70.27% | -61.06% | 1.151 | 4.947 | 9.000 | 2.996 | 9.000 | 4.47% |
| 2019_2021 | MA150 risk-off cash | stress_25bps_delay1d | 25.0 | 1 | 69.50% | -61.29% | 1.134 | 4.880 | 9.000 | 2.996 | 9.000 | 11.12% |
| 2022_2023 | TQQQ buy-and-hold | base_0bps | 0.0 | 0 | -22.29% | -81.02% | -0.275 | 0.606 | 1.000 | 0.503 | 1.000 | 0.00% |
| 2022_2023 | TQQQ buy-and-hold | cost_5bps_delay0d | 5.0 | 0 | -22.29% | -81.02% | -0.275 | 0.605 | 1.000 | 0.503 | 1.000 | 0.03% |
| 2022_2023 | TQQQ buy-and-hold | cost_10bps_delay0d | 10.0 | 0 | -22.29% | -81.02% | -0.275 | 0.605 | 1.000 | 0.503 | 1.000 | 0.06% |
| 2022_2023 | TQQQ buy-and-hold | cost_25bps_delay0d | 25.0 | 0 | -22.29% | -81.02% | -0.275 | 0.604 | 1.000 | 0.503 | 1.000 | 0.15% |
| 2022_2023 | TQQQ buy-and-hold | cost_50bps_delay0d | 50.0 | 0 | -22.29% | -81.02% | -0.275 | 0.603 | 1.000 | 0.503 | 1.000 | 0.30% |
| 2022_2023 | TQQQ buy-and-hold | stress_10bps_delay1d | 10.0 | 1 | -22.29% | -81.02% | -0.275 | 0.605 | 1.000 | 0.503 | 1.000 | 0.06% |
| 2022_2023 | TQQQ buy-and-hold | stress_25bps_delay1d | 25.0 | 1 | -22.29% | -81.02% | -0.275 | 0.604 | 1.000 | 0.503 | 1.000 | 0.15% |
| 2022_2023 | 70/30 TQQQ/QQQ | base_0bps | 0.0 | 0 | -13.33% | -71.30% | -0.187 | 0.752 | 1.000 | 0.503 | 1.000 | 0.00% |
| 2022_2023 | 70/30 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | -13.33% | -71.30% | -0.187 | 0.752 | 1.000 | 0.503 | 1.000 | 0.04% |
| 2022_2023 | 70/30 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | -13.33% | -71.30% | -0.187 | 0.752 | 1.000 | 0.503 | 1.000 | 0.08% |
| 2022_2023 | 70/30 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | -13.33% | -71.30% | -0.187 | 0.750 | 1.000 | 0.503 | 1.000 | 0.19% |
| 2022_2023 | 70/30 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | -13.33% | -71.30% | -0.187 | 0.749 | 1.000 | 0.503 | 1.000 | 0.38% |
| 2022_2023 | 70/30 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | -13.33% | -71.30% | -0.187 | 0.752 | 1.000 | 0.503 | 1.000 | 0.08% |
| 2022_2023 | 70/30 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | -13.33% | -71.30% | -0.187 | 0.750 | 1.000 | 0.503 | 1.000 | 0.19% |
| 2022_2023 | 50/50 TQQQ/QQQ | base_0bps | 0.0 | 0 | -8.05% | -62.96% | -0.128 | 0.846 | 1.000 | 0.503 | 1.000 | 0.00% |
| 2022_2023 | 50/50 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | -8.05% | -62.96% | -0.128 | 0.846 | 1.000 | 0.503 | 1.000 | 0.04% |
| 2022_2023 | 50/50 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | -8.05% | -62.96% | -0.128 | 0.845 | 1.000 | 0.503 | 1.000 | 0.08% |
| 2022_2023 | 50/50 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | -8.05% | -62.96% | -0.128 | 0.844 | 1.000 | 0.503 | 1.000 | 0.21% |
| 2022_2023 | 50/50 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | -8.05% | -62.96% | -0.128 | 0.842 | 1.000 | 0.503 | 1.000 | 0.42% |
| 2022_2023 | 50/50 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | -8.05% | -62.96% | -0.128 | 0.845 | 1.000 | 0.503 | 1.000 | 0.08% |
| 2022_2023 | 50/50 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | -8.05% | -62.96% | -0.128 | 0.844 | 1.000 | 0.503 | 1.000 | 0.21% |
| 2022_2023 | MA150 risk-off QQQ | base_0bps | 0.0 | 0 | 12.02% | -50.92% | 0.236 | 1.253 | 25.000 | 12.575 | 13.000 | 0.00% |
| 2022_2023 | MA150 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 11.34% | -51.36% | 0.221 | 1.238 | 25.000 | 12.575 | 13.000 | 1.56% |
| 2022_2023 | MA150 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 10.67% | -51.80% | 0.206 | 1.222 | 25.000 | 12.575 | 13.000 | 3.10% |
| 2022_2023 | MA150 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | 8.68% | -53.09% | 0.164 | 1.177 | 25.000 | 12.575 | 13.000 | 7.61% |
| 2022_2023 | MA150 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | 5.43% | -55.17% | 0.098 | 1.105 | 25.000 | 12.575 | 13.000 | 14.79% |
| 2022_2023 | MA150 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | 1.10% | -56.79% | 0.019 | 1.021 | 25.000 | 12.575 | 13.000 | 2.59% |
| 2022_2023 | MA150 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | -0.72% | -57.95% | -0.012 | 0.983 | 25.000 | 12.575 | 13.000 | 6.36% |
| 2022_2023 | MA200 risk-off QQQ | base_0bps | 0.0 | 0 | 3.56% | -56.40% | 0.063 | 1.072 | 29.000 | 14.587 | 15.000 | 0.00% |
| 2022_2023 | MA200 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 2.84% | -56.79% | 0.050 | 1.057 | 29.000 | 14.587 | 15.000 | 1.54% |
| 2022_2023 | MA200 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 2.11% | -57.18% | 0.037 | 1.041 | 29.000 | 14.587 | 15.000 | 3.07% |
| 2022_2023 | MA200 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | -0.03% | -58.55% | -0.000 | 0.997 | 29.000 | 14.587 | 15.000 | 7.52% |
| 2022_2023 | MA200 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | -3.51% | -61.08% | -0.058 | 0.927 | 29.000 | 14.587 | 15.000 | 14.54% |
| 2022_2023 | MA200 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | -5.08% | -61.73% | -0.082 | 0.901 | 29.000 | 14.587 | 15.000 | 2.65% |
| 2022_2023 | MA200 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | -7.07% | -63.09% | -0.112 | 0.862 | 29.000 | 14.587 | 15.000 | 6.50% |
| 2022_2023 | MA150 risk-off cash | base_0bps | 0.0 | 0 | 21.60% | -37.17% | 0.581 | 1.475 | 13.000 | 6.539 | 13.000 | 0.00% |
| 2022_2023 | MA150 risk-off cash | cost_5bps_delay0d | 5.0 | 0 | 21.23% | -37.48% | 0.566 | 1.466 | 13.000 | 6.539 | 13.000 | 0.96% |
| 2022_2023 | MA150 risk-off cash | cost_10bps_delay0d | 10.0 | 0 | 20.86% | -37.79% | 0.552 | 1.456 | 13.000 | 6.539 | 13.000 | 1.91% |
| 2022_2023 | MA150 risk-off cash | cost_25bps_delay0d | 25.0 | 0 | 19.77% | -38.72% | 0.511 | 1.428 | 13.000 | 6.539 | 13.000 | 4.72% |
| 2022_2023 | MA150 risk-off cash | cost_50bps_delay0d | 50.0 | 0 | 17.97% | -40.24% | 0.447 | 1.382 | 13.000 | 6.539 | 13.000 | 9.31% |
| 2022_2023 | MA150 risk-off cash | stress_10bps_delay1d | 10.0 | 1 | 6.08% | -46.33% | 0.131 | 1.123 | 13.000 | 6.539 | 13.000 | 1.47% |
| 2022_2023 | MA150 risk-off cash | stress_25bps_delay1d | 25.0 | 1 | 5.12% | -47.13% | 0.109 | 1.102 | 13.000 | 6.539 | 13.000 | 3.64% |
| 2024_latest | TQQQ buy-and-hold | base_0bps | 0.0 | 0 | 56.07% | -58.04% | 0.966 | 2.948 | 1.000 | 0.412 | 1.000 | 0.00% |
| 2024_latest | TQQQ buy-and-hold | cost_5bps_delay0d | 5.0 | 0 | 56.07% | -58.04% | 0.966 | 2.947 | 1.000 | 0.412 | 1.000 | 0.15% |
| 2024_latest | TQQQ buy-and-hold | cost_10bps_delay0d | 10.0 | 0 | 56.07% | -58.04% | 0.966 | 2.945 | 1.000 | 0.412 | 1.000 | 0.29% |
| 2024_latest | TQQQ buy-and-hold | cost_25bps_delay0d | 25.0 | 0 | 56.07% | -58.04% | 0.966 | 2.941 | 1.000 | 0.412 | 1.000 | 0.74% |
| 2024_latest | TQQQ buy-and-hold | cost_50bps_delay0d | 50.0 | 0 | 56.07% | -58.04% | 0.966 | 2.933 | 1.000 | 0.412 | 1.000 | 1.47% |
| 2024_latest | TQQQ buy-and-hold | stress_10bps_delay1d | 10.0 | 1 | 56.07% | -58.04% | 0.966 | 2.945 | 1.000 | 0.412 | 1.000 | 0.29% |
| 2024_latest | TQQQ buy-and-hold | stress_25bps_delay1d | 25.0 | 1 | 56.07% | -58.04% | 0.966 | 2.941 | 1.000 | 0.412 | 1.000 | 0.74% |
| 2024_latest | 70/30 TQQQ/QQQ | base_0bps | 0.0 | 0 | 48.83% | -49.05% | 0.996 | 2.627 | 1.000 | 0.412 | 1.000 | 0.00% |
| 2024_latest | 70/30 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | 48.83% | -49.05% | 0.996 | 2.625 | 1.000 | 0.412 | 1.000 | 0.13% |
| 2024_latest | 70/30 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | 48.83% | -49.05% | 0.996 | 2.624 | 1.000 | 0.412 | 1.000 | 0.26% |
| 2024_latest | 70/30 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | 48.83% | -49.05% | 0.996 | 2.620 | 1.000 | 0.412 | 1.000 | 0.66% |
| 2024_latest | 70/30 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | 48.83% | -49.05% | 0.996 | 2.613 | 1.000 | 0.412 | 1.000 | 1.31% |
| 2024_latest | 70/30 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | 48.83% | -49.05% | 0.996 | 2.624 | 1.000 | 0.412 | 1.000 | 0.26% |
| 2024_latest | 70/30 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | 48.83% | -49.05% | 0.996 | 2.620 | 1.000 | 0.412 | 1.000 | 0.66% |
| 2024_latest | 50/50 TQQQ/QQQ | base_0bps | 0.0 | 0 | 43.00% | -42.29% | 1.017 | 2.384 | 1.000 | 0.412 | 1.000 | 0.00% |
| 2024_latest | 50/50 TQQQ/QQQ | cost_5bps_delay0d | 5.0 | 0 | 43.00% | -42.29% | 1.017 | 2.383 | 1.000 | 0.412 | 1.000 | 0.12% |
| 2024_latest | 50/50 TQQQ/QQQ | cost_10bps_delay0d | 10.0 | 0 | 43.00% | -42.29% | 1.017 | 2.381 | 1.000 | 0.412 | 1.000 | 0.24% |
| 2024_latest | 50/50 TQQQ/QQQ | cost_25bps_delay0d | 25.0 | 0 | 43.00% | -42.29% | 1.017 | 2.378 | 1.000 | 0.412 | 1.000 | 0.60% |
| 2024_latest | 50/50 TQQQ/QQQ | cost_50bps_delay0d | 50.0 | 0 | 43.00% | -42.29% | 1.017 | 2.372 | 1.000 | 0.412 | 1.000 | 1.19% |
| 2024_latest | 50/50 TQQQ/QQQ | stress_10bps_delay1d | 10.0 | 1 | 43.00% | -42.29% | 1.017 | 2.381 | 1.000 | 0.412 | 1.000 | 0.24% |
| 2024_latest | 50/50 TQQQ/QQQ | stress_25bps_delay1d | 25.0 | 1 | 43.00% | -42.29% | 1.017 | 2.378 | 1.000 | 0.412 | 1.000 | 0.60% |
| 2024_latest | MA150 risk-off QQQ | base_0bps | 0.0 | 0 | 46.24% | -40.42% | 1.144 | 2.517 | 33.000 | 13.588 | 17.000 | 0.00% |
| 2024_latest | MA150 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 45.28% | -40.59% | 1.115 | 2.476 | 33.000 | 13.588 | 17.000 | 4.12% |
| 2024_latest | MA150 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 44.32% | -40.77% | 1.087 | 2.435 | 33.000 | 13.588 | 17.000 | 8.18% |
| 2024_latest | MA150 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | 41.49% | -41.31% | 1.004 | 2.317 | 33.000 | 13.588 | 17.000 | 19.98% |
| 2024_latest | MA150 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | 36.87% | -43.20% | 0.853 | 2.132 | 33.000 | 13.588 | 17.000 | 38.46% |
| 2024_latest | MA150 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | 50.69% | -36.87% | 1.375 | 2.704 | 33.000 | 13.588 | 17.000 | 9.08% |
| 2024_latest | MA150 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | 47.73% | -37.44% | 1.275 | 2.573 | 33.000 | 13.588 | 17.000 | 22.19% |
| 2024_latest | MA200 risk-off QQQ | base_0bps | 0.0 | 0 | 51.65% | -41.68% | 1.239 | 2.749 | 13.000 | 5.353 | 7.000 | 0.00% |
| 2024_latest | MA200 risk-off QQQ | cost_5bps_delay0d | 5.0 | 0 | 51.28% | -41.85% | 1.225 | 2.731 | 13.000 | 5.353 | 7.000 | 1.78% |
| 2024_latest | MA200 risk-off QQQ | cost_10bps_delay0d | 10.0 | 0 | 50.91% | -42.03% | 1.211 | 2.714 | 13.000 | 5.353 | 7.000 | 3.55% |
| 2024_latest | MA200 risk-off QQQ | cost_25bps_delay0d | 25.0 | 0 | 49.79% | -42.55% | 1.170 | 2.661 | 13.000 | 5.353 | 7.000 | 8.81% |
| 2024_latest | MA200 risk-off QQQ | cost_50bps_delay0d | 50.0 | 0 | 47.94% | -43.41% | 1.104 | 2.575 | 13.000 | 5.353 | 7.000 | 17.38% |
| 2024_latest | MA200 risk-off QQQ | stress_10bps_delay1d | 10.0 | 1 | 52.17% | -39.64% | 1.316 | 2.769 | 13.000 | 5.353 | 7.000 | 3.63% |
| 2024_latest | MA200 risk-off QQQ | stress_25bps_delay1d | 25.0 | 1 | 51.04% | -40.18% | 1.270 | 2.716 | 13.000 | 5.353 | 7.000 | 8.99% |
| 2024_latest | MA150 risk-off cash | base_0bps | 0.0 | 0 | 37.05% | -38.03% | 0.974 | 2.150 | 17.000 | 7.000 | 17.000 | 0.00% |
| 2024_latest | MA150 risk-off cash | cost_5bps_delay0d | 5.0 | 0 | 36.59% | -38.12% | 0.960 | 2.132 | 17.000 | 7.000 | 17.000 | 1.82% |
| 2024_latest | MA150 risk-off cash | cost_10bps_delay0d | 10.0 | 0 | 36.15% | -38.21% | 0.946 | 2.113 | 17.000 | 7.000 | 17.000 | 3.63% |
| 2024_latest | MA150 risk-off cash | cost_25bps_delay0d | 25.0 | 0 | 34.80% | -38.49% | 0.904 | 2.060 | 17.000 | 7.000 | 17.000 | 8.96% |
| 2024_latest | MA150 risk-off cash | cost_50bps_delay0d | 50.0 | 0 | 32.59% | -38.95% | 0.837 | 1.974 | 17.000 | 7.000 | 17.000 | 17.56% |
| 2024_latest | MA150 risk-off cash | stress_10bps_delay1d | 10.0 | 1 | 45.53% | -37.46% | 1.215 | 2.485 | 17.000 | 7.000 | 17.000 | 4.26% |
| 2024_latest | MA150 risk-off cash | stress_25bps_delay1d | 25.0 | 1 | 44.09% | -37.65% | 1.171 | 2.422 | 17.000 | 7.000 | 17.000 | 10.53% |
