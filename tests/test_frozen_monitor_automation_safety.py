from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_TEMPLATE = ROOT / "automation" / "run_frozen_monitor.ps1.template"
TASK_TEMPLATE = ROOT / "automation" / "create_frozen_monitor_task.ps1.template"
AUTOMATION_DOC = ROOT / "automation" / "FROZEN_MONITOR_AUTOMATION.md"
GITIGNORE = ROOT / ".gitignore"


def test_automation_templates_and_doc_exist():
    assert RUNNER_TEMPLATE.exists()
    assert TASK_TEMPLATE.exists()
    assert AUTOMATION_DOC.exists()


def test_runner_template_contains_monitor_history_flags():
    text = RUNNER_TEMPLATE.read_text(encoding="utf-8")

    assert "--frozen_monitor" in text
    assert "--append_monitor_history" in text
    assert "--monitor_history_csv" in text
    assert "logs" in text
    assert "frozen_monitor_" in text


def test_runner_template_has_no_dangerous_execution_semantics():
    text = RUNNER_TEMPLATE.read_text(encoding="utf-8").lower()
    forbidden = [
        "ibkr",
        "moomoo",
        "telegram",
        "placeorder",
        "broker account",
        "submit order",
        "order file",
        "shares",
        "notional",
        "trade instruction",
    ]

    for term in forbidden:
        assert term not in text


def test_task_template_is_disabled_by_default_and_does_not_run_immediately():
    text = TASK_TEMPLATE.read_text(encoding="utf-8")

    assert "Disable-ScheduledTask" in text
    assert "EnableAfterCreate" in text
    assert "Start-ScheduledTask" not in text
    assert "run_frozen_monitor.ps1" in text


def test_automation_doc_contains_required_safety_text():
    text = AUTOMATION_DOC.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "monitoring only" in lowered
    assert "not a trade instruction" in lowered
    assert "not paper trading" in lowered
    assert "no broker connection" in lowered
    assert "no order generation" in lowered
    assert "NOT READY" in text
    assert "signal_changed=True" in text


def test_gitignore_ignores_logs_and_real_ps1_but_allows_templates():
    text = GITIGNORE.read_text(encoding="utf-8").splitlines()

    assert "logs/" in text
    assert "automation/*.ps1" in text
    assert "!automation/*.ps1.template" in text
