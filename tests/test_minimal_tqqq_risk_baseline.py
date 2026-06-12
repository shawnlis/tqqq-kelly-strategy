import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import minimal_tqqq_risk_baseline as m


def make_prices(periods=260, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[60:90] = -0.01
    base_ret[90:130] = 0.004
    base_ret[180:200] = -0.015
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq_ret = np.clip(3.0 * base_ret, -0.95, 1.0)
    tqqq = 100.0 * np.cumprod(1.0 + tqqq_ret)
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def test_static_blend_weights_sum_to_one():
    df = make_prices()
    res = m.run_static_blend_backtest(df, 0.7, 0.3, 0.0)
    weights = res["weights"]
    assert np.allclose(weights[["w_tqqq", "w_qqq", "w_cash"]].sum(axis=1), 1.0)
    assert weights["effective_leverage"].max() <= 3.0 + 1e-12
    with pytest.raises(ValueError):
        m.run_static_blend_backtest(df, 0.7, 0.2, 0.0)


def test_strategies_do_not_use_qqq5():
    df = make_prices()
    df_with_qqq5 = df.copy()
    df_with_qqq5["QQQ5"] = np.linspace(1.0, 1000.0, len(df_with_qqq5))
    res_plain = m.run_static_blend_backtest(df, 0.5, 0.5, 0.0)
    res_qqq5 = m.run_static_blend_backtest(df_with_qqq5, 0.5, 0.5, 0.0)
    assert "w_qqq5" not in res_qqq5["weights"].columns
    assert res_qqq5["report"]["QQQ5_Used"] is False
    pd.testing.assert_series_equal(res_plain["equity"], res_qqq5["equity"])


def test_ma_signal_is_shifted_one_day():
    idx = pd.bdate_range("2020-01-01", periods=4)
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 90.0, 120.0, 121.0],
            "TQQQ": [100.0, 70.0, 90.0, 91.0],
        },
        index=idx,
    )
    res = m.run_ma_regime_backtest(df, {"ma_window": 2, "risk_off_asset": "cash"})
    weights = res["weights"]
    assert weights.loc[idx[1], "raw_signal"] == 0.0
    assert weights.loc[idx[2], "raw_signal"] == 1.0
    assert weights.loc[idx[2], "w_tqqq"] == 0.0
    assert weights.loc[idx[3], "w_tqqq"] == 1.0


def test_vol_target_weight_and_effective_leverage_caps():
    df = make_prices()
    res = m.run_vol_target_backtest(df, target_vol=10.0, vol_window=20)
    weights = res["weights"]
    assert weights["w_tqqq"].max() <= 1.0 + 1e-12
    assert weights["w_tqqq"].min() >= -1e-12
    assert weights["effective_leverage"].max() <= 3.0 + 1e-12


def test_suite_metrics_fields_present():
    df = make_prices()
    slices = [
        {
            "slice": "toy",
            "train_start": "2019-01-01",
            "train_end": "2019-06-30",
            "test_start": "2019-07-01",
            "test_end": "2019-12-31",
        }
    ]
    summary = m.run_minimal_baseline_suite(df, slices)
    assert {"Sharpe_DailyExcess", "CAGR_over_Vol", "MaxEffectiveLeverage"}.issubset(summary.columns)
    assert summary["MaxEffectiveLeverage"].max() <= 3.0 + 1e-12


def test_future_append_does_not_change_finished_oos_slice():
    df = make_prices(periods=260, start="2019-01-01")
    test_end = df.index[180]
    slices = [
        {
            "slice": "closed",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[99].date()),
            "test_start": str(df.index[100].date()),
            "test_end": str(test_end.date()),
        }
    ]
    base = m.run_minimal_baseline_suite(df, slices).sort_values("strategy").reset_index(drop=True)
    future_idx = pd.bdate_range(test_end + pd.offsets.BDay(1), periods=20)
    future = pd.DataFrame(
        {"QQQ": np.linspace(1000.0, 10.0, len(future_idx)), "TQQQ": np.linspace(1000.0, 1.0, len(future_idx))},
        index=future_idx,
    )
    appended = pd.concat([df, future])
    after = m.run_minimal_baseline_suite(appended, slices).sort_values("strategy").reset_index(drop=True)
    pd.testing.assert_series_equal(base["Final_Equity"], after["Final_Equity"], check_names=False)
    pd.testing.assert_series_equal(base["CAGR"], after["CAGR"], check_names=False)


def test_cli_smoke_uses_local_csv_without_download(tmp_path):
    df = make_prices(periods=260, start="2019-01-01").reset_index().rename(columns={"index": "Date"})
    qqq_csv = tmp_path / "qqq.csv"
    tqqq_csv = tmp_path / "tqqq.csv"
    df[["Date", "QQQ"]].rename(columns={"QQQ": "Close"}).to_csv(qqq_csv, index=False)
    df[["Date", "TQQQ"]].rename(columns={"TQQQ": "Close"}).to_csv(tqqq_csv, index=False)
    slices_csv = tmp_path / "slices.csv"
    pd.DataFrame(
        [
            {
                "slice": "toy",
                "train_start": "2019-01-01",
                "train_end": "2019-06-30",
                "test_start": "2019-07-01",
                "test_end": "2019-12-31",
            }
        ]
    ).to_csv(slices_csv, index=False)
    out_prefix = tmp_path / "minimal_tqqq"
    script = Path(__file__).resolve().parents[1] / "minimal_tqqq_risk_baseline.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--start",
            "2019-01-01",
            "--end",
            "2020-01-01",
            "--qqq_csv",
            str(qqq_csv),
            "--tqqq_csv",
            str(tqqq_csv),
            "--slices_csv",
            str(slices_csv),
            "--out_prefix",
            str(out_prefix),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "best composite strategy" in proc.stdout
    assert Path(f"{out_prefix}_summary.csv").exists()
    assert (tmp_path / "MINIMAL_BASELINE_REPORT.md").exists()
