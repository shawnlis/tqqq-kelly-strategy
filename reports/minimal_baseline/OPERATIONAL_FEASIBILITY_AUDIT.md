# Operational Feasibility Audit for MA150 Risk-Off QQQ

## Executive Verdict

MA150 is operationally feasible for continued research, but the added trade events versus static blends keep the decision at HOLD and NOT READY FOR PAPER TRADING.

Current decision: HOLD. Paper-trading status: NOT READY FOR PAPER TRADING.

## Scope And Tax Disclaimer

- This audit measures turnover, trade events, sell-side event proxies, and operational burden.
- It does not provide formal tax advice.
- Tax treatment depends on jurisdiction/account type and requires professional advice.
- For a Singapore-based investor, practical concerns often include operating discipline, USD products, broker execution, reporting, and estate tax / withholding considerations rather than only US-style short-term capital gains. This is not a legal conclusion.
- Static blends are modeled as buy-and-hold with no periodic rebalance; they only have the initial OOS allocation event.

## MA150 Summary

| slice | trades_per_year | switches_per_year | average_turnover_per_year | max_annual_turnover | number_sell_events | number_buy_events | estimated_taxable_events_proxy | taxable_sell_notional_proxy | average_holding_period_proxy_days | percentage_years_with_no_trades | operational_burden_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | 3.329 | 2.996 | 6.325 | 11.000 | 9.000 | 10.000 | 9.000 | 9.000 | 75.700 | 0.333 | 60.061 |
| 2022_2023 | 6.539 | 6.036 | 12.575 | 19.000 | 12.000 | 13.000 | 12.000 | 12.000 | 38.538 | 0.000 | 100.000 |
| 2024_latest | 7.000 | 6.588 | 13.588 | 16.000 | 16.000 | 17.000 | 16.000 | 16.000 | 36.000 | 0.000 | 100.000 |

## Comparison Versus Static Blends

MA150 versus TQQQ buy-and-hold: mean extra trades/year 5.207, mean extra annual turnover 10.413, mean extra operational burden score 82.365.
MA150 versus 70/30 TQQQ/QQQ: mean extra trades/year 5.207, mean extra annual turnover 10.413, mean extra operational burden score 82.365.
MA150 versus 50/50 TQQQ/QQQ: mean extra trades/year 5.207, mean extra annual turnover 10.413, mean extra operational burden score 82.365.

## Strategy-Level Operational Burden

| strategy | MeanTradesPerYear | MeanSwitchesPerYear | MeanAnnualTurnover | MeanSellEvents | MeanTaxableSellNotionalProxy | MeanHoldingPeriodProxyDays | MeanOperationalBurdenScore |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 50/50 TQQQ/QQQ | 0.416 | 0.000 | 0.416 | 0.000 | 0.000 | 623.333 | 4.322 |
| 70/30 TQQQ/QQQ | 0.416 | 0.000 | 0.416 | 0.000 | 0.000 | 623.333 | 4.322 |
| MA150 risk-off QQQ | 5.623 | 5.207 | 10.829 | 12.333 | 12.333 | 50.079 | 86.687 |
| MA150 risk-off cash | 5.512 | 5.207 | 5.512 | 6.000 | 6.000 | 50.079 | 69.926 |
| MA200 risk-off QQQ | 4.807 | 4.391 | 9.199 | 10.333 | 10.333 | 61.304 | 74.698 |
| TQQQ buy-and-hold | 0.416 | 0.000 | 0.416 | 0.000 | 0.000 | 623.333 | 4.322 |

## Feasibility Interpretation

- MA150 is materially more complex than buy-and-hold or static blends because it can require sell-side events during regime switches.
- The strategy is still simple enough to execute manually at low frequency, but paper-trading evidence should include retained daily artifacts and an operator checklist.
- Automation is useful for consistency and audit logs, but the rule should remain manually understandable before any live or paper workflow.
- The sell-side taxable-event proxy is not a tax estimate; it only flags that realized-sale events may exist in taxable accounts.
- If operational burden or taxable-event proxy offsets the pain/Calmar improvement, the MA strategy should remain research-only.

