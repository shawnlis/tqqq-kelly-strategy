# Frozen MA150 Forward Monitor Specification

## Frozen Rule

- MA150 rule is frozen.
- Risk-on asset: TQQQ.
- Risk-off asset: QQQ.
- Signal is based on QQQ close versus QQQ MA150.
- Signal at day t close applies to the next trading session.
- The monitor is not paper-trading ready and does not authorize allocation.

## Safety Boundary

- No broker connection.
- No account read.
- No order generation.
- No automatic execution.
- No trade recommendation.
- Output is monitoring-only and must not be treated as an instruction to trade.

## Manual Checklist Before Any Future Paper Trading

1. Confirm no-lookahead daily timing remains covered by tests.
2. Confirm strict OOS and raw TQQQ benchmark assumptions remain unchanged.
3. Confirm cost, turnover, and operational feasibility hurdles remain acceptable.
4. Confirm daily artifacts are retained for every monitor run used in research review.
5. Confirm the rule remains frozen; do not tune MA windows from monitor outcomes.
6. Confirm paper-trading entry hurdles in GO_NO_GO_HURDLES.md are met.

## Conditions Before Paper Trading Is Reconsidered

- Current status must move from HOLD to an explicitly documented approval state.
- The strategy must pass the frozen go/no-go hurdles without parameter changes.
- Slippage/cost, underwater/pain, and operational-burden evidence must remain acceptable.
- A separate paper-trading plan must define capital cap, kill switch, logs, and manual operator controls.
- This monitor alone is insufficient evidence for paper trading.
