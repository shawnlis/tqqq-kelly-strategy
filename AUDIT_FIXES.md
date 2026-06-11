# Audit Fixes

## Issue 3: TQQQ / QQQ5 Expense Double Count

### Original Problem

The project uses real market or adjusted TQQQ prices by default, and synthetic QQQ5 is already built as a fee-adjusted synthetic NAV. Several backtest paths also subtracted `tqqq_expense / 252` or `qqq5_expense / 252` from those price returns, which double counted fund expense drag and understated both the TQQQ benchmark and strategy sleeves.

### Files Changed

- `qqq_deep_learning_and_baseline_experimental.py`
- `robust_fast.py`
- `tests/test_benchmark_expense.py`

### Fix

- Default TQQQ sleeve returns now use raw TQQQ price returns.
- Default QQQ5 sleeve returns now use raw QQQ5 price returns.
- Default TQQQ buy-and-hold benchmark returns now use raw TQQQ price returns.
- Additional expense subtraction only occurs when `deduct_tqqq_expense_in_returns` or `deduct_qqq5_expense_in_returns` is explicitly enabled.
- Reports now include expense-policy fields showing whether extra expense was deducted and whether price series are assumed to already embed expense.
- `robust_fast.py` now uses raw TQQQ price returns by default for pilot relative checks, OOS benchmark comparisons, and alpha calculations.

### Tests

- `tests/test_benchmark_expense.py`

### Verification

Run:

```powershell
python -m py_compile qqq_deep_learning_and_baseline_experimental.py robust_fast.py
python -m pytest -q tests/test_benchmark_expense.py --basetemp=tmp/pytest-expense
python -m pytest -q --basetemp=tmp/pytest
```

Expected result: all tests pass.

### Residual Limits

- The code assumes yfinance `auto_adjust=True` and CSV adjusted or market prices already reflect real ETF expenses.
- Synthetic QQQ5 fee modeling remains dependent on the assumptions inside `synth_leveraged_from_base`.
- This issue does not fix leverage-cap fairness, headline QQQ5 tradability, strict OOS path slicing, or Sharpe/metric standardization.

## Issue 4: Effective Leverage Cap for Fair TQQQ Comparison

### Original Problem

The strategy could combine futures exposure, TQQQ, and synthetic QQQ5 into an effective beta exposure above long TQQQ's nominal 3x exposure. Outperformance versus buy-and-hold TQQQ could therefore be driven by higher leverage rather than a defensible timing or allocation edge.

### Files Changed

- `qqq_deep_learning_and_baseline_experimental.py`
- `robust_fast.py`
- `tests/test_effective_leverage_cap.py`

### Fix

- Added `compute_effective_leverage_components` and `enforce_effective_leverage_cap`.
- Added `max_effective_leverage` to `baseline_backtest` and `deep_learning_backtest`.
- The cap is applied only after day `t` PnL is computed and after day `t` close signals have generated next-day target weights.
- When the cap is active, excess exposure is reduced in this order: QQQ5 sleeve, TQQQ sleeve, FUT leverage, then FUT weight to cash.
- `weights_df` now records next-day target `effective_leverage`.
- Reports now include cap enabled, cap level, cap hit count, average/max/P95 effective leverage, and max cap violation.
- `robust_fast.py` can pass `--max-effective-leverage` through pilot, DL validation, OOS verification, and manifest metadata.

### Tests

- `tests/test_effective_leverage_cap.py`

### Verification

Run:

```powershell
python -m py_compile qqq_deep_learning_and_baseline_experimental.py robust_fast.py
python -m pytest -q tests/test_effective_leverage_cap.py --basetemp=tmp/pytest-leverage
python -m pytest -q --basetemp=tmp/pytest
```

Expected result: all tests pass. With `max_effective_leverage=3.0`, baseline and DL next-day target weights should have `effective_leverage <= 3.0 + epsilon`.

### Residual Limits

- The `<=3x` cap only addresses nominal beta exposure fairness against TQQQ. It does not make the strategy volatility-matched, drawdown-matched, or path-risk-matched versus TQQQ.
- Synthetic QQQ5 remains a separate tradability/headline issue and still needs isolation before live-tradable claims.
- Strict OOS slicing and metric standardization are still separate pending phases.

## Issue 5: Synthetic / Hybrid QQQ5 Headline Isolation

### Original Problem

The project could use a QQQ5 series that was synthetic or synthetic-plus-real hybrid data. That is useful for research, but it can make headline results misleading because the long historical series may not have been tradable as shown.

### Files Changed

- `qqq_deep_learning_and_baseline_experimental.py`
- `robust_fast.py`
- `tests/test_qqq5_isolation.py`

### Fix

