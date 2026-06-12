from pathlib import Path

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_prices(periods=280, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[60:95] = -0.012
    base_ret[95:160] = 0.006
    base_ret[210:235] = -0.01
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq = 100.0 * np.cumprod(1.0 + np.clip(3.0 * base_ret, -0.95, 1.0))
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def make_slice(df):
    return [
        {
            "slice": "toy",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[119].date()),
            "test_start": str(df.index[120].date()),
            "test_end": str(df.index[-1].date()),
        }
    ]


def test_zero_bps_cost_matches_original_equity():
    df = make_prices()
    result = m.run_ma_regime_backtest(df, {"ma_window": 50, "risk_off_asset": "qqq"})
    oos_index = df.index[120:]
    original = m._normalize(result["equity"].reindex(oos_index))
    costed = m.apply_trade_cost_to_equity(df, result["weights"], cost_bps=0.0, execution_delay_days=0, oos_index=oos_index)
    pd.testing.assert_series_equal(original, costed["equity"], check_names=False, check_freq=False)


def test_final_equity_does_not_increase_as_cost_rises():
    df = make_prices()
    summary = m.run_cost_stress_suite(df, make_slice(df), cost_bps_list=[0, 5, 10, 25, 50])
    ma = summary[
        (summary["strategy"] == "MA150 risk-off QQQ")
        & (summary["execution_delay_days"] == 0)
    ].sort_values("cost_bps")
    assert list(ma["cost_bps"]) == [0.0, 5.0, 10.0, 25.0, 50.0]
    assert ma["Final_Equity"].is_monotonic_decreasing


def test_one_day_execution_delay_does_not_use_future_signal():
    idx = pd.bdate_range("2020-01-01", periods=5)
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 90.0, 120.0, 121.0, 122.0],
            "TQQQ": [100.0, 70.0, 90.0, 91.0, 92.0],
        },
        index=idx,
    )
    result = m.run_ma_regime_backtest(df, {"ma_window": 2, "risk_off_asset": "cash"})
    delayed = m.apply_trade_cost_to_equity(
        df,
        result["weights"],
        cost_bps=0.0,
        execution_delay_days=1,
        oos_index=idx,
    )
    weights = delayed["weights"]
    assert result["weights"].loc[idx[2], "raw_signal"] == 1.0
    assert result["weights"].loc[idx[3], "w_tqqq"] == 1.0
    assert weights.loc[idx[3], "w_tqqq"] == 0.0
    assert weights.loc[idx[4], "w_tqqq"] == 1.0


def test_buy_and_hold_blend_has_only_initial_trade_cost():
    df = make_prices()
    result = m.run_static_blend_backtest(df, 0.7, 0.3, 0.0)
    oos_index = df.index[120:]
    costed = m.apply_trade_cost_to_equity(df, result["weights"], cost_bps=10.0, oos_index=oos_index)
    stats = m.calculate_turnover_stats(costed["weights"], cost_bps=10.0)
    assert stats["trade_days"] == 1
    assert np.isclose(stats["total_turnover"], 1.0)
    assert costed["equity"].iloc[-1] < costed["gross_equity"].iloc[-1]


def test_turnover_stats_are_non_negative():
    df = make_prices()
    result = m.run_ma_regime_backtest(df, {"ma_window": 50, "risk_off_asset": "qqq"})
    stats = m.calculate_turnover_stats(result["weights"], cost_bps=25.0)
    assert stats["total_turnover"] >= 0.0
    assert stats["annualized_turnover"] >= 0.0
    assert stats["trade_days"] >= 0
    assert stats["total_cost_rate"] >= 0.0


def test_cost_stress_report_generation_smoke(tmp_path):
    df = make_prices()
    summary = m.run_cost_stress_suite(df, make_slice(df), cost_bps_list=[0, 10, 25])
    assert {"TQQQ buy-and-hold", "70/30 TQQQ/QQQ", "50/50 TQQQ/QQQ", "MA150 risk-off QQQ"}.issubset(
        set(summary["strategy"])
    )
    assert {"total_turnover", "final_cost_drag", "execution_delay_days"}.issubset(summary.columns)
    csv_path, report_path = m.write_cost_stress_outputs(summary, str(tmp_path / "minimal_tqqq"))
    assert csv_path == tmp_path / "cost_stress_summary.csv"
    assert report_path == tmp_path / "COST_STRESS_AUDIT.md"
    text = report_path.read_text(encoding="utf-8")
    assert "NOT READY FOR PAPER TRADING" in text
    assert "Static blends are buy-and-hold" in text