## Full Summary

| slice | strategy | trades_per_year | switches_per_year | average_turnover_per_year | max_annual_turnover | number_sell_events | number_buy_events | estimated_taxable_events_proxy | taxable_sell_notional_proxy | average_holding_period_proxy_days | percentage_years_with_no_trades | operational_burden_score | PeriodicRebalance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | TQQQ buy-and-hold | 0.333 | 0.000 | 0.333 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 757.000 | 1.000 | 3.759 | False |
| 2019_2021 | 70/30 TQQQ/QQQ | 0.333 | 0.000 | 0.333 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 757.000 | 1.000 | 3.759 | False |
| 2019_2021 | 50/50 TQQQ/QQQ | 0.333 | 0.000 | 0.333 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 757.000 | 1.000 | 3.759 | False |
| 2019_2021 | MA150 risk-off QQQ | 3.329 | 2.996 | 6.325 | 11.000 | 9.000 | 10.000 | 9.000 | 9.000 | 75.700 | 0.333 | 60.061 | False |
| 2019_2021 | MA200 risk-off QQQ | 3.995 | 3.662 | 7.657 | 15.000 | 11.000 | 12.000 | 11.000 | 11.000 | 63.083 | 0.333 | 75.240 | False |
| 2019_2021 | MA150 risk-off cash | 2.996 | 2.996 | 2.996 | 5.000 | 4.000 | 5.000 | 4.000 | 4.000 | 75.700 | 0.333 | 40.409 | False |
| 2022_2023 | TQQQ buy-and-hold | 0.503 | 0.000 | 0.503 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 501.000 | 1.000 | 4.913 | False |
| 2022_2023 | 70/30 TQQQ/QQQ | 0.503 | 0.000 | 0.503 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 501.000 | 1.000 | 4.913 | False |
| 2022_2023 | 50/50 TQQQ/QQQ | 0.503 | 0.000 | 0.503 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 501.000 | 1.000 | 4.913 | False |
| 2022_2023 | MA150 risk-off QQQ | 6.539 | 6.036 | 12.575 | 19.000 | 12.000 | 13.000 | 12.000 | 12.000 | 38.538 | 0.000 | 100.000 | False |
| 2022_2023 | MA200 risk-off QQQ | 7.545 | 7.042 | 14.587 | 19.000 | 14.000 | 15.000 | 14.000 | 14.000 | 33.400 | 0.000 | 100.000 | False |
| 2022_2023 | MA150 risk-off cash | 6.539 | 6.036 | 6.539 | 10.000 | 6.000 | 7.000 | 6.000 | 6.000 | 38.538 | 0.000 | 83.515 | False |
| 2024_latest | TQQQ buy-and-hold | 0.412 | 0.000 | 0.412 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 612.000 | 1.000 | 4.294 | False |
| 2024_latest | 70/30 TQQQ/QQQ | 0.412 | 0.000 | 0.412 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 612.000 | 1.000 | 4.294 | False |
| 2024_latest | 50/50 TQQQ/QQQ | 0.412 | 0.000 | 0.412 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 612.000 | 1.000 | 4.294 | False |
| 2024_latest | MA150 risk-off QQQ | 7.000 | 6.588 | 13.588 | 16.000 | 16.000 | 17.000 | 16.000 | 16.000 | 36.000 | 0.000 | 100.000 | False |
| 2024_latest | MA200 risk-off QQQ | 2.882 | 2.471 | 5.353 | 8.000 | 6.000 | 7.000 | 6.000 | 6.000 | 87.429 | 0.333 | 48.853 | False |
| 2024_latest | MA150 risk-off cash | 7.000 | 6.588 | 7.000 | 8.000 | 8.000 | 9.000 | 8.000 | 8.000 | 36.000 | 0.000 | 85.853 | False |
