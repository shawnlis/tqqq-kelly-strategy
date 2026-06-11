import numpy as np
import pandas as pd
import pytest

import qqq_deep_learning_and_baseline_experimental as strategy


def _levered_toy_df():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    return pd.DataFrame(
        {
            "QQQ": [100.0, 110.0, 111.0, 112.0, 113.0],
            "TQQQ": [100.0, 130.0, 133.0, 136.0, 139.0],
            "QQQ5": [100.0, 150.0, 156.0, 162.0, 168.0],
        },
        index=idx,
    )


def _cap_kwargs():
    return dict(
        rf_ann=0.0,
        base_kelly_frac=1.0,
        kelly_lookback_days=2,
        rebalance_every_days=999,
        fut_fin_spread=0.0,
        trade_cost_bps=0.0,
        policy_mode="none",
        use_risk_gate=True,
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
        initial_L_base=6.0,
    )


def _patch_benign_signals(monkeypatch):
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda *args, **kwargs: False)


def test_effective_leverage_cap_helper_trims_to_cap_without_negative_weights():
    result = strategy.enforce_effective_leverage_cap(
        w_fut=1.0,
        w_tqqq=0.5,
        w_qqq5=0.5,
        L_base=6.0,
        max_effective_leverage=3.0,
    )

    assert result["cap_hit"] is True
    assert result["post_eff"] <= 3.0 + 1e-8
    assert result["w_fut"] >= 0.0
    assert result["w_tqqq"] >= 0.0
    assert result["w_qqq5"] >= 0.0
    assert result["w_fut"] + result["w_tqqq"] + result["w_qqq5"] <= 1.0 + 1e-8


def test_baseline_effective_leverage_cap_applies_to_next_day_targets(monkeypatch):
    _patch_benign_signals(monkeypatch)
    df = _levered_toy_df()

    equity, _, report, weights_df, _, _, notes_df, *_ = strategy.baseline_backtest(
        df,
        max_effective_leverage=3.0,
        **_cap_kwargs(),
    )

    assert equity.iloc[1] == pytest.approx(1.60)
    assert (weights_df["effective_leverage"] <= 3.0 + 1e-8).all()
    assert report["EffectiveLeverageCapEnabled"] is True
    assert report["EffectiveLeverageCap"] == pytest.approx(3.0)
    assert report["EffectiveLeverageCapHits"] >= 1
    assert report["MaxEffectiveLeverage"] <= 3.0 + 1e-8
    assert report["EffectiveLeverageCap_MaxViolation"] <= 1e-8
    assert notes_df["Notes"].astype(str).str.contains("effective_leverage_cap_hit").any()


def test_deep_learning_rule_fallback_effective_leverage_cap(monkeypatch):
    _patch_benign_signals(monkeypatch)
    monkeypatch.setattr(strategy, "DL_AVAILABLE", False)
    df = _levered_toy_df()

    equity, _, report, weights_df, *_ = strategy.deep_learning_backtest(
        df,
        initial_train_end=df.index[0],
        max_effective_leverage=3.0,
        **_cap_kwargs(),
    )

    assert equity.iloc[1] == pytest.approx(1.60)
    assert (weights_df["effective_leverage"] <= 3.0 + 1e-8).all()
    assert report["EffectiveLeverageCapEnabled"] is True
    assert report["EffectiveLeverageCapHits"] >= 1
    assert report["MaxEffectiveLeverage"] <= 3.0 + 1e-8


def test_no_cap_keeps_report_disabled(monkeypatch):
    _patch_benign_signals(monkeypatch)
    df = _levered_toy_df()

    _, _, report, weights_df, *_ = strategy.baseline_backtest(
        df,
        max_effective_leverage=None,
        **_cap_kwargs(),
    )

    assert report["EffectiveLeverageCapEnabled"] is False
    assert np.isnan(report["EffectiveLeverageCap"])
    assert "effective_leverage" in weights_df.columns
