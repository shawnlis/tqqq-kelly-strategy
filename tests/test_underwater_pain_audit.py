from pathlib import Path

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_prices(periods=320, start="2018-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[80:125] = -0.012
    base_ret[125:210] = 0.005
    base_ret[240:270] = -0.01
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq_ret = np.clip(3.0 * base_ret, -0.95, 1.0)
    tqqq = 100.0 * np.cumprod(1.0 + tqqq_ret)
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def test_underwater_duration_calculation():
    idx = pd.bdate_range("2020-01-01", periods=7)
    equity = pd.Series([1.0, 0.9, 0.8, 0.95, 1.01, 0.99, 1.02], index=idx)
    metrics = m.compute_underwater_pain_metrics(equity)
    assert np.isclose(metrics["MaxDD"], -0.2)
    assert metrics["LongestUnderwaterDays"] == 3
    assert metrics["AverageUnderwaterDays"] == 2.0
    assert metrics["TimeToRecoveryAfterMaxDDDays"] == 2.0
    assert metrics["MaxDDRecovered"] is True


def test_ulcer_and_pain_index_calculation():
    idx = pd.bdate_range("2020-01-01", periods=5)
    equity = pd.Series([1.0, 0.9, 0.8, 0.95, 1.0], index=idx)
    metrics = m.compute_underwater_pain_metrics(equity)
    expected_ulcer = np.sqrt(np.mean(np.array([0.0, -0.1, -0.2, -0.05, 0.0]) ** 2))
    expected_pain = np.mean(np.array([0.0, 0.1, 0.2, 0.05, 0.0]))
    assert np.isclose(metrics["UlcerIndex"], expected_ulcer)
    assert np.isclose(metrics["PainIndex"], expected_pain)


def test_worst_rolling_return_calculation():
    idx = pd.bdate_range("2020-01-01", periods=5)
    equity = pd.Series([1.0, 0.9, 0.81, 1.0, 0.5], index=idx)
    expected = min(0.81 / 1.0 - 1.0, 1.0 / 0.9 - 1.0, 0.5 / 0.81 - 1.0)
    assert np.isclose(m._worst_rolling_return(equity, 2), expected)


def test_monotonic_equity_has_no_underwater_days():
    idx = pd.bdate_range("2020-01-01", periods=30)
    equity = pd.Series(np.linspace(1.0, 2.0, len(idx)), index=idx)
    metrics = m.compute_underwater_pain_metrics(equity)
    assert metrics["MaxDD"] == 0.0
    assert metrics["LongestUnderwaterDays"] == 0
    assert metrics["AverageUnderwaterDays"] == 0.0
    assert metrics["PainIndex"] == 0.0
    assert metrics["UlcerIndex"] == 0.0
    assert metrics["PctTimeBelowPreviousHigh"] == 0.0


def test_underwater_report_generation_smoke(tmp_path):
    df = make_prices()
    slices = [
        {
            "slice": "toy",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[149].date()),
            "test_start": str(df.index[150].date()),
            "test_end": str(df.index[-1].date()),
        }
    ]
    results = m.run_underwater_pain_audit(df, slices)
    assert {"MA150 risk-off QQQ", "TQQQ buy-and-hold", "70/30 TQQQ/QQQ", "50/50 TQQQ/QQQ"}.issubset(
        set(results["strategy"])
    )
    assert {"UlcerIndex", "PainIndex", "Worst1MReturn", "BestMissed1MReturnVsTQQQ"}.issubset(results.columns)

    csv_path, report_path = m.write_underwater_pain_outputs(results, str(tmp_path / "minimal_tqqq"))
    assert csv_path == tmp_path / "underwater_pain_summary.csv"
    assert report_path == tmp_path / "UNDERWATER_PAIN_AUDIT.md"
    assert csv_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "Paper-Trading Readiness" in text
    assert "NOT READY" in text
