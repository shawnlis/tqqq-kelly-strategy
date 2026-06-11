import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _toy_price_df():
    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 105.0, 110.0],
            "TQQQ": [100.0, 110.0, 121.0],
            "QQQ5": [100.0, 115.0, 132.25],
        },
        index=idx,
    )
    df.attrs["price_meta"] = {
        "QQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "TQQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "QQQ5": {"source": "synthetic", "expense_embedded": True},
    }
    return df


def _baseline_kwargs():
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
        tqqq_expense=0.252,
        qqq5_expense=0.252,
    )


def test_tqqq_buy_hold_benchmark_does_not_double_deduct_expense_by_default():
    _, _, report, *_ = strategy.baseline_backtest(_toy_price_df(), **_baseline_kwargs())

    assert report["TQQQ_Final_Equity"] == pytest.approx(1.21)
    assert report["TQQQ_Benchmark_Expense_Deducted"] is False
    assert report["TQQQ_Expense_Deducted_In_Returns"] is False
    assert report["TQQQ_Price_Expense_Embedded"] is True


def test_tqqq_buy_hold_benchmark_only_deducts_when_explicitly_requested():
    _, _, report, *_ = strategy.baseline_backtest(
        _toy_price_df(),
        deduct_tqqq_expense_in_returns=True,
        **_baseline_kwargs(),
    )

    assert report["TQQQ_Final_Equity"] < 1.21
    assert report["TQQQ_Benchmark_Expense_Deducted"] is True
    assert report["TQQQ_Expense_Deducted_In_Returns"] is True


def test_ma_crossover_tqqq_benchmark_does_not_double_deduct_expense_by_default():
    _, _, report, *_ = strategy.ma_crossover_backtest(
        _toy_price_df(),
        fast=1,
        slow=2,
        tqqq_expense=0.252,
        trade_cost_bps=0.0,
        slip_bps=0.0,
    )

    assert report["TQQQ_Final_Equity"] == pytest.approx(1.21)
    assert report["TQQQ_Benchmark_Expense_Deducted"] is False


def test_qqq5_expense_helper_only_deducts_when_explicitly_requested():
    raw = pd.Series([0.0, 0.15, 0.15], name="QQQ5")

    no_deduct = strategy.apply_expense_to_returns(raw, 0.252, False)
    deduct = strategy.apply_expense_to_returns(raw, 0.252, True)

    pd.testing.assert_series_equal(no_deduct, raw)
    assert deduct.iloc[1] == pytest.approx(0.15 - 0.252 / 252.0)
