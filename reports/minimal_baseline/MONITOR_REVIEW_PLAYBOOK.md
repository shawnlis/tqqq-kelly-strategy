# Frozen Monitor Review Playbook

## Scope

- The monitor is read-only and monitoring only.
- It is not a trade recommendation.
- It is not paper trading.
- It does not connect to brokers, read accounts, generate orders, or suggest position size.
- Signal is generated after the close and is only meaningful for the next trading session.
- Current status remains HOLD / NOT READY FOR PAPER TRADING.

## Required Review When signal_changed=True

1. Confirm the data date is correct.
2. Confirm QQQ close is credible against an independent market-data source.
3. Confirm MA150 is reasonable and based only on available close data.
4. Check for missing data, holidays, partial sessions, or stale prices.
5. Confirm the strategy still satisfies the go/no-go hurdles.
6. Confirm the project remains HOLD / NOT READY.

## Prohibited Actions

- Do not automatically place orders.
- Do not read account data.
- Do not change the rule.
- Do not enter live or paper trading based on a single signal.
- Do not treat monitor target as a trade instruction.
- Do not add broker, shares, notional, account, or order fields to monitor history.

## Record Discipline

- History rows are signal observations, not trades.
- Duplicate as-of dates are skipped by default.
- Use duplicate rows only when explicitly documenting a rerun or data correction.
- Any future paper-trading approval must be documented outside this monitor.