- Added QQQ5 source classification: `disabled`, `csv`, `synthetic`, `hybrid`, `real_market`, and `unknown`.
- `load_prices` now records QQQ5 source metadata in `df.attrs["price_meta"]`.
- `build_qqq5_hybrid_from_base` tags returned QQQ5 series as `synthetic`, `hybrid`, or `real_market`.
- Added `disable_qqq5`, `allow_synthetic_qqq5`, and `qqq5_source` to baseline and DL backtests.
- Default behavior is strict: synthetic, hybrid, and unknown QQQ5 sources are not allowed unless `allow_synthetic_qqq5=True`.
- `allow_synthetic_qqq5=True` enables research use only. It does not make QQQ5 headline eligible.
- Only `real_market` QQQ5 is headline eligible when QQQ5 is not disabled.
- All QQQ5 buy paths are protected by the same `qqq5_allowed` gate in `book_trade`.
- Reports now include QQQ5 source, allowed/disabled flags, headline eligibility, max/average QQQ5 weight, QQQ5 trade count, and strict no-synthetic status.
- `robust_fast.py` now passes and records `disable_qqq5`, `allow_synthetic_qqq5`, and `qqq5_source` in common kwargs, gate signature, and manifest metadata.

### Tests

- `tests/test_qqq5_isolation.py`

### Verification

Run:

```powershell
python -m py_compile qqq_deep_learning_and_baseline_experimental.py robust_fast.py
python -m pytest -q tests/test_qqq5_isolation.py --basetemp=tmp/pytest-qqq5
python -m pytest -q --basetemp=tmp/pytest
```

Expected result: all tests pass. With `disable_qqq5=True`, or with source `synthetic` / `hybrid` / `unknown` and `allow_synthetic_qqq5=False`, baseline and DL next-day target weights should have `w_qqq5 == 0`.

### Residual Limits

- Even if real-market QQQ5 is available, the audit still needs exchange, timezone, currency, tax, liquidity, broker availability, and tracking-error checks.
- Headline proof should preferably still use TQQQ/QQQ-only or QQQ/TQQQ/futures-only versions unless the live-tradable QQQ5 segment is isolated and separately validated.
- This issue does not fix strict OOS slicing, metric standardization, or volatility-matched benchmarks.

## Issue 6: Strict OOS / Walk-Forward Without Future Path Pollution

### Original Problem

Legacy OOS and walk-forward paths could run a stateful strategy over a longer or full future path and then slice the resulting equity curve for OOS statistics. For adaptive components such as Kelly updates, risk gates, Bandit state, DL retraining, crash caps, and allocation state, that can contaminate OOS slices with path state that would not have existed at the slice's test end.

### Files Changed

- `qqq_deep_learning_and_baseline_experimental.py`
- `robust_fast.py`
- `tests/test_strict_oos.py`
- `tests/test_robust_fast_strict_oos.py`

### Fix

- Added `run_strict_oos_slice`, which immediately truncates input data to `train_start:test_end`.
- The helper also truncates time-series inputs in `common_kw`, including RF, VIX, and ADV-like series, to the same strict index.
- OOS equity, weights, trades, notes, and report metrics are computed only from `test_start:test_end`.
- DL strict OOS defaults `initial_train_end=train_end` and rejects `initial_train_end > train_end`.
- Added `run_strict_walk_forward`, which calls `run_strict_oos_slice` independently for each slice.
- Added CLI flags for strict OOS and strict walk-forward outputs in the strategy module.
- `robust_fast.py` now supports `--strict-oos-verify` and `--strict-oos-spans`; strict verification calls the strategy module's `run_strict_oos_slice` rather than legacy full-path slicing.
- Robust manifests and gate signatures now record strict OOS flags/spans, effective leverage cap, QQQ5 policy, QQQ5 source, and `initial_train_end`.

### Tests

- `tests/test_strict_oos.py`
- `tests/test_robust_fast_strict_oos.py`

### Verification

Run:

```powershell
python -m py_compile qqq_deep_learning_and_baseline_experimental.py robust_fast.py
python -m pytest -q tests/test_strict_oos.py tests/test_robust_fast_strict_oos.py --basetemp=tmp/pytest-strict-oos
python -m pytest -q --basetemp=tmp/pytest
```

Expected result: all tests pass. Appending extreme data after a slice's `test_end` must not change that slice's OOS equity or OOS report metrics.

### Residual Limits

- Strict OOS prevents future path pollution inside each evaluated slice, but it does not solve parameter data mining.
- Parameter selection still needs train-only selection, nested walk-forward, or an untouched holdout discipline.
- This issue does not standardize Sharpe/metric definitions or add volatility-matched benchmarks.

## Issue 7: Standardized Performance Metrics

### Original Problem

Report fields mixed multiple metric definitions. In particular, `Sharpe_ex_rf0` was computed like `CAGR / Vol` in several paths, while robust comparison logic also mixed alpha, Calmar, and OOS quality metrics from different definitions. That made strategy-vs-TQQQ comparisons harder to audit and could overstate a standard Sharpe ratio.

### Files Changed

- `qqq_deep_learning_and_baseline_experimental.py`
- `robust_fast.py`
- `tests/test_metrics_standardization.py`

### Fix

