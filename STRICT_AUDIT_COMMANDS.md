# Strict Audit Commands

These commands are templates for reproducible strict audit runs. They do not place orders and should be run only in a local research environment. Do not add expense-deduction flags unless deliberately running an explicit stress test; by default, adjusted TQQQ price returns already embed fund expense.

## A. TQQQ-Only Headline Candidate

Baseline:

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode baseline `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --disable_qqq5 `
  --strict_walk_forward `
  --out_prefix strict_headline_tqqq_only_baseline
```

DL:

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode dl `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --disable_qqq5 `
  --strict_walk_forward `
  --out_prefix strict_headline_tqqq_only_dl
```

Headline candidates must use raw TQQQ benchmark returns, `Sharpe_DailyExcess`, capped effective leverage, and strict OOS evidence. `CAGR_over_Vol` may be reported but must not be called standard Sharpe.

## B. Research-Only QQQ5 Allowed

This run explicitly permits synthetic or hybrid QQQ5 research. It is not headline proof.

```powershell
python qqq_deep_learning_and_baseline_experimental.py `
  --mode dl `
  --start 2015-01-01 `
  --end auto `
  --initial_train_end 2018-12-31 `
  --max_effective_leverage 3.0 `
  --allow_synthetic_qqq5 `
  --strict_walk_forward `
  --out_prefix research_only_qqq5_allowed_dl
```

Interpretation rule: if `QQQ5_Source` is `synthetic`, `hybrid`, `unknown`, or `csv`, and `QQQ5_MaxWeight > 0`, the output is research-only even if the backtest beats TQQQ.

## C. robust_fast Strict Search

Use strict OOS verification, a 3x cap, disabled QQQ5, and explicit strict spans. `robust_fast.py` does not normalize `--end auto`, so use a concrete end date.

Baseline strict search:

```powershell
python robust_fast.py `
  --strategy qqq_deep_learning_and_baseline_experimental.py `
  --start 2015-01-01 `
  --end 2026-06-11 `
  --mode baseline `
  --threads 4 `
  --max-runs 120 `
  --topk 20 `
  --strict-oos-verify `
  --strict-oos-spans "2015-01-01:2018-12-31:2019-01-01:2021-12-31@2019_2021,2015-01-01:2021-12-31:2022-01-01:2023-12-31@2022_2023,2015-01-01:2023-12-31:2024-01-01:2026-06-11@2024_latest" `
  --max-effective-leverage 3.0 `
  --disable-qqq5 `
  --initial-train-end 2018-12-31 `
  --out-prefix robust_strict_tqqq_only_baseline
```

DL strict search:

```powershell
python robust_fast.py `
  --strategy qqq_deep_learning_and_baseline_experimental.py `
  --start 2015-01-01 `
  --end 2026-06-11 `
  --mode dl `
  --threads 4 `
  --max-runs 120 `
  --topk 20 `
  --strict-oos-verify `
  --strict-oos-spans "2015-01-01:2018-12-31:2019-01-01:2021-12-31@2019_2021,2015-01-01:2021-12-31:2022-01-01:2023-12-31@2022_2023,2015-01-01:2023-12-31:2024-01-01:2026-06-11@2024_latest" `
  --max-effective-leverage 3.0 `
  --disable-qqq5 `
  --initial-train-end 2018-12-31 `
  --out-prefix robust_strict_tqqq_only_dl
```

Do not add `--allow-synthetic-qqq5` to a headline robust search. If it is used for exploration, label all outputs research-only and exclude them from headline proof.
