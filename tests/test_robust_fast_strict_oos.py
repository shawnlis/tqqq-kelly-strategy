from types import SimpleNamespace

import pandas as pd

import robust_fast


def _df():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    return pd.DataFrame(
        {
            "QQQ": [100.0, 101.0, 102.0, 103.0, 104.0],
            "TQQQ": [100.0, 103.0, 106.0, 109.0, 112.0],
            "QQQ5": [100.0, 105.0, 110.0, 115.0, 120.0],
        },
        index=idx,
    )


def test_parse_strict_oos_spans_with_labels():
    args = SimpleNamespace(
        strict_oos_spans="2015-01-01:2018-12-31:2019-01-01:2021-12-31@first,"
        "2015-01-01:2021-12-31:2022-01-01:2023-12-31@second",
        start="2015-01-01",
        end="2024-12-31",
        oos_start="2019-01-01",
        initial_train_end="2018-12-31",
    )

    spans = robust_fast.parse_strict_oos_spans(args)

    assert spans == [
        ("first", "2015-01-01", "2018-12-31", "2019-01-01", "2021-12-31"),
        ("second", "2015-01-01", "2021-12-31", "2022-01-01", "2023-12-31"),
    ]


def test_gate_signature_records_strict_oos_and_policy_fields():
    args = SimpleNamespace(
        pilot_min_cagr=0.1,
        pilot_max_dd=-0.7,
        pilot_relative=False,
        disable_pilot_gate=False,
        pilot_years=1,
        rel_cagr_mult=0.95,
        rel_dd_mult=1.0,
        pilot_two_windows=False,
        pilot_min_dsr=0.95,
        pilot_min_calmar=0.45,
        pilot_last12m_min_sharpe=0.4,
        alpha_min=0.05,
        slippage_mult=1.0,
        return_noise_std=0.0,
        price_lag_days=0,
        stress_seed=None,
        stress_auto_relax=True,
        max_effective_leverage=3.0,
        disable_qqq5=True,
        allow_synthetic_qqq5=False,
        initial_train_end="2018-12-31",
        strict_oos_verify=True,
        strict_oos_spans="a:b:c:d@x",
    )

    sig = robust_fast.build_gate_signature(args, qqq5_source_for_run="synthetic", effective_seed=42)

    assert sig["strict_oos_verify"] is True
    assert sig["strict_oos_spans"] == "a:b:c:d@x"
    assert sig["max_effective_leverage"] == 3.0
    assert sig["disable_qqq5"] is True
    assert sig["allow_synthetic_qqq5"] is False
    assert sig["qqq5_source"] == "synthetic"
    assert sig["initial_train_end"] == "2018-12-31"


def test_strict_oos_verification_calls_strategy_helper_not_legacy():
    calls = []

    class FakeStrategyModule:
        @staticmethod
        def run_strict_oos_slice(**kwargs):
            calls.append(kwargs)
            idx = pd.date_range(kwargs["test_start"], periods=3, freq="B")
            eq = pd.Series([1.0, 1.02, 1.03], index=idx, name="Equity")
            return {
                "equity_oos": eq,
                "report_oos": {"CAGR": 0.2, "MaxDD": -0.01, "Sharpe_ex_rf0": 1.0},
            }

        @staticmethod
        def baseline_backtest(**kwargs):
            raise AssertionError("legacy baseline_backtest should not be called")

        @staticmethod
        def deep_learning_backtest(**kwargs):
            raise AssertionError("legacy deep_learning_backtest should not be called")

    candidates = pd.DataFrame(
        [
            {
                "id": "A",
                "cfg_json": '{"id":"A"}',
                "DSR": 1.0,
                "CAGR": 0.2,
            }
        ]
    )
    span_payload = [
        {
            "label": "strict",
            "train_start": "2020-01-01",
            "train_end": "2020-01-02",
            "test_start": "2020-01-03",
            "test_end": "2020-01-07",
            "df": _df(),
            "common_kw": {"initial_train_end": "2020-01-02", "rf_ann": 0.0},
            "tqqq_returns": pd.Series([0.0, 0.01, 0.01], index=pd.date_range("2020-01-03", periods=3, freq="B")),
        }
    ]
    args = SimpleNamespace(mode="baseline", pilot_min_cagr=0.0, pilot_max_dd=-0.5)

    filtered, eval_df = robust_fast.run_strict_oos_verification(
        candidates,
        ["id"],
        FakeStrategyModule,
        args,
        span_payload,
        timeout_seconds=0,
        enforce_gate=True,
    )

    assert len(calls) == 1
    assert calls[0]["train_end"] == "2020-01-02"
    assert calls[0]["test_end"] == "2020-01-07"
    assert calls[0]["common_kw"]["id"] == "A"
    assert eval_df.loc[0, "OOS_CAGR_strict"] == 0.2
    assert not filtered.empty
