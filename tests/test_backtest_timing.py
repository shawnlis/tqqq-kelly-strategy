import numpy as np
import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _gap_down_df():
    idx = pd.date_range("2020-01-01", periods=4, freq="B")
    return pd.DataFrame(
        {
            "QQQ": [100.0, 90.0, 91.0, 92.0],
            "TQQQ": [100.0, 70.0, 72.0, 74.0],
            "QQQ5": [100.0, 50.0, 52.0, 54.0],
        },
        index=idx,
    )


def _gap_down_df_with_future_extreme():
    idx = pd.date_range("2020-01-01", periods=6, freq="B")
    return pd.DataFrame(
        {
            "QQQ": [100.0, 90.0, 91.0, 92.0, 5.0, 4.0],
            "TQQQ": [100.0, 70.0, 72.0, 74.0, 1.0, 0.5],
            "QQQ5": [100.0, 50.0, 52.0, 54.0, 0.25, 0.1],
        },
        index=idx,
    )


def _strict_kwargs():
    return dict(
        rf_ann=0.0,
        base_kelly_frac=1.0,
        kelly_lookback_days=2,
        rebalance_every_days=1,
        fut_fin_spread=0.0,
        trade_cost_bps=0.0,
        policy_mode="none",
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
        initial_L_base=3.0,
    )


def test_baseline_day1_risk_gate_cannot_reduce_same_day_loss(monkeypatch):
    df = _gap_down_df()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)

    equity, _, _, weights_df, *_ = strategy.baseline_backtest(
        df,
        use_risk_gate=True,
        **_strict_kwargs(),
    )

    assert equity.iloc[1] == pytest.approx(0.70)
    assert weights_df.attrs["timing"].startswith("Rows are end-of-day target weights")


def test_baseline_day1_risk_gate_only_affects_day2(monkeypatch):
    df = _gap_down_df()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)

    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: False)
    no_gate_equity = strategy.baseline_backtest(df, use_risk_gate=True, **_strict_kwargs())[0]

    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)
    day1_gate_equity = strategy.baseline_backtest(df, use_risk_gate=True, **_strict_kwargs())[0]

    assert day1_gate_equity.iloc[1] == pytest.approx(no_gate_equity.iloc[1])
    assert day1_gate_equity.iloc[2] != pytest.approx(no_gate_equity.iloc[2])


def test_baseline_future_rows_do_not_change_day1_day2(monkeypatch):
    df = _gap_down_df()
    df_with_future = _gap_down_df_with_future_extreme()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)

    short_equity = strategy.baseline_backtest(df, use_risk_gate=True, **_strict_kwargs())[0]
    extended_equity = strategy.baseline_backtest(df_with_future, use_risk_gate=True, **_strict_kwargs())[0]

    assert np.allclose(short_equity.iloc[:3].values, extended_equity.iloc[:3].values)


def test_deep_learning_rule_fallback_day1_risk_gate_cannot_reduce_same_day_loss(monkeypatch):
    df = _gap_down_df()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)

    equity, _, _, weights_df, *_ = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        use_risk_gate=True,
        **_strict_kwargs(),
    )

    assert equity.iloc[1] == pytest.approx(0.70)
    assert weights_df.attrs["timing"].startswith("Rows are end-of-day target weights")


def test_deep_learning_rule_fallback_day1_risk_gate_only_affects_day2(monkeypatch):
    df = _gap_down_df()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)

    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: False)
    no_gate_equity = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        use_risk_gate=True,
        **_strict_kwargs(),
    )[0]

    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)
    day1_gate_equity = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        use_risk_gate=True,
        **_strict_kwargs(),
    )[0]

    assert day1_gate_equity.iloc[1] == pytest.approx(no_gate_equity.iloc[1])
    assert day1_gate_equity.iloc[2] != pytest.approx(no_gate_equity.iloc[2])


def test_deep_learning_future_rows_do_not_change_day1_day2(monkeypatch):
    df = _gap_down_df()
    df_with_future = _gap_down_df_with_future_extreme()
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda i, *args, **kwargs: i == 1)
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)

    short_equity = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        use_risk_gate=True,
        **_strict_kwargs(),
    )[0]
    extended_equity = strategy.deep_learning_backtest(
        df_with_future,
        initial_train_end=df_with_future.index[0],
        use_risk_gate=True,
        **_strict_kwargs(),
    )[0]

    assert np.allclose(short_equity.iloc[:3].values, extended_equity.iloc[:3].values)
