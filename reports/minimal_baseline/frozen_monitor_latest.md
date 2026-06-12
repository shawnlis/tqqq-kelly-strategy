# Frozen MA150 Monitor

Monitoring only, not trade instruction.

## Current Signal

- As-of date: 2026-06-11
- QQQ close: 717.120
- QQQ MA150: 632.565
- Distance to MA: 13.37%
- Current signal: risk_on
- Next-session target: TQQQ
- Signal changed?: False
- Last switch date: 2026-04-08
- Trading sessions since switch: 45
- Status: HOLD / NOT READY FOR PAPER TRADING

## Manual Review Checklist

- Confirm this run used only prices available at or before the as-of date.
- Confirm no broker, account, or order system was connected.
- Confirm this monitor output is not being used as an order ticket.
- Confirm MA150 remains frozen and no parameter was changed.
- Confirm the HOLD / NOT READY status remains in force.
- Confirm any future paper-trading consideration is handled in a separate approval document.

## Notes

- Monitoring only; not a trade instruction.
- Day t close signal applies to the next trading session.
- Frozen rule: QQQ close > QQQ MA150 means risk-on TQQQ; otherwise risk-off QQQ.
- Decision remains HOLD and paper-trading status remains NOT_READY.
- No broker connection, account read, order generation, or automatic execution is performed.
