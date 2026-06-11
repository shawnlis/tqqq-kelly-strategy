import numpy as np
import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _df(periods=12):
    idx = pd.date_range("2020-01-01", periods=periods, freq="B")
    qqq = np.array([100.0, 101.0, 99.0, 102.0, 104.0, 103.0, 105.0, 107.0, 106.0, 108.0, 109.0, 110.0])
    qqq = qqq[:periods]
    tqqq = 100.0 * np.cumprod(np.r_[1.0, 1.0 + 3.0 * pd.Series(qqq).pct_change().fillna(0.0).values[1:]])
    qqq5 = 100.0 * np.cumprod(np.r_[1.0, 1.0 + 5.0 * pd.Series(qqq).pct_change().fillna(0.0).values[1:]])
    df = pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq, "QQQ5": qqq5}, index=idx)
    df.attrs["price_meta"] = {
        "QQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "TQQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "QQQ5": {"source": "synthetic", "expense_embedded": True},
    }
    return df


def _df_with_future_extreme():
    df = _df()
    future_idx = pd.date_range(df.index[-1] + pd.offsets.BDay(1), periods=3, freq="B")
    future = pd.DataFrame(
        {
            "QQQ": [10.0, 8.0, 7.0],
            "TQQQ": [1.0, 0.8, 0.6],
            "QQQ5": [0.2, 0.1, 0.05],
        },
        index=future_idx,
    )
    out = pd.concat([df, future])
    out.attrs["price_meta"] = df.attrs["price_meta"]
    return out


def _common_kw():
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
        initial_L_base=3.0,
        disable_qqq5=True,
    )


def _slice_args(df):
    return dict(
        train_start=df.index[0],
        train_end=df.index[3],
        test_start=df.index[4],
        test_end=df.index[8],
    )


def test_strict_oos_baseline_ignores_rows_after_test_end(monkeypatch):
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    df = _df()
    df_future = _df_with_future_extreme()

    short = strategy.run_strict_oos_slice(df, mode="baseline", common_kw=_common_kw(), **_slice_args(df))
    extended = strategy.run_strict_oos_slice(df_future, mode="baseline", common_kw=_common_kw(), **_slice_args(df))

    pd.testing.assert_series_equal(short["equity_oos"], extended["equity_oos"])
    assert short["report_oos"]["CAGR"] == pytest.approx(extended["report_oos"]["CAGR"])
    assert short["report_oos"]["MaxDD"] == pytest.approx(extended["report_oos"]["MaxDD"])
    assert short["metadata"]["input_end_used"] == pd.Timestamp(df.index[8]).strftime("%Y-%m-%d")
    assert short["equity_oos"].iloc[0] == pytest.approx(1.0)


def test_strict_oos_dl_fallback_ignores_rows_after_test_end(monkeypatch):
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    df = _df()
    df_future = _df_with_future_extreme()

    short = strategy.run_strict_oos_slice(df, mode="dl", common_kw=_common_kw(), **_slice_args(df))
    extended = strategy.run_strict_oos_slice(df_future, mode="dl", common_kw=_common_kw(), **_slice_args(df))

    pd.testing.assert_series_equal(short["equity_oos"], extended["equity_oos"])
    assert short["report_oos"]["CAGR"] == pytest.approx(extended["report_oos"]["CAGR"])
    assert short["metadata"]["initial_train_end"] == pd.Timestamp(df.index[3]).strftime("%Y-%m-%d")


def test_strict_oos_helper_truncates_input_to_test_end(monkeypatch):
    df_future = _df_with_future_extreme()
    test_end = _df().index[8]
    captured = {}

    def fake_baseline_backtest(df, policy_mode="bandit", **kwargs):
        captured["max_index"] = df.index.max()
        eq = pd.Series(np.linspace(1.0, 1.1, len(df)), index=df.index, name="Equity")
        dd = eq / eq.cummax() - 1.0
        weights = pd.DataFrame(
            {"w_fut": 1.0, "w_tqqq": 0.0, "w_qqq5": 0.0, "L_base": 1.0, "kelly_frac": 1.0},
            index=df.index[1:],
        )
        trades = pd.DataFrame(columns=["From", "To", "Fraction", "TradeCost"])
        notes = pd.DataFrame({"Notes": ""}, index=df.index[1:])
        pos = pd.DataFrame(index=df.index)
        return eq, dd, {"CAGR": 0.0, "MaxDD": 0.0, "Sharpe_ex_rf0": 0.0}, weights, trades, eq, notes, pos, None, None

    monkeypatch.setattr(strategy, "baseline_backtest", fake_baseline_backtest)

    strategy.run_strict_oos_slice(
        df_future,
        train_start=df_future.index[0],
        train_end=df_future.index[3],
        test_start=df_future.index[4],
        test_end=test_end,
        mode="baseline",
        common_kw=_common_kw(),
    )

    assert captured["max_index"] == test_end


def test_strict_oos_dl_rejects_initial_train_end_after_train_end(monkeypatch):
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)
    df = _df()

    with pytest.raises(ValueError, match="initial_train_end"):
        strategy.run_strict_oos_slice(
            df,
            mode="dl",
            common_kw=_common_kw(),
            initial_train_end=df.index[4],
            **_slice_args(df),
        )


def test_strict_oos_dl_defaults_initial_train_end_to_train_end(monkeypatch):
    df = _df()
    captured = {}

    def fake_dl_backtest(df, policy_mode="bandit", initial_train_end=None, **kwargs):
        captured["initial_train_end"] = initial_train_end
        eq = pd.Series(np.linspace(1.0, 1.1, len(df)), index=df.index, name="Equity")
        dd = eq / eq.cummax() - 1.0
        weights = pd.DataFrame(
            {"w_fut": 1.0, "w_tqqq": 0.0, "w_qqq5": 0.0, "L_base": 1.0, "kelly_frac": 1.0},
            index=df.index[1:],
        )
        trades = pd.DataFrame(columns=["From", "To", "Fraction", "TradeCost"])
        notes = pd.DataFrame({"Notes": ""}, index=df.index[1:])
        pos = pd.DataFrame(index=df.index)
        return eq, dd, {"CAGR": 0.0, "MaxDD": 0.0, "Sharpe_ex_rf0": 0.0}, weights, trades, eq, notes, pos, None, None

    monkeypatch.setattr(strategy, "deep_learning_backtest", fake_dl_backtest)

    strategy.run_strict_oos_slice(df, mode="dl", common_kw=_common_kw(), **_slice_args(df))

    assert pd.Timestamp(captured["initial_train_end"]) == pd.Timestamp(df.index[3])


def test_strict_walk_forward_slices_are_independent_of_later_future(monkeypatch):
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    df = _df()
    df_future = _df_with_future_extreme()
    slices = [
        {
            "label": "s1",
            "train_start": df.index[0],
            "train_end": df.index[3],
            "test_start": df.index[4],
            "test_end": df.index[8],
        }
    ]

    short = strategy.run_strict_walk_forward(df, slices=slices, mode="baseline", common_kw=_common_kw())
    extended = strategy.run_strict_walk_forward(df_future, slices=slices, mode="baseline", common_kw=_common_kw())

    pd.testing.assert_frame_equal(short, extended)
