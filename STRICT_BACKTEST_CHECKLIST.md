# Strict Backtest Checklist

This checklist defines the minimum conditions for treating a TQQQ Kelly strategy result as an audit-grade headline candidate. Passing this checklist does not prove the strategy is suitable for live trading; it only reduces known backtest bias paths.

## Required Strict Audit Conditions

1. Day `t` PnL must be earned only by positions fixed after the day `t-1` close.
2. Day `t` close, return, drawdown, risk flag, Kelly update, regime score, DL prediction, crash cap, or Bandit decision may only affect day `t+1` or later target weights.
3. DL must not affect any position, trade, crash cap, Bandit state, equity return, notes, or report before `initial_train_end`.
4. DL retraining must use a label buffer. Training data must exclude rows whose future labels would cross the current prediction date.
5. Real TQQQ adjusted price or `auto_adjust` price returns must not have fund expense deducted again.
6. Synthetic or hybrid QQQ5 is research-only by default and must not enter a headline result.
7. `max_effective_leverage=3.0` is required for a fair nominal exposure comparison against long TQQQ.
8. Strict OOS must call the strict helpers and must not run the full future path before slicing OOS statistics.
9. The TQQQ benchmark must default to raw TQQQ price returns, with no extra expense subtraction.
10. Metrics must use `Sharpe_DailyExcess` for standard daily excess return Sharpe. `CAGR_over_Vol` must be listed separately and must not be described as standard Sharpe.

## Recommended Strict Audit Commands

The strategy CLI uses boolean action flags. Do not pass `0` to expense-deduction flags. By default, no additional TQQQ or QQQ5 expense is deducted from price returns.

Run baseline and DL strict audits separately so each strict walk-forward artifact has an unambiguous mode.

### Baseline TQQQ-Only Headline Candidate

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode baseline `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --disable_qqq5 `
  --strict_walk_forward `
  --out_prefix strict_audit_tqqq_only_baseline
```

### DL TQQQ-Only Headline Candidate

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode dl `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --disable_qqq5 `
  --strict_walk_forward `
  --out_prefix strict_audit_tqqq_only_dl
```

Using `--mode both` is allowed for side-by-side normal reports, but the current strict walk-forward helper selects one strict mode. Use the separate baseline and DL commands above for headline artifacts.

## Research-Only QQQ5 Command

The following command allows synthetic QQQ5 for research only. It is not headline eligible and must not be used as proof that the strategy can live-tradeably beat TQQQ.

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode dl `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --allow_synthetic_qqq5 `
  --strict_walk_forward `
  --out_prefix research_only_synthetic_qqq5_dl
```

Reports from this run must be labeled research-only unless `QQQ5_HeadlineEligible=True`, which synthetic and hybrid QQQ5 sources should not satisfy.

## Result Interpretation Rules

1. If strategy CAGR is greater than TQQQ CAGR but effective leverage is above 3x, do not claim the strategy beat TQQQ.
2. If strategy CAGR is greater than TQQQ CAGR but the result depends on synthetic or hybrid QQQ5, do not use it as headline proof.
3. If the strategy wins in-sample but fails strict OOS, do not treat it as a live-ready strategy.
4. If strategy CAGR is slightly lower than TQQQ but MaxDD, Calmar, or recovery behavior is materially better, describe it as a TQQQ risk-management strategy, not a TQQQ killer.
5. If `Sharpe_DailyExcess` and `CAGR_over_Vol` disagree, use the standard Sharpe field for Sharpe claims and keep CAGR/Vol as a separate diagnostic.

## Minimum Headline Output Checks

Before making any headline statement, inspect the generated reports and confirm:

- `EffectiveLeverageCapEnabled=True`
- `EffectiveLeverageCap <= 3.0`
- `MaxEffectiveLeverage <= 3.0 + 1e-8`
- `QQQ5_Disabled=True` or `QQQ5_HeadlineEligible=True`
- `Strict_NoSyntheticQQQ5=True`
- `TQQQ_Benchmark_Expense_Deducted=False`
- `TQQQ_Expense_Deducted_In_Returns=False`
- `Metric_Definition_Version=v2_standard_daily_excess`
- Strict OOS and strict walk-forward artifacts exist and are the basis of the conclusion.
