# Frozen Monitor Automation

This automation is monitoring only. It is not a trade instruction, not paper trading, and not a live execution workflow.

The frozen MA150 monitor updates local monitoring reports and appends a read-only history row. It has no broker connection, no account access, and no order generation.

## Files

- `automation/run_frozen_monitor.ps1.template` is the local runner template.
- `automation/create_frozen_monitor_task.ps1.template` is the Windows Task Scheduler template.
- `reports/minimal_baseline/frozen_monitor_latest.md` is the latest human-readable monitor report.
- `reports/minimal_baseline/frozen_monitor_latest.json` is the latest machine-readable monitor payload.
- `reports/minimal_baseline/frozen_monitor_history.csv` is the append-only monitor history.
- `logs/frozen_monitor_YYYYMMDD.log` is the local run log.

Real `.ps1` files and logs are intentionally ignored by git. Copy the templates locally when installing the automation.

## Install Steps

1. Copy `automation/run_frozen_monitor.ps1.template` to `automation/run_frozen_monitor.ps1`.
2. Review the copied runner and confirm it only calls:

   ```powershell
   python minimal_tqqq_risk_baseline.py `
     --start 2015-01-01 `
     --end auto `
     --frozen_monitor `
     --monitor_out reports/minimal_baseline/frozen_monitor_latest.md `
     --append_monitor_history `
     --monitor_history_csv reports/minimal_baseline/frozen_monitor_history.csv
   ```

3. Copy `automation/create_frozen_monitor_task.ps1.template` to `automation/create_frozen_monitor_task.ps1`.
4. Run the task creation script from PowerShell. By default it registers the task disabled.
5. Enable the task manually only after reviewing the runner, logs path, and monitor status.

The default scheduled time is Singapore 06:45, intended to run after the prior US market close. The template does not request administrator privileges unless Windows requires them for your local Task Scheduler settings.

## Reviewing Logs

Logs are written to `logs/frozen_monitor_YYYYMMDD.log`. Review the latest log after each run for:

- Python exit code.
- Data download or local data errors.
- Monitor report generation.
- History append status.
- Any unexpected exception.

## Disabling The Task

Use Windows Task Scheduler and disable `TQQQ Frozen MA150 Monitor`, or run:

```powershell
Disable-ScheduledTask -TaskName "TQQQ Frozen MA150 Monitor"
```

## If `signal_changed=True`

`signal_changed=True` means manual review is required. It does not authorize any execution.

Manual checklist:

1. Confirm the as-of date is the latest intended market close.
2. Confirm QQQ close and MA150 are credible.
3. Confirm there are no missing-data, holiday, or stale-data issues.
4. Confirm the project remains `HOLD` and `NOT READY FOR PAPER TRADING`.
5. Re-read `reports/minimal_baseline/GO_NO_GO_HURDLES.md`.
6. Record the review outcome separately if needed.

## Human Checklist Before Any Future Decision

- The frozen MA150 rule remains unchanged.
- Strict OOS and no-lookahead constraints remain satisfied.
- The current decision remains `HOLD`.
- Paper trading remains `NOT READY`.
- No synthetic or hybrid QQQ5 is used.
- No DL, Bandit, Kelly, or complex strategy module is used for the monitor.
- No automated execution path has been added.
