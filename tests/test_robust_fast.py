import json
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import robust_fast  # noqa: E402


def _make_equity(returns: np.ndarray) -> pd.Series:
    eq = (1.0 + pd.Series(returns)).cumprod()
    eq.iloc[0] = 1.0
    return eq


def test_select_diverse_respects_threshold():
    param_keys = ["id"]
    df_candidates = pd.DataFrame(
        [
            {"id": "A", "DSR": 1.05, "Sharpe_ex_rf0": 0.9, "Calmar": 0.6, "cfg_json": json.dumps({"id": "A"})},
            {"id": "B", "DSR": 1.04, "Sharpe_ex_rf0": 0.88, "Calmar": 0.58, "cfg_json": json.dumps({"id": "B"})},
            {"id": "C", "DSR": 0.95, "Sharpe_ex_rf0": 0.70, "Calmar": 0.45, "cfg_json": json.dumps({"id": "C"})},
        ]
    )
    eq_map = {
        "A": _make_equity(np.array([0.010, 0.011, 0.010, 0.009])),
        "B": _make_equity(np.array([0.010, 0.012, 0.010, 0.011])),
        "C": _make_equity(np.array([-0.020, 0.030, -0.015, 0.025])),
    }

    def fake_run_backtest(_mod, _df, _common, cfg, _mode, _timeout):
        eq = eq_map[cfg["id"]]
        rep = {"CAGR": 0.20, "MaxDD": -0.40, "Trades": 1200}
        return eq, rep, None

    with mock.patch("robust_fast.run_backtest", side_effect=fake_run_backtest):
        high_df, diag_high, _ = robust_fast.select_diverse_topk(
            df_candidates,
            param_keys,
            target_count=3,
            corr_thresh=0.99,
            min_keep=1,
            metric="dsr",
            mod=None,
            df_full=pd.DataFrame(),
            common_kw={},
            mode="baseline",
            timeout_seconds=0,
        )
        low_df, diag_low, _ = robust_fast.select_diverse_topk(
            df_candidates,
            param_keys,
            target_count=3,
            corr_thresh=0.70,
            min_keep=1,
            metric="dsr",
            mod=None,
            df_full=pd.DataFrame(),
            common_kw={},
            mode="baseline",
            timeout_seconds=0,
        )

    assert len(high_df) > len(low_df)
    assert {"rank", "cfg_json", "metric_value", "selected"}.issubset(set(diag_low.columns))


def test_build_manifest_contains_required_metadata():
    df_final = pd.DataFrame(
        [
            {
                "id": "A",
                "cfg_json": json.dumps({"id": "A"}),
                "CAGR": 0.25,
                "MaxDD": -0.5,
                "Sharpe_ex_rf0": 0.9,
                "DSR": 1.1,
                "Calmar": 0.5,
                "det_seed": 42,
                "oos_flag": "",
            }
        ]
    )
    metadata = {
        "strategy_path": "demo.py",
        "strategy_sha": "abc123",
        "grid_source": "default",
        "grid_sha": "def456",
        "gate_signature": {},
        "seed": 42,
        "det_seed": 42,
        "start": "2010-01-01",
        "end": "2020-01-01",
        "generated_at": "2024-01-01T00:00:00Z",
    }
    manifest = robust_fast.build_manifest(df_final, ["id"], metadata=metadata)
    assert set(["metadata", "entries"]).issubset(manifest.keys())
    assert manifest["metadata"]["strategy_sha"] == "abc123"
    assert manifest["metadata"]["grid_sha"] == "def456"
    assert "gate_signature" in manifest["metadata"]
    assert manifest["entries"][0]["det_seed"] == 42
    assert "baseline" in manifest["entries"][0]


def test_parse_oos_spans_default_labels():
    class Args:
        oos_spans = None
        oos_start = "2010-01-01"
        end = "2025-10-01"

    spans = robust_fast.parse_oos_spans(Args())
    assert spans[0][0] == "2010p"
    assert spans[1][0] == "5y"
