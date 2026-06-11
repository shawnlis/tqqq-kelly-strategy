# Strict TQQQ Audit Results

## Executive Verdict

PARTIAL PASS: strategy improves risk-adjusted profile but does not consistently beat TQQQ CAGR

The strict audit does not prove stable outperformance over long-term TQQQ. The strategy is materially more defensive in several OOS slices, but it does not consistently beat raw TQQQ buy-and-hold on CAGR. Under the requested decision rules, this is not a PASS.

## Data and Run Conditions

- Commit: `3ca5ea1`
- Data download time: `2026-06-11T21:33:53+08:00`
- Data source: public market data through the project loader, primarily yfinance.
- Price data: QQQ and TQQQ downloaded with adjusted/auto-adjusted price handling from the strategy module.
- QQQ5 metadata source: `hybrid`; QQQ5 was disabled for this audit.
- RF/VIX/ADV inputs: public `^IRX`, `^VIX`, and TQQQ volume/price data through the strategy module where available.
- Local cached data: none found by repo file scan before the audit; data was downloaded during the run.
- Loaded price date range: `2015-01-02` to `2026-06-10`.
- Requested audit modes: `baseline, dl`.
- Test baseline before run: `python -m pytest -q --basetemp=tmp/pytest` passed with `55 passed, 102 warnings`.

Commands run:

```powershell
python qqq_deep_learning_and_baseline_experimental.py --mode baseline --start 2015-01-01 --end auto --initial_train_end 2018-12-31 --max_effective_leverage 3.0 --disable_qqq5 --strict_walk_forward --out_prefix reports/strict_audit/baseline_tqqq_only_capped3x
python qqq_deep_learning_and_baseline_experimental.py --mode dl --start 2015-01-01 --end auto --initial_train_end 2018-12-31 --max_effective_leverage 3.0 --disable_qqq5 --strict_walk_forward --out_prefix reports/strict_audit/dl_tqqq_only_capped3x
python qqq_deep_learning_and_baseline_experimental.py --mode both --start 2015-01-01 --end auto --initial_train_end 2018-12-31 --max_effective_leverage 3.0 --disable_qqq5 --strict_walk_forward --out_prefix reports/strict_audit/tqqq_only_capped3x
```

The `--mode both` strict walk-forward artifact is not treated as the primary comparison because the current CLI selects one strict mode internally. The baseline-only and DL-only strict outputs are the authoritative audit runs.

## Strict Constraints Confirmed

- `max_effective_leverage=3.0`: confirmed; max observed effective leverage was `2.484732`.
- `disable_qqq5=True`: confirmed; max observed QQQ5 weight was `0.000000`.
- No synthetic QQQ5 headline exposure: confirmed; QQQ5 was disabled even though source metadata was `hybrid`.
- Raw TQQQ benchmark: confirmed; no extra TQQQ expense-deduction flag was used.
- No extra expense deduction: confirmed; `deduct_tqqq_expense_in_returns=False` and `deduct_qqq5_expense_in_returns=False`.
- Strict OOS / no future slicing: confirmed by `run_strict_oos_slice()` recomputation for each slice.
- Standard metrics: confirmed; summary uses `Sharpe_DailyExcess`, `CAGR_over_Vol`, `Calmar`, and `MaxDD`.
- No new parameter search: confirmed; no tuning or parameter changes were made after seeing results.

## Slice Results

| slice | mode | strategy_cagr | tqqq_cagr | strategy_final_equity | tqqq_final_equity | strategy_maxdd | tqqq_maxdd | strategy_sharpe_daily_excess | tqqq_sharpe_daily_excess | strategy_calmar | tqqq_calmar | max_effective_leverage | qqq5_max_weight | pass_cagr | pass_drawdown | pass_calmar | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | baseline | 35.28% | 106.73% | 2.479 | 8.861 | -22.17% | -69.92% | 1.449 | 1.343 | 1.591 | 1.526 | 1.716 | 0.00% | False | True | True | PARTIAL |
| 2022_2023 | baseline | 6.70% | -22.29% | 1.138 | 0.606 | -33.14% | -81.02% | 0.304 | 0.039 | 0.202 | -0.275 | 1.650 | 0.00% | True | True | True | PASS |
| 2024_latest | baseline | 17.89% | 56.07% | 1.491 | 2.948 | -22.11% | -58.04% | 0.806 | 0.998 | 0.809 | 0.966 | 1.560 | 0.00% | False | True | False | FAIL |
| 2019_2021 | dl | 20.93% | 106.73% | 1.770 | 8.861 | -22.40% | -69.92% | 1.068 | 1.343 | 0.934 | 1.526 | 1.792 | 0.00% | False | True | False | FAIL |
| 2022_2023 | dl | -12.57% | -22.29% | 0.766 | 0.606 | -41.00% | -81.02% | -0.681 | 0.039 | -0.307 | -0.275 | 2.303 | 0.00% | True | True | False | FAIL |
| 2024_latest | dl | 16.15% | 56.07% | 1.438 | 2.948 | -29.26% | -58.04% | 0.709 | 0.998 | 0.552 | 0.966 | 2.485 | 0.00% | False | True | False | FAIL |

## Comparison vs TQQQ

### CAGR

The strategy does not consistently beat TQQQ CAGR. Failed CAGR slices: baseline:2019_2021, baseline:2024_latest, dl:2019_2021, dl:2024_latest. This alone prevents a PASS verdict.

### MaxDD

The strategy generally reduced drawdown versus TQQQ. Drawdown failures: none. Lower drawdown is valuable, but it is not the same claim as beating TQQQ on long-run CAGR.

### Calmar

Calmar improved in the defensive bear-market slices, especially around 2022-2023. It was not consistently enough to make the strategy a TQQQ killer because CAGR lagged badly in strong TQQQ bull slices.

### Sharpe_DailyExcess

`Sharpe_DailyExcess` was often more stable than raw TQQQ in stress periods, but not consistently superior across all slices and modes.

### Effective Leverage

The `max_effective_leverage=3.0` constraint held in all summarized slices. The results therefore do not rely on nominal exposure above 3x.

### Consistency

The baseline and DL variants both show the same broad profile: defensive behavior and lower drawdowns, but no consistent CAGR outperformance versus raw TQQQ. The first DL slice reported insufficient initial leak-safe DL samples and used rule logic until retrain; that slice cannot be cited as evidence that the DL model itself added value.

## Interpretation

If the strategy is attractive, it should be described as a TQQQ risk-management strategy, not a TQQQ killer. It can reduce drawdowns in difficult regimes, but the strict OOS evidence here does not prove stable long-run outperformance over buy-and-hold TQQQ.

Winning only in one slice or in a bear-market slice is not enough to claim durable edge. The DL path is also not clean evidence of model value because at least one strict OOS segment began with rule fallback due to the label-buffered sample requirement.

## Final Recommendation

- Paper trading: not recommended as a "beats TQQQ" strategy based on these results alone. A small research simulation could be justified only as a defensive allocation experiment.
- Further validation needed: nested parameter selection, volatility-matched benchmark, tax/slippage/execution stress, and an untouched holdout or live paper period.
- Headline claim: do not claim the strategy reliably beats long-term TQQQ under strict conditions.
