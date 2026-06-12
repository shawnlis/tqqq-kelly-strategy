import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_monitor_prices(last_close=120.0, periods=170, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    qqq = pd.Series(100.0, index=idx)
    qqq.iloc[-1] = float(last_close)
    return pd.DataFrame({"QQQ": qqq, "TQQQ": qqq}, index=idx)


def write_price_csvs(df, tmp_path):
    qqq_csv = tmp_path / "qqq.csv"
    tqqq_csv = tmp_path / "tqqq.csv"
    pd.DataFrame({"Date": df.index, "Adj Close": df["QQQ"].values}).to_csv(qqq_csv, index=False)
    pd.DataFrame({"Date": df.index, "Adj Close": df["TQQQ"].values}).to_csv(tqqq_csv, index=False)
    return qqq_csv, tqqq_csv


def test_default_cli_does_not_write_history(tmp_path):
    df = make_monitor_prices()
    qqq_csv, tqqq_csv = write_price_csvs(df, tmp_path)
    history = tmp_path / "history.csv"
    monitor_out = tmp_path / "latest.md"

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
            "--monitor_history_csv",
            str(history),
        ]
    )

    assert rc == 0
    assert monitor_out.exists()
    assert not history.exists()


def test_explicit_append_creates_history_csv(tmp_path):
    df = make_monitor_prices()
    monitor = m.generate_frozen_ma150_monitor(df)
    history, appended = m.append_monitor_history(monitor, tmp_path / "history.csv", run_timestamp_local="2026-01-01T00:00:00")

    assert appended is True
    assert history.exists()
    rows = pd.read_csv(history)
    assert len(rows) == 1
    assert rows.loc[0, "asof_date"] == monitor["asof_date"]
    assert rows.loc[0, "monitoring_only"] == True


def test_same_asof_date_is_not_duplicated_by_default(tmp_path):
    df = make_monitor_prices()
    monitor = m.generate_frozen_ma150_monitor(df)
    history = tmp_path / "history.csv"

    _, first = m.append_monitor_history(monitor, history, run_timestamp_local="2026-01-01T00:00:00")
    _, second = m.append_monitor_history(monitor, history, run_timestamp_local="2026-01-01T00:01:00")
    rows = pd.read_csv(history)

    assert first is True
    assert second is False
    assert len(rows) == 1


def test_allow_duplicate_flag_allows_duplicate_asof(tmp_path):
    df = make_monitor_prices()
    monitor = m.generate_frozen_ma150_monitor(df)
    history = tmp_path / "history.csv"

    m.append_monitor_history(monitor, history, run_timestamp_local="2026-01-01T00:00:00")
    _, appended = m.append_monitor_history(
        monitor,
        history,
        allow_duplicate=True,
        run_timestamp_local="2026-01-01T00:01:00",
    )
    rows = pd.read_csv(history)

    assert appended is True
    assert len(rows) == 2


def test_history_fields_are_complete_and_not_execution_fields(tmp_path):
    df = make_monitor_prices()
    monitor = m.generate_frozen_ma150_monitor(df)
    history, _ = m.append_monitor_history(monitor, tmp_path / "history.csv")
    rows = pd.read_csv(history)

    assert list(rows.columns) == m.MONITOR_HISTORY_COLUMNS
    forbidden = {"account", "broker", "order", "shares", "notional", "position", "quantity", "qty"}
    lowered = {c.lower() for c in rows.columns}
    assert lowered.isdisjoint(forbidden)


def test_review_playbook_exists_and_contains_required_safety_text(tmp_path):
    playbook = m.write_monitor_review_playbook(tmp_path)
    text = playbook.read_text(encoding="utf-8")

    assert playbook.name == "MONITOR_REVIEW_PLAYBOOK.md"
    assert "monitoring only" in text.lower()
    assert "not a trade recommendation" in text.lower()
    assert "NOT READY" in text
    assert "Do not automatically place orders" in text
