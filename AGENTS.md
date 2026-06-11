# AGENTS.md

This repository is a systematic trading research project. Backtest correctness, reproducibility, and absence of look-ahead bias take priority over attractive performance numbers.

## Strict Backtest Rules

1. All backtest-related changes must prioritize eliminating future leakage and look-ahead bias.
2. Daily backtests use this default execution model:
   - Day `t` returns must be earned only by positions already fixed after the day `t-1` close.
   - Day `t` close, return, risk flag, Kelly estimate, regime signal, model prediction, or other day `t` information may only create signals effective on day `t+1` or later.
   - Day `t` close, return, or risk flag must never change day `t` PnL.
3. When using real TQQQ adjusted close or `auto_adjust` prices, do not subtract the fund expense ratio again.
4. Management fees, financing costs, and explicit expense drag may be deducted only from clearly labeled synthetic series.
5. Every deep-learning backtest must have an explicit train cutoff such as `initial_train_end`.
6. Before the train cutoff, DL must not affect any position, trade, crash cap, Bandit state, equity return, or reported result.
7. Benchmarks must include:
   - raw TQQQ buy-and-hold from adjusted prices;
   - cost-adjusted synthetic benchmarks only when explicitly requested;
   - strategy effective leverage capped at `<= 3x` when making a headline comparison against long-term TQQQ.
8. Any strategy-logic change must add or update automated tests that would catch the relevant failure mode.
9. After each completed fix, run `pytest` and report the exact command and result.
10. Do not hide risk by only weakening tests, changing expected values, or deleting coverage.

## Safety Rules

- Do not read or print secrets, `.env`, API keys, broker credentials, tokens, or local live-trading configuration.
- Do not connect to brokers, place orders, cancel orders, or run live/paper execution while auditing backtests.
- Do not improve headline performance by weakening costs, timing, risk controls, benchmark assumptions, or sample discipline.
- Robust/OOS runners must pass explicit DL cutoffs into every DL path, including validation, walk-forward, and pilot paths.
- Synthetic or hybrid leveraged ETF paths must be labeled clearly and must not be used as live-tradable proof unless the live-tradable segment is isolated.

## Required Workflow

For each fix:

1. Locate the relevant code.
2. Add or update an automated test that would fail before the fix.
3. Make the smallest implementation change that enforces the stricter assumption.
4. Run tests.
5. Document the prior bug, the corrected behavior, test evidence, and residual risks.

Do not modify unrelated files while performing staged audit fixes.
