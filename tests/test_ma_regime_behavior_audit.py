import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_prices(periods=320, start="2018-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[80:120] = -0.012
    base_ret[120:190] = 0.006
    base_ret[230:260] = -0.01
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq_ret = np.clip(3.0 * base_ret, -0.95, 1.0)
    tqqq = 100.0 * np.cumprod(1.0 + tqqq_ret)
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def test_ma_signal_lags_one_day():
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
    assert weights.loc[idx[2], "raw_signal"] == 1.0
    assert weights.loc[idx[2], "w_tqqq"] == 0.0
    assert weights.loc[idx[3], "w_tqqq"] == 1.0


def test_confirmation_uses_only_past_and_current_closes():
    idx = pd.bdate_range("2020-01-01", periods=6)
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 99.0, 101.0, 102.0, 103.0, 104.0],
            "TQQQ": [100.0, 97.0, 103.0, 106.0, 109.0, 112.0],
        },
        index=idx,
    )
    res = m.run_ma_regime_backtest(
        df,
        {"ma_window": 2, "risk_off_asset": "qqq", "confirmation_days": 3},
    )
    weights = res["weights"]
    assert weights.loc[idx[3], "raw_signal"] == 0.0
    assert weights.loc[idx[4], "raw_signal"] == 1.0
    assert weights.loc[idx[4], "w_tqqq"] == 0.0
    assert weights.loc[idx[5], "w_tqqq"] == 1.0


def test_risk_off_qqq_and_cash_weights_are_correct():
    df = make_prices(periods=260)
    qqq = m.run_ma_regime_backtest(df, {"ma_window": 200, "risk_off_asset": "qqq"})
    cash = m.run_ma_regime_backtest(df, {"ma_window": 200, "risk_off_asset": "cash"})
    qqq_weights = qqq["weights"]
    cash_weights = cash["weights"]
    qqq_off = qqq_weights[qqq_weights["w_tqqq"] < 0.5]
    cash_off = cash_weights[cash_weights["w_tqqq"] < 0.5]
    assert not qqq_off.empty
    assert not cash_off.empty
    assert np.allclose(qqq_off["w_qqq"], 1.0)
    assert np.allclose(qqq_off["w_cash"], 0.0)
    assert np.allclose(cash_off["w_qqq"], 0.0)
    assert np.allclose(cash_off["w_cash"], 1.0)


def test_ma_variant_behavior_stats_are_reasonable():
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
    summary = m.run_ma_regime_variants(df, slices)
    assert len(summary["strategy"].unique()) == 5
    assert summary["time_in_risk_on"].between(0.0, 1.0).all()
    assert (summary["switches_per_year"] >= 0.0).all()
    assert {"Sharpe_DailyExcess", "CAGR_over_Vol", "worst_missed_up_month", "best_avoided_down_month"}.issubset(summary.columns)


def test_future_append_does_not_change_finished_ma_slice():
    df = make_prices(periods=320)
    test_end = df.index[250]
    slices = [
        {
            "slice": "closed",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[149].date()),
            "test_start": str(df.index[150].date()),
            "test_end": str(test_end.date()),
        }
    ]
    base = m.run_ma_regime_variants(df, slices).sort_values("strategy").reset_index(drop=True)
    future_idx = pd.bdate_range(test_end + pd.offsets.BDay(1), periods=30)
    future = pd.DataFrame(
        {"QQQ": np.linspace(1000.0, 5.0, len(future_idx)), "TQQQ": np.linspace(1000.0, 1.0, len(future_idx))},
        index=future_idx,
    )
    after = m.run_ma_regime_variants(pd.concat([df, future]), slices).sort_values("strategy").reset_index(drop=True)
    compare_cols = ["CAGR", "MaxDD", "Calmar", "Sharpe_DailyExcess", "Final_Equity", "time_in_risk_on"]
    pd.testing.assert_frame_equal(base[compare_cols], after[compare_cols])


def test_ma_behavior_cli_smoke_uses_local_csv_without_download(tmp_path):
    df = make_prices(periods=320).reset_index().rename(columns={"index": "Date"})
    qqq_csv = tmp_path / "qqq.csv"
    tqqq_csv = tmp_path / "tqqq.csv"
    df[["Date", "QQQ"]].rename(columns={"QQQ": "Close"}).to_csv(qqq_csv, index=False)
    df[["Date", "TQQQ"]].rename(columns={"TQQQ": "Close"}).to_csv(tqqq_csv, index=False)
    slices_csv = tmp_path / "slices.csv"
    pd.DataFrame(
        [
            {
                "slice": "toy",
                "train_start": str(pd.Timestamp(df["Date"].iloc[0]).date()),
                "train_end": str(pd.Timestamp(df["Date"].iloc[149]).date()),
                "test_start": str(pd.Timestamp(df["Date"].iloc[150]).date()),
                "test_end": str(pd.Timestamp(df["Date"].iloc[-1]).date()),
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
            str(pd.Timestamp(df["Date"].iloc[0]).date()),
            "--end",
            str(pd.Timestamp(df["Date"].iloc[-1]).date()),
            "--qqq_csv",
            str(qqq_csv),
            "--tqqq_csv",
            str(tqqq_csv),
            "--slices_csv",
            str(slices_csv),
            "--out_prefix",
            str(out_prefix),
            "--ma_behavior_audit",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "best MA variant" in proc.stdout
    assert (tmp_path / "ma_regime_variant_summary.csv").exists()
    report = tmp_path / "MA_REGIME_BEHAVIOR_AUDIT.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "Paper-trading status: NOT READY" in text
