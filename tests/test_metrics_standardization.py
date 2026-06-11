import math

import numpy as np
import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _equity_from_returns(returns, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(returns) + 1, freq="B")
    eq = pd.Series([1.0] + list(np.cumprod(1.0 + np.asarray(returns, dtype=float))), index=idx)
    eq.name = "Equity"
    return eq


def _toy_df():
    idx = pd.date_range("2020-01-01", periods=8, freq="B")
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 101.0, 100.5, 102.0, 103.0, 102.5, 104.0, 105.0],
            "TQQQ": [100.0, 103.0, 101.5, 106.0, 109.0, 107.5, 112.0, 115.0],
            "QQQ5": [100.0, 105.0, 102.0, 110.0, 116.0, 113.0, 121.0, 126.0],
        },
        index=idx,
    )
    df.attrs["price_meta"] = {
        "QQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "TQQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "QQQ5": {"source": "synthetic", "expense_embedded": True},
    }
    return df


def _common_kwargs():
    return dict(
        rf_ann=0.0,
        base_kelly_frac=1.0,
        kelly_lookback_days=2,
        rebalance_every_days=1,
        fut_fin_spread=0.0,
        trade_cost_bps=0.0,
        policy_mode="none",
        use_risk_gate=False,
        target_vol=None,
        dl_max_qqq5=0.0,
        dl_max_tqqq=0.0,
        dl_trade_max_frac=0.0,
        slip_bps=0.0,
        tqqq_slip_bps=0.0,
        qqq5_slip_bps=0.0,
        impact_k=0.0,
        adv_daily_frac_cap=0.0,
        crash_mode="none",
        initial_w_fut=1.0,
        initial_w_tqqq=0.0,
        initial_w_qqq5=0.0,
        initial_L_base=1.0,
        disable_qqq5=True,
    )


def _assert_metric_fields(report):
    for key in [
        "CAGR",
        "AnnVol",
        "Sharpe_DailyExcess",
        "CAGR_over_Vol",
        "Sortino_DailyExcess",
        "MaxDD",
        "Calmar",
        "Final_Equity",
        "Beta_vs_Benchmark",
        "Alpha_vs_Benchmark_Ann",
        "TrackingError_vs_Benchmark",
        "InformationRatio_vs_Benchmark",
        "Benchmark_CAGR",
        "Benchmark_AnnVol",
        "Benchmark_Sharpe_DailyExcess",
        "Benchmark_MaxDD",
        "Benchmark_Final_Equity",
        "Metric_Definition_Version",
        "Vol",
        "Sharpe_ex_rf0",
        "TQQQ_CAGR",
        "TQQQ_Vol",
        "TQQQ_Sharpe",
        "TQQQ_MaxDD",
        "TQQQ_Final_Equity",
    ]:
        assert key in report
    assert report["Metric_Definition_Version"] == "v2_standard_daily_excess"


def test_monotonic_up_equity_metrics():
    equity = pd.Series([1.0, 1.01, 1.02, 1.03], index=pd.date_range("2020-01-01", periods=4, freq="B"))

    metrics = strategy.compute_performance_metrics(equity)

    assert metrics["MaxDD"] == pytest.approx(0.0)
    assert metrics["Final_Equity"] == pytest.approx(1.03)
    assert metrics["CAGR"] > 0.0


def test_fixed_daily_returns_standard_sharpe():
    daily_returns = pd.Series(
        [0.01, 0.02, -0.005, 0.0],
        index=pd.date_range("2020-01-02", periods=4, freq="B"),
    )
    equity = _equity_from_returns(daily_returns.values)
    excess = daily_returns - 0.01 / 252.0
    expected = excess.mean() / excess.std(ddof=1) * np.sqrt(252.0)

    metrics = strategy.compute_performance_metrics(equity, daily_returns=daily_returns, rf_ann=0.01)

    assert metrics["Sharpe_DailyExcess"] == pytest.approx(expected)
    assert metrics["Sharpe_ex_rf0"] == pytest.approx(metrics["Sharpe_DailyExcess"])


def test_identical_strategy_and_benchmark_metrics():
    returns = pd.Series([0.01, -0.005, 0.003, 0.012, -0.002], index=pd.date_range("2020-01-02", periods=5, freq="B"))
    equity = _equity_from_returns(returns.values)

    metrics = strategy.compute_performance_metrics(equity, benchmark_equity=equity.copy())

    assert metrics["Beta_vs_Benchmark"] == pytest.approx(1.0)
    assert metrics["Alpha_vs_Benchmark_Ann"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["TrackingError_vs_Benchmark"] == pytest.approx(0.0)
    assert not math.isinf(metrics["InformationRatio_vs_Benchmark"])


def test_double_beta_strategy_metrics():
    benchmark_returns = pd.Series(
        [0.01, -0.005, 0.003, 0.012, -0.002],
        index=pd.date_range("2020-01-02", periods=5, freq="B"),
    )
    strategy_returns = 2.0 * benchmark_returns
    equity = _equity_from_returns(strategy_returns.values)
    benchmark = _equity_from_returns(benchmark_returns.values)

    metrics = strategy.compute_performance_metrics(equity, benchmark_equity=benchmark)

    assert metrics["Beta_vs_Benchmark"] == pytest.approx(2.0)
    assert metrics["TrackingError_vs_Benchmark"] > 0.0
    assert np.isfinite(metrics["Alpha_vs_Benchmark_Ann"])


def test_sortino_downside_zero_returns_nan_not_inf():
    returns = pd.Series([0.01, 0.02, 0.03], index=pd.date_range("2020-01-02", periods=3, freq="B"))
    equity = _equity_from_returns(returns.values)

    metrics = strategy.compute_performance_metrics(equity, daily_returns=returns)

    assert np.isnan(metrics["Sortino_DailyExcess"])
    assert not math.isinf(metrics["Sortino_DailyExcess"])


def test_backtest_reports_include_standard_metrics(monkeypatch):
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    df = _toy_df()

    _, _, baseline_report, *_ = strategy.baseline_backtest(df, **_common_kwargs())
    _assert_metric_fields(baseline_report)

    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)
    _, _, dl_report, *_ = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[1],
        **_common_kwargs(),
    )
    _assert_metric_fields(dl_report)

    _, _, ma_report, *_ = strategy.ma_crossover_backtest(
        df,
        fast=2,
        slow=3,
        trade_cost_bps=0.0,
        slip_bps=0.0,
    )
    _assert_metric_fields(ma_report)

    strict = strategy.run_strict_oos_slice(
        df,
        train_start=df.index[0],
        train_end=df.index[2],
        test_start=df.index[3],
        test_end=df.index[-1],
        mode="baseline",
        common_kw=_common_kwargs(),
    )
    _assert_metric_fields(strict["report_oos"])
