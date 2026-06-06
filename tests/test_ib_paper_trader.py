import json
import os
from pathlib import Path

from ib_paper_trader import (
    committee_fingerprint,
    compute_turnover_value,
    idempotency_mark,
    idempotency_should_skip,
)


def test_fingerprint_stability():
    a = [(0.45, 0.32, 0.70, 5), (0.45, 0.31, 0.75, 5)]
    b = [(0.45, 0.32, 0.70, 5), (0.45, 0.31, 0.75, 5)]
    assert committee_fingerprint(a) == committee_fingerprint(b)


def test_turnover_and_min_drift_gate():
    px_q, px_t = 500.0, 50.0
    d_q, d_t = 10, -100  # $10*500 + 100*50 = $10k
    delta = compute_turnover_value(px_q, px_t, d_q, d_t)
    assert delta == 10000.0
    eq = 800000.0
    assert (delta / eq) > 0.012


def test_idempotency_lock(tmp_path: Path):
    state_dir = tmp_path / ".state"
    os.makedirs(state_dir, exist_ok=True)
    skip, lock = idempotency_should_skip("2025-10-31", "ibkr", "abc123", str(state_dir))
    assert skip is False
    assert isinstance(lock, Path)
    idempotency_mark(lock)
    skip2, _ = idempotency_should_skip("2025-10-31", "ibkr", "abc123", str(state_dir))
    assert skip2 is True
    # ensure file written
    payload = json.loads(lock.read_text())
    assert payload["locked"] is True
