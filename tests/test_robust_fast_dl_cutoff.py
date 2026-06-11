import pandas as pd

import robust_fast


class _FakeStrategyModule:
    captured_kwargs = None

    @staticmethod
    def baseline_backtest(**kwargs):
        _FakeStrategyModule.captured_kwargs = kwargs
        idx = kwargs["df"].index
        return pd.Series([1.0] * len(idx), index=idx), None, {"CAGR": 0.0}

    @staticmethod
    def deep_learning_backtest(**kwargs):
        _FakeStrategyModule.captured_kwargs = kwargs
        idx = kwargs["df"].index
        return pd.Series([1.0] * len(idx), index=idx), None, {"CAGR": 0.0}


def _df():
    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    return pd.DataFrame({"QQQ": [1, 2, 3], "TQQQ": [1, 2, 3], "QQQ5": [1, 2, 3]}, index=idx)


def test_run_backtest_dl_requires_initial_train_end():
    eq, rep, err = robust_fast.run_backtest(_FakeStrategyModule, _df(), {}, {}, "dl", timeout_seconds=0)

    assert eq is None
    assert rep is None
    assert "initial_train_end" in err


def test_run_backtest_dl_passes_initial_train_end():
    eq, rep, err = robust_fast.run_backtest(
        _FakeStrategyModule,
        _df(),
        {"initial_train_end": "2018-12-31"},
        {},
        "dl",
        timeout_seconds=0,
    )

    assert err is None
    assert rep == {"CAGR": 0.0}
    assert _FakeStrategyModule.captured_kwargs["initial_train_end"] == "2018-12-31"


def test_run_backtest_baseline_drops_dl_only_initial_train_end():
    eq, rep, err = robust_fast.run_backtest(
        _FakeStrategyModule,
        _df(),
        {"initial_train_end": "2018-12-31"},
        {},
        "baseline",
        timeout_seconds=0,
    )

    assert err is None
    assert rep == {"CAGR": 0.0}
    assert "initial_train_end" not in _FakeStrategyModule.captured_kwargs


def test_oos_span_common_kw_uses_span_start_as_initial_train_end():
    common_kw = {"initial_train_end": "2018-12-31", "rf_ann": 0.02}

    span_kw = robust_fast.common_kw_for_oos_span(common_kw, "2021-06-30")

    assert span_kw["initial_train_end"] == "2021-06-30"
    assert span_kw["rf_ann"] == 0.02
    assert common_kw["initial_train_end"] == "2018-12-31"
