# Final Audit Report

## Executive Verdict

Current code has significantly reduced backtest bias risk, but the project still cannot claim from code alone that the strategy reliably beats long-term TQQQ. That claim requires actual strict OOS results using capped leverage, no synthetic QQQ5 headline exposure, raw TQQQ benchmark returns, no expense double count, and standardized metrics.

The repaired code is suitable for stricter research validation. It is not, by itself, proof of stable live-tradable outperformance over buy-and-hold TQQQ.

## What Was Fixed

1. Same-day signal / return timing: baseline and DL backtests now compute day `t` PnL from day `t-1` target positions before day `t` signals can affect future targets.
2. DL cutoff leakage: DL cannot influence positions, trades, crash caps, notes, Bandit state, or returns before `initial_train_end`.
3. DL label buffer: initial and rolling DL training windows exclude rows whose future labels would cross the current prediction date.
4. TQQQ / QQQ5 expense double count: real adjusted TQQQ prices and fee-adjusted QQQ5 series no longer get extra expense deducted by default.
5. Effective leverage cap: baseline and DL support `max_effective_leverage`, with 3.0 required for fair nominal comparison to TQQQ.
6. QQQ5 source isolation: synthetic, hybrid, unknown, and CSV QQQ5 sources are not headline eligible by default; `disable_qqq5=True` forces the sleeve to zero.
7. Strict OOS / walk-forward: strict helpers truncate data at each `test_end` and avoid full future-path pre-runs before slicing.
8. Metrics standardization: reports now include `Sharpe_DailyExcess`, `CAGR_over_Vol`, Sortino, beta, annualized alpha, tracking error, information ratio, and benchmark metrics under a common definition version.

## Current Test Status

Latest validation before this report:

```text
python -m pytest -q --basetemp=tmp/pytest
54 passed, 102 warnings
```

Warnings are from installed third-party packages and existing imports. They are not new strategy logic failures.

## Headline Eligibility Rules

A result is not headline eligible unless all of the following are true:

1. `max_effective_leverage <= 3.0`, and report max effective leverage is within floating tolerance of the cap.
2. QQQ5 is disabled, or `QQQ5_HeadlineEligible=True` from a real-market QQQ5 source.
3. No synthetic or hybrid QQQ5 data contributes to the headline result.
4. Strict OOS and strict walk-forward results are used instead of in-sample or full-path-sliced OOS.
5. TQQQ benchmark uses raw adjusted price returns.
6. No extra TQQQ expense deduction is applied to adjusted TQQQ price returns.
7. Metrics use `Sharpe_DailyExcess` for standard Sharpe claims, with `CAGR_over_Vol` disclosed separately.
8. The claim is checked against raw TQQQ buy-and-hold and not only against cash or a synthetic benchmark.

## Remaining Risks

- Parameter data mining and multiple testing remain open risks.
- TQQQ buy-and-hold itself has extreme tail risk and can experience very deep drawdowns.
- Taxes are not modeled.
- Borrow, financing, futures roll, margin, and cash collateral mechanics are not fully live-realistic.
- yfinance and CSV data quality can still affect conclusions.
- QQQ5 real-market availability, exchange access, timezone, currency, tax treatment, liquidity, broker availability, and tracking error need separate validation.
- Live execution can diverge from backtest execution through market impact, partial fills, halts, opening gaps, and operational delays.
- Regime overfitting remains possible.
- A volatility-matched benchmark has not yet been added.
- Nested walk-forward parameter selection has not yet been implemented.

## Recommended Next Research

1. Run strict TQQQ-only audit.
2. Run strict capped-3x no-QQQ5 audit.
3. Compare to raw TQQQ buy-and-hold.
4. Add a volatility-matched benchmark.
5. Add nested parameter selection or an untouched holdout protocol.
6. Add tax and slippage stress.
7. Only then consider a small paper-trading simulation, with no real broker execution enabled by default.

## Final Recommendation

Keep the project in research mode until strict OOS, capped-3x, no-synthetic-QQQ5 results demonstrate robust behavior against raw TQQQ buy-and-hold and a volatility-matched benchmark. Any result using synthetic or hybrid QQQ5 should be labeled research-only.
