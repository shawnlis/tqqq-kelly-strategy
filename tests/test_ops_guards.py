import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from ib_paper_trader import (
    append_jsonl,
    compute_committee_weights,
    is_in_close_window,
    oos_prune,
    should_debounce_notify,
)


def test_committee_determinism_mean():
    rows = [
        {"w_fut": 0.80, "w_tqqq": 0.20, "w_qqq5": 0.00, "Vol": 0.30, "DSR": 1.1},
        {"w_fut": 0.75, "w_tqqq": 0.25, "w_qqq5": 0.00, "Vol": 0.29, "DSR": 1.0},
        {"w_fut": 0.70, "w_tqqq": 0.28, "w_qqq5": 0.02, "Vol": 0.32, "DSR": 1.2},
    ]
    w1 = compute_committee_weights(rows, mode="mean")
    w2 = compute_committee_weights(rows, mode="mean")
    assert np.allclose(w1, w2)
    assert abs(np.sum(w1) - 1.0) < 1e-12


def test_committee_voltarget_weights_high_dsr_prefers_low_vol():
    rows = [
        {"w_fut": 0.60, "w_tqqq": 0.40, "w_qqq5": 0.00, "Vol": 0.40},
        {"w_fut": 0.90, "w_tqqq": 0.10, "w_qqq5": 0.00, "Vol": 0.20},
    ]
    weights = compute_committee_weights(rows, mode="voltarget")
    # voltarget returns final sleeve allocation, not per-row model weights.
    assert np.allclose(weights, np.array([0.8, 0.2, 0.0]))
    assert abs(np.sum(weights) - 1.0) < 1e-12


def test_oos_prune_filters_flagged_and_degraded():
    rows = [
        {"id": 1, "oos_flag": "", "OOS_DSR_long": 0.90, "OOS_Calmar_long": 0.60},
        {"id": 2, "oos_flag": "true", "OOS_DSR_long": 1.2, "OOS_Calmar_long": 0.7},
        {"id": 3, "oos_flag": "", "OOS_DSR_long": 0.50, "OOS_Calmar_long": 0.6},
    ]
    kept = oos_prune(rows)
    ids = {row["id"] for row in kept}
    assert ids == {1}


def test_append_jsonl_and_debounce(tmp_path: Path):
    jsonl_path = tmp_path / "log.jsonl"
    append_jsonl(jsonl_path, {"foo": 1})
    append_jsonl(jsonl_path, {"bar": 2})
    data = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    assert data == [{"foo": 1}, {"bar": 2}]

    state_dir = tmp_path / ".state"
    os.makedirs(state_dir, exist_ok=True)
    key = "test-key"
    first = should_debounce_notify(str(state_dir), key, cooldown_sec=3600)
    second = should_debounce_notify(str(state_dir), key, cooldown_sec=3600)
    assert first is False
    assert second is True


def test_is_in_close_window_smoke():
    now_utc = pd.Timestamp.now(tz="UTC")
    allowed, detail = is_in_close_window(now_utc)
    assert isinstance(allowed, bool)
    assert isinstance(detail, str)
