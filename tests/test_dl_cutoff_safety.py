import numpy as np
import pandas as pd

import qqq_deep_learning_and_baseline_experimental as strategy


class _IdentityScaler:
    def transform(self, values):
        return values


def _long_df(n=1080):
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    base_ret = np.full(n, 0.0005)
    base_ret[0] = 0.0
    base_ret[::37] = -0.01
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq = 100.0 * np.cumprod(1.0 + 3.0 * base_ret)
    qqq5 = 100.0 * np.cumprod(1.0 + 5.0 * base_ret)
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq, "QQQ5": qqq5}, index=idx)


def _dl_kwargs(cutoff):
    return dict(
        initial_train_end=cutoff,
        rf_ann=0.0,
        base_kelly_frac=0.5,
        kelly_lookback_days=20,
        rebalance_every_days=1,
        fut_fin_spread=0.0,
        trade_cost_bps=0.0,
        policy_mode="none",
        target_vol=None,
        dl_conf=0.50,
        dl_max_qqq5=0.45,
        dl_max_tqqq=0.60,
        dl_trade_max_frac=0.20,
        slip_bps=0.0,
        tqqq_slip_bps=0.0,
        qqq5_slip_bps=0.0,
        impact_k=0.0,
        adv_daily_frac_cap=0.0,
        crash_mode="soft_scale",
    )


def test_dl_model_is_not_used_on_or_before_initial_train_end(monkeypatch):
    df = _long_df()
    cutoff_pos = 1035
    cutoff = df.index[cutoff_pos]
    predict_calls = {"count": 0}

    def fake_train(train_df, train_features, **kwargs):
        def fake_predict(values):
            predict_calls["count"] += 1
            return np.array([[0.01, 0.01, 0.98, 0.95]], dtype=np.float32)

        stats = {
            "thresholds": (-0.01, 0.01),
            "class_returns": [-0.02, 0.02, 0.08],
            "class_scores": [-1.0, 1.0, 3.0],
            "class_counts": [100, 100, 100],
        }
        return object(), _IdentityScaler(), fake_predict, stats

    monkeypatch.setattr(strategy, "DL_AVAILABLE", True)
    monkeypatch.setattr(strategy, "train_deep_learning_model", fake_train)
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda *args, **kwargs: False)

    result = strategy.deep_learning_backtest(df, **_dl_kwargs(cutoff))
    notes_df = result[6]

    pre_cutoff_notes = notes_df.loc[notes_df.index <= cutoff, "Notes"].astype(str)
    post_cutoff_notes = notes_df.loc[notes_df.index > cutoff, "Notes"].astype(str)

    assert not pre_cutoff_notes.str.contains("DL\\(action=").any()
    assert not pre_cutoff_notes.str.contains("crash_soft_scale").any()
    assert post_cutoff_notes.str.contains("DL\\(action=").any()
    assert predict_calls["count"] == int((notes_df.index > cutoff).sum())


def test_dl_training_windows_are_cut_before_future_label_buffer(monkeypatch):
    df = _long_df()
    cutoff_pos = 1035
    cutoff = df.index[cutoff_pos]
    label_buffer = strategy.dl_label_buffer_days()
    train_calls = []

    def fake_train(train_df, train_features, **kwargs):
        assert len(train_df) == len(train_features)
        train_calls.append(
            {
                "last_date": train_df.index[-1],
                "max_train_date": pd.Timestamp(kwargs["max_train_date"]),
                "len": len(train_df),
            }
        )

        def fake_predict(values):
            return np.array([[0.98, 0.01, 0.01, 0.01]], dtype=np.float32)

        stats = {
            "thresholds": (-0.01, 0.01),
            "class_returns": [-0.02, 0.02, 0.08],
            "class_scores": [-1.0, 1.0, 3.0],
            "class_counts": [100, 100, 100],
        }
        return object(), _IdentityScaler(), fake_predict, stats

    monkeypatch.setattr(strategy, "DL_AVAILABLE", True)
    monkeypatch.setattr(strategy, "train_deep_learning_model", fake_train)
    monkeypatch.setattr(strategy, "compute_regime_score", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(strategy, "risk_gate_signal", lambda *args, **kwargs: False)

    strategy.deep_learning_backtest(df, **_dl_kwargs(cutoff))

    assert len(train_calls) > 1
    initial_call = train_calls[0]
    assert initial_call["last_date"] == initial_call["max_train_date"]
    assert df.index.get_loc(initial_call["last_date"]) <= cutoff_pos - label_buffer

    for offset, call in enumerate(train_calls[1:]):
        current_pos = cutoff_pos + 1 + offset
        assert call["last_date"] == call["max_train_date"]
        assert df.index.get_loc(call["last_date"]) <= current_pos - label_buffer - 1
