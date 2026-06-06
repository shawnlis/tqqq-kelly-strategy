# TQQQ Kelly Strategy

This repository contains Python research, backtest, stress-test, and paper/live execution utilities for QQQ/TQQQ-style strategy work. It includes offline tests for operational guardrails, no-lookahead deep-learning cutoff behavior, robust backtest helpers, and paper-trading idempotency.

This hardening branch does not change strategy or investment logic. It adds portable setup docs, a dependency manifest, and fixes stale tests/CLI help formatting.

## Setup

Use Python 3.11 or newer. The current clean-clone checks were run with Python 3.13.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Broker SDKs for live execution are optional and intentionally not required for offline tests. Install and configure broker-specific packages only on a machine intended for live or paper trading.

## Safe Offline Usage

Prefer offline validation and smoke checks first:

```powershell
python -m py_compile .\ib_paper_trader.py .\live_ma_executor.py .\robust_fast.py
python .\live_ma_executor.py --help
python .\ib_paper_trader.py --help
python -m pytest
```

Do not run long backtests, live trading, broker connections, Telegram alerts, or market-data jobs from a fresh clone until local configuration has been reviewed.

## Live Trading Warning

Live trading paths can connect to brokers and may send real orders when explicitly enabled. Keep live credentials and account state local only.

Required local-only files should be based on templates:

- `.env.example` -> `.env`
- `live_trader.example.yaml` -> `live_trader.yaml`
- `telegram.example.py` -> local Telegram configuration or environment variables

Do not commit `.env`, `live_trader.yaml`, Telegram chat IDs, broker credentials, logs, `.state/`, outputs, or private account data.

## Tests

```powershell
python -m pytest
```

The tests cover:

- idempotency and turnover guard helpers
- committee weighting and close-window utilities
- robust-fast manifest and selection behavior
- deep-learning no-lookahead cutoff enforcement

## Intentionally Ignored Files

The repository ignores local-only and generated artifacts, including:

- `.env`, `.env.*`
- `live_trader.yaml`
- `*chatid*`
- `.state/`
- `logs/`, `outputs/`, `cache/`, `diagnostics/`
- archives and temporary folders
- virtual environments and Python caches
- model/data artifacts such as `.pkl`, `.pt`, `.parquet`, `.sqlite`, `.db`

Keep private broker data and generated backtest/trading artifacts outside Git.