- Added `compute_performance_metrics` with `Metric_Definition_Version = "v2_standard_daily_excess"`.
- The helper now computes standard daily excess return Sharpe from daily returns, with RF aligned by date.
- `CAGR_over_Vol` is reported separately and is no longer treated as the standard Sharpe.
- Added Sortino, beta, annualized alpha, tracking error, information ratio, and benchmark metrics.
- `baseline_backtest`, `deep_learning_backtest`, `ma_crossover_backtest`, and strict OOS reporting now use the unified helper.
- Backward-compatible fields remain:
  - `Vol` maps to `AnnVol`.
  - `Sharpe_ex_rf0` maps to `Sharpe_DailyExcess`.
  - `TQQQ_*` fields map to the benchmark metrics.
- `robust_fast.py` now prefers `Sharpe_DailyExcess`, `CAGR_over_Vol`, `Alpha_vs_Benchmark_Ann`, and `InformationRatio_vs_Benchmark`, with old fields used only as fallback where needed.

### Tests

- `tests/test_metrics_standardization.py`

### Verification

Run:

```powershell
python -m py_compile qqq_deep_learning_and_baseline_experimental.py robust_fast.py
python -m pytest -q tests/test_metrics_standardization.py --basetemp=tmp/pytest-metrics
python -m pytest -q --basetemp=tmp/pytest
```

Expected result: all tests pass. `Sharpe_DailyExcess` should match the standard daily excess-return formula, while `CAGR_over_Vol` remains a separate compatibility/summary metric.

### Residual Limits

- Metrics standardization does not prove the strategy is effective.
- The audit still needs volatility-matched benchmarks, nested parameter selection, realistic trading costs, borrow/financing checks, and tax analysis.

## Post-Fix Audit Summary

This summary maps the original high-risk audit issues to the current fixes and automated evidence.

### Issue 1: Same-Day Signal / Same-Day Return Timing

- Status: fixed.
- Evidence: `tests/test_backtest_timing.py`.
- What is verified: day `t` PnL is computed from day `t-1` target positions; day `t` risk gate, Kelly, regime, DL, and allocation decisions can only affect later target weights.
- Verification command:

```powershell
python -m pytest -q tests/test_backtest_timing.py --basetemp=tmp/pytest-timing
```

### Issue 2: DL Cutoff and Label Leakage

- Status: fixed.
- Evidence: `tests/test_dl_cutoff_safety.py`, `tests/test_robust_fast_dl_cutoff.py`, and `tests/test_no_dl_full_sample_leak.py`.
- What is verified: `initial_train_end` is required for DL paths; DL does not predict or affect trades before cutoff; rolling retrain data is label-buffered; `robust_fast.py` propagates the cutoff.
- Verification command:

```powershell
python -m pytest -q tests/test_dl_cutoff_safety.py tests/test_robust_fast_dl_cutoff.py tests/test_no_dl_full_sample_leak.py --basetemp=tmp/pytest-dl
```

### Issue 3: Expense Double Count

- Status: fixed.
- Evidence: `tests/test_benchmark_expense.py`.
- What is verified: raw adjusted TQQQ benchmark returns are not reduced by expense again by default; TQQQ and QQQ5 sleeves only deduct extra expense when explicit flags are enabled.

### Issue 4: Effective Leverage Cap

- Status: fixed.
- Evidence: `tests/test_effective_leverage_cap.py`.
- What is verified: `max_effective_leverage=3.0` caps next-day target effective exposure for baseline and DL paths without changing already-earned day `t` PnL.

### Issue 5: QQQ5 Source Isolation

- Status: fixed.
- Evidence: `tests/test_qqq5_isolation.py`.
- What is verified: `disable_qqq5=True` forces `w_qqq5 == 0`; synthetic, hybrid, and unknown QQQ5 sources are disabled by default and are not headline eligible.

### Issue 6: Strict OOS / Walk-Forward

- Status: fixed.
- Evidence: `tests/test_strict_oos.py` and `tests/test_robust_fast_strict_oos.py`.
- What is verified: strict OOS truncates input at `test_end`; appending extreme future rows after `test_end` does not change OOS equity or reports; `robust_fast.py` strict OOS verification calls the strategy strict helper.

### Issue 7: Metrics Standardization

- Status: fixed.
- Evidence: `tests/test_metrics_standardization.py`.
- What is verified: `Sharpe_DailyExcess` is standard daily excess return Sharpe; `CAGR_over_Vol` is separate; reports include standardized benchmark, alpha, beta, tracking error, information ratio, and compatibility aliases.

### Remaining Post-Fix Limitations

- These fixes reduce known backtest bias paths, but they do not prove durable outperformance versus TQQQ.
- Headline claims still require actual strict OOS results with `max_effective_leverage=3.0`, no synthetic/hybrid QQQ5, raw TQQQ benchmark returns, and standardized metrics.
- Parameter data mining, regime overfitting, volatility-matched benchmarks, nested parameter selection, taxes, and live execution gaps remain open research work.
