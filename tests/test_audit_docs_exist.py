from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_strict_audit_docs_exist_and_contain_required_keywords():
    docs = [
        ROOT / "STRICT_BACKTEST_CHECKLIST.md",
        ROOT / "FINAL_AUDIT_REPORT.md",
        ROOT / "STRICT_AUDIT_COMMANDS.md",
    ]
    for path in docs:
        assert path.exists(), f"missing audit doc: {path.name}"

    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)
    for keyword in [
        "max_effective_leverage",
        "strict OOS",
        "TQQQ",
        "Sharpe_DailyExcess",
        "synthetic",
        "headline",
    ]:
        assert keyword in combined
