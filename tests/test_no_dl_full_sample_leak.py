import pandas as pd
import pytest

import ib_paper_trader as trader
import qqq_deep_learning_and_baseline_experimental as strategy


def test_deep_learning_backtest_refuses_missing_initial_train_end_when_dl_available():
    if not strategy.DL_AVAILABLE:
        pytest.skip("DL_AVAILABLE is False in this environment.")

    df = pd.DataFrame(
        {
            "QQQ": [100.0, 101.0],
            "TQQQ": [100.0, 103.0],
            "QQQ5": [100.0, 105.0],
        },
        index=pd.date_range("2020-01-01", periods=2, freq="B"),
    )
    with pytest.raises(ValueError, match="initial_train_end is required for DL backtests"):
        strategy.deep_learning_backtest(df, initial_train_end=None)


def test_run_strategy_snapshot_defaults_initial_train_end():
    class FakeStrategyModule:
        captured_initial_train_end = None

        @staticmethod
        def load_prices(args):
            idx = pd.date_range("2020-01-01", periods=3, freq="B")
            df = pd.DataFrame(
                {
                    "QQQ": [100.0, 101.0, 102.0],
                    "TQQQ": [100.0, 103.0, 106.0],
                    "QQQ5": [100.0, 105.0, 110.0],
                },
                index=idx,
            )
            return df, False

        @staticmethod
        def fetch_rf_series_or_default(start, end):
            return None

        @staticmethod
        def fetch_vix_series_or_none(start, end):
            return None

        @staticmethod
        def fetch_tqqq_adv_series(start, end, lookback=20):
            return None

        @classmethod
        def deep_learning_backtest(cls, df, initial_train_end=None, **kwargs):
            cls.captured_initial_train_end = initial_train_end
            weights_df = pd.DataFrame(
                {
                    "w_fut": [1.0],
                    "w_tqqq": [0.0],
                    "w_qqq5": [0.0],
                    "L_base": [1.0],
                },
                index=[df.index[-1]],
            )
            return None, None, None, weights_df, None, None, None, None, None, None

    trader.run_strategy_snapshot(
        FakeStrategyModule,
        start_iso="2020-01-01",
        end_iso="2020-01-03",
        base_kelly_frac=0.5,
        target_vol=0.3,
        bandit_alpha=0.6,
        other={},
    )

    assert FakeStrategyModule.captured_initial_train_end == "2018-12-31"
