from pathlib import Path

import numpy as np
import pandas as pd

import minimal_tqqq_risk_baseline as m


def make_prices(periods=260, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=periods)
    base_ret = np.full(periods, 0.001)
    base_ret[60:95] = -0.012
    base_ret[95:150] = 0.005
    qqq = 100.0 * np.cumprod(1.0 + base_ret)
    tqqq = 100.0 * np.cumprod(1.0 + np.clip(3.0 * base_ret, -0.95, 1.0))
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def make_slice(df):
    return [
        {
            "slice": "toy",
            "train_start": str(df.index[0].date()),
            "train_end": str(df.index[99].date()),
            "test_start": str(df.index[100].date()),
            "test_end": str(df.index[-1].date()),
        }
    ]


def write_local_inputs(tmp_path, df, slices):
    src = df.reset_index().rename(columns={"index": "Date"})
    qqq_csv = tmp_path / "qqq.csv"
    tqqq_csv = tmp_path / "tqqq.csv"
    slices_csv = tmp_path / "slices.csv"
    src[["Date", "QQQ"]].rename(columns={"QQQ": "Close"}).to_csv(qqq_csv, index=False)
    src[["Date", "TQQQ"]].rename(columns={"TQQQ": "Close"}).to_csv(tqqq_csv, index=False)
    pd.DataFrame(slices).to_csv(slices_csv, index=False)
    return qqq_csv, tqqq_csv, slices_csv


def test_default_cli_does_not_save_raw_daily_artifacts(tmp_path):
    df = make_prices()
    slices = make_slice(df)
    qqq_csv, tqqq_csv, slices_csv = write_local_inputs(tmp_path, df, slices)
    artifact_dir = tmp_path / "raw"
    out_prefix = tmp_path / "minimal_tqqq"

    rc = m.main(
        [
            "--start",
            str(df.index[0].date()),
            "--end",
            str(df.index[-1].date()),
            "--qqq_csv",
            str(qqq_csv),
            "--tqqq_csv",
            str(tqqq_csv),
            "--slices_csv",
            str(slices_csv),
            "--out_prefix",
            str(out_prefix),
            "--artifact_dir",
            str(artifact_dir),
        ]
    )

    assert rc == 0
    assert not list(artifact_dir.glob("*_daily_*.csv"))


def test_explicit_save_daily_artifacts_writes_required_columns(tmp_path):
    df = make_prices()
    paths = m.save_daily_artifacts(df, make_slice(df), artifact_dir=tmp_path / "raw", run_id="test_run")
    assert paths
    assert all(path.exists() for path in paths)

    artifact = pd.read_csv(paths[0])
    required = {
        "Date",
        "run_id",
        "slice",
        "strategy",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "equity",
        "w_tqqq",
        "w_qqq",
        "w_cash",
        "effective_leverage",
        "signal_state",
        "drawdown",
        "tqqq_buyhold_drawdown",
    }
    assert required.issubset(artifact.columns)
    assert artifact["effective_leverage"].max() <= 3.0 + 1e-12


def test_gitignore_contains_raw_artifact_patterns():
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert "reports/minimal_baseline/raw/" in text
    assert "reports/minimal_baseline/*_daily_*.csv" in text


def test_go_no_go_hurdles_doc_exists_and_contains_required_language():
    text = Path("reports/minimal_baseline/GO_NO_GO_HURDLES.md").read_text(encoding="utf-8")
    assert "NOT READY FOR PAPER TRADING" in text
    assert "Kill Conditions" in text
    assert "Strict OOS" in text
    assert "UlcerIndex" in text


def test_manifest_exists_and_contains_do_not_paper_trade_rule():
    text = Path("reports/minimal_baseline/MINIMAL_BASELINE_RESEARCH_MANIFEST.md").read_text(encoding="utf-8")
    assert "do not paper trade before hurdles pass" in text.lower()
    assert "do not tune MA windows by grid search" in text
    assert "MA150 risk-off QQQ" in text
