from pathlib import Path

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_prices(periods=320, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[70:115] = -0.011
    base_ret[115:190] = 0.005
    base_ret[245:275] = -0.008
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq = 100.0 * np.cumprod(1.0 + np.clip(3.0 * base_ret, -0.95, 1.0))
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def make_slice(df):
    return [
        {
            "slice": "toy",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[149].date()),
            "test_start": str(df.index[150].date()),
            "test_end": str(df.index[-1].date()),
        }
    ]


def test_buy_and_hold_turnover_is_initial_only():
    df = make_prices()
    result = m.run_static_blend_backtest(df, 1.0, 0.0, 0.0)
    stats = m.calculate_turnover_tax_event_proxy(result["weights"])
    assert stats["trade_days"] == 1
    assert stats["ongoing_trade_days"] == 0
    assert stats["number_sell_events"] == 0
    assert np.isclose(stats["total_turnover"], 1.0)
    assert stats["percentage_years_with_no_trades"] == 1.0


def test_alternating_signal_turnover_exceeds_buy_and_hold():
    idx = pd.bdate_range("2020-01-01", periods=20)
    static = m._weights_frame(idx, 1.0, 0.0, 0.0, "static")
    alternating_tqqq = pd.Series([1.0 if i % 2 == 0 else 0.0 for i in range(len(idx))], index=idx)
    alternating_cash = 1.0 - alternating_tqqq
    alternating = m._weights_frame(idx, alternating_tqqq, 0.0, alternating_cash, "alternating")

    static_stats = m.calculate_turnover_tax_event_proxy(static)
    alt_stats = m.calculate_turnover_tax_event_proxy(alternating)

    assert alt_stats["total_turnover"] > static_stats["total_turnover"]
    assert alt_stats["number_sell_events"] > static_stats["number_sell_events"]
    assert alt_stats["average_holding_period_proxy_days"] < static_stats["average_holding_period_proxy_days"]


def test_taxable_event_proxy_is_non_negative():
    df = make_prices()
    result = m.run_ma_regime_backtest(df, {"ma_window": 50, "risk_off_asset": "qqq"})
    stats = m.calculate_turnover_tax_event_proxy(result["weights"])
    assert stats["estimated_taxable_events_proxy"] >= 0
    assert stats["taxable_sell_notional_proxy"] >= 0.0
    assert stats["short_holding_risk_proxy"] >= 0.0


def test_annual_turnover_aggregation_is_correct():
    idx = pd.to_datetime(["2020-12-30", "2020-12-31", "2021-01-04", "2021-01-05"])
    weights = m._weights_frame(
        idx,
        pd.Series([1.0, 0.0, 0.0, 1.0], index=idx),
        pd.Series([0.0, 1.0, 1.0, 0.0], index=idx),
        0.0,
        "manual",
    )
    stats = m.calculate_turnover_tax_event_proxy(weights)
    assert np.isclose(stats["annual_turnover_by_year"]["2020"], 3.0)
    assert np.isclose(stats["annual_turnover_by_year"]["2021"], 2.0)
    assert stats["worst_calendar_year"] == "2020"
    assert stats["number_sell_events"] == 2


def test_operational_report_generation_smoke(tmp_path):
    df = make_prices()
    results = m.run_operational_feasibility_audit(df, make_slice(df))
    assert {"TQQQ buy-and-hold", "70/30 TQQQ/QQQ", "50/50 TQQQ/QQQ", "MA150 risk-off QQQ"}.issubset(
        set(results["strategy"])
    )
    required = {
        "trades_per_year",
        "switches_per_year",
        "estimated_taxable_events_proxy",
        "taxable_sell_notional_proxy",
        "operational_burden_score",
    }
    assert required.issubset(results.columns)
    csv_path, report_path = m.write_operational_feasibility_outputs(results, str(tmp_path / "minimal_tqqq"))
    assert csv_path == tmp_path / "operational_feasibility_summary.csv"
    assert report_path == tmp_path / "OPERATIONAL_FEASIBILITY_AUDIT.md"
    text = report_path.read_text(encoding="utf-8")
    assert "NOT READY FOR PAPER TRADING" in text
    assert "Tax treatment depends on jurisdiction/account type" in text
    assert "Static blends are modeled as buy-and-hold" in text
