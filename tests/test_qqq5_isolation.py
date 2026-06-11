import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _toy_df(source="synthetic"):
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    df = pd.DataFrame(
        {
            "QQQ": [100.0, 90.0, 92.0, 94.0, 96.0],
            "TQQQ": [100.0, 70.0, 75.0, 80.0, 85.0],
            "QQQ5": [100.0, 50.0, 58.0, 66.0, 74.0],
        },
        index=idx,
    )
    df.attrs["price_meta"] = {
        "QQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "TQQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "QQQ5": {"source": source, "expense_embedded": True},
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
        target_vol=None,
        dl_max_qqq5=0.50,
        dl_max_tqqq=0.95,
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


def _patch_aggressive(monkeypatch):
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda *args, **kwargs: False)


def test_disable_qqq5_forces_zero_baseline_weight(monkeypatch):
    _patch_aggressive(monkeypatch)

    _, _, report, weights_df, trades_df, *_ = strategy.baseline_backtest(
        _toy_df("real_market"),
        disable_qqq5=True,
        **_common_kwargs(),
    )

    assert weights_df["w_qqq5"].max() == pytest.approx(0.0)
    assert report["QQQ5_Disabled"] is True
    assert report["QQQ5_Allowed"] is False
    assert report["QQQ5_HeadlineEligible"] is False
    assert report["QQQ5_Trades"] == 0
    assert not ((trades_df["To"] == "QQQ5").any() if len(trades_df) else False)


def test_synthetic_qqq5_is_disabled_by_default(monkeypatch):
    _patch_aggressive(monkeypatch)

    _, _, report, weights_df, *_ = strategy.baseline_backtest(
        _toy_df("synthetic"),
        allow_synthetic_qqq5=False,
        **_common_kwargs(),
    )

    assert weights_df["w_qqq5"].max() == pytest.approx(0.0)
    assert report["QQQ5_Source"] == "synthetic"
    assert report["QQQ5_Allowed"] is False
    assert report["QQQ5_HeadlineEligible"] is False


def test_hybrid_qqq5_is_disabled_by_default(monkeypatch):
    _patch_aggressive(monkeypatch)

    _, _, report, weights_df, *_ = strategy.baseline_backtest(
        _toy_df("hybrid"),
        allow_synthetic_qqq5=False,
        **_common_kwargs(),
    )

    assert weights_df["w_qqq5"].max() == pytest.approx(0.0)
    assert report["QQQ5_Source"] == "hybrid"
    assert report["QQQ5_Allowed"] is False
    assert report["QQQ5_HeadlineEligible"] is False


def test_synthetic_qqq5_can_be_research_allowed_but_not_headline(monkeypatch):
    _patch_aggressive(monkeypatch)

    _, _, report, _, *_ = strategy.baseline_backtest(
        _toy_df("synthetic"),
        allow_synthetic_qqq5=True,
        **_common_kwargs(),
    )

    assert report["QQQ5_Source"] == "synthetic"
    assert report["QQQ5_Allowed"] is True
    assert report["QQQ5_AllowSynthetic"] is True
    assert report["QQQ5_HeadlineEligible"] is False
    assert report["QQQ5_ResearchOnly"] is True


def test_real_market_qqq5_is_headline_eligible_when_not_disabled(monkeypatch):
    _patch_aggressive(monkeypatch)

    _, _, report, _, *_ = strategy.baseline_backtest(
        _toy_df("real_market"),
        allow_synthetic_qqq5=False,
        **_common_kwargs(),
    )

    assert report["QQQ5_Source"] == "real_market"
    assert report["QQQ5_Allowed"] is True
    assert report["QQQ5_HeadlineEligible"] is True


def test_dl_fallback_disables_synthetic_qqq5(monkeypatch):
    _patch_aggressive(monkeypatch)
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)
    df = _toy_df("synthetic")

    _, _, report, weights_df, *_ = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        allow_synthetic_qqq5=False,
        **_common_kwargs(),
    )

    assert weights_df["w_qqq5"].max() == pytest.approx(0.0)
    assert report["QQQ5_Source"] == "synthetic"
    assert report["QQQ5_Allowed"] is False
    assert report["QQQ5_HeadlineEligible"] is False
