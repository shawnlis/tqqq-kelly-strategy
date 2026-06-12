import json

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_monitor_prices(last_close=120.0, periods=170, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    qqq = np.full(periods, 100.0)
    qqq[-1] = float(last_close)
    tqqq = qqq.copy()
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def test_monitor_does_not_use_rows_after_asof_date():
    df = make_monitor_prices(periods=180)
    asof = df.index[160]
    future = df.copy()
    future.loc[future.index > asof, "QQQ"] = 1.0
    future.loc[future.index > asof, "TQQQ"] = 1.0

    truncated = df.loc[df.index <= asof]
    monitor_truncated = m.generate_frozen_ma150_monitor(truncated, asof_date=asof)
    monitor_future = m.generate_frozen_ma150_monitor(future, asof_date=asof)

    assert monitor_future == monitor_truncated


def test_qqq_above_ma150_targets_tqqq_next_session():
    df = make_monitor_prices(last_close=120.0)
    monitor = m.generate_frozen_ma150_monitor(df)
    assert monitor["signal_state"] == "risk_on"
    assert monitor["previous_signal_state"] == "risk_off"
    assert monitor["signal_changed"] is True
    assert monitor["target_asset_next_session"] == "TQQQ"


def test_qqq_below_ma150_targets_qqq_next_session():
    df = make_monitor_prices(last_close=80.0)
    monitor = m.generate_frozen_ma150_monitor(df)
    assert monitor["signal_state"] == "risk_off"
    assert monitor["target_asset_next_session"] == "QQQ"


def test_output_dict_contains_required_keys():
    df = make_monitor_prices()
    monitor = m.generate_frozen_ma150_monitor(df)
    required = {
        "asof_date",
        "latest_close",
        "ma150",
        "distance_to_ma_pct",
        "signal_state",
        "target_asset_next_session",
        "previous_signal_state",
        "signal_changed",
        "last_switch_date",
        "days_since_last_switch",
        "rule_name",
        "decision_status",
        "paper_trading_status",
        "notes",
    }
    assert required.issubset(monitor)
    assert monitor["decision_status"] == "HOLD"
    assert monitor["paper_trading_status"] == "NOT_READY"


def test_frozen_monitor_cli_report_generation_smoke(tmp_path):
    df = make_monitor_prices()
    qqq_csv = tmp_path / "qqq.csv"
    tqqq_csv = tmp_path / "tqqq.csv"
    pd.DataFrame({"Date": df.index, "Adj Close": df["QQQ"].values}).to_csv(qqq_csv, index=False)
    pd.DataFrame({"Date": df.index, "Adj Close": df["TQQQ"].values}).to_csv(tqqq_csv, index=False)

    monitor_out = tmp_path / "frozen_monitor_latest.md"
    rc = m.main(
        [
            "--start",
            "2024-01-01",
            "--end",
            "auto",
            "--qqq_csv",
            str(qqq_csv),
            "--tqqq_csv",
            str(tqqq_csv),
            "--frozen_monitor",
            "--monitor_out",
            str(monitor_out),
        ]
    )
    assert rc == 0
    spec_path = tmp_path / "FROZEN_MONITOR_SPEC.md"
    json_path = tmp_path / "frozen_monitor_latest.json"
    assert spec_path.exists()
    assert monitor_out.exists()
    assert json_path.exists()
    report_text = monitor_out.read_text(encoding="utf-8")
    spec_text = spec_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "Monitoring only, not trade instruction." in report_text
    assert "no automatic execution" in spec_text.lower()
    assert payload["target_asset_next_session"] == "TQQQ"
    assert payload["decision_status"] == "HOLD"
