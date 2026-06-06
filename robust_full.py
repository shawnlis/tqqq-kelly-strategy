#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robust_full.py

High-coverage robustness harness that orchestrates multiple robust_fast.py runs
across different market scenarios and aggregates the results into a single
report. Designed to complement the fast sweep by layering additional checks.

Example:
    python robust_full.py \
        --strategy qqq_deep_learning_and_baseline.py \
        --grid-json highgear_base_kelly_grid.json \
        --prefix robust_rel_v5f_full \
        --mode baseline \
        --threads 4 \
        --extra-args "--pilot-years 6 --pilot-two-windows --pilot-min-cagr 0.00 \
                      --pilot-max-dd -0.95 --pilot-relative --rel-cagr-mult 0.90 \
                      --rel-dd-mult 1.05 --pilot-min-dsr 0.90 --pilot-min-calmar 0.30 \
                      --pilot-last12m-min-sharpe 0.30 --alpha-min 0.03 \
                      --topk 40 --max-runs 240 --fractions 0.35,0.55,0.70,1.00 \
                      --optimism 0.18 --timeout-seconds 1200 --det-seed 1337 \
                      --min-trades 400 --max-turnover 5.0"
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd


DEFAULT_SCENARIOS = [
    {"label": "full", "start": "2010-01-01", "end": "2025-10-01"},
    {"label": "sideways", "start": "2015-01-01", "end": "2018-12-31"},
    {"label": "pandemic", "start": "2019-01-01", "end": "2021-12-31"},
    {"label": "rate_hike", "start": "2022-01-01", "end": "2024-12-31"},
    {"label": "recent", "start": "2024-01-01", "end": "2025-10-01"},
]


@dataclass
class Scenario:
    label: str
    start: str
    end: str


def parse_scenarios(path: Path | None, default: Iterable[Dict[str, str]]) -> List[Scenario]:
    if path is None:
        raw = default
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data.get("scenarios", [])
        else:
            raw = data
    scenarios: List[Scenario] = []
    for item in raw:
        label = item.get("label")
        start = item.get("start")
        end = item.get("end")
        if not label or not start or not end:
            raise ValueError(f"Invalid scenario entry: {item}")
        scenarios.append(Scenario(label=label, start=start, end=end))
    if not scenarios:
        raise ValueError("No scenarios specified.")
    return scenarios


def build_command(
    robust_fast: Path,
    base_args: List[str],
    scenario: Scenario,
    prefix: str,
    strategy: str,
    grid_json: str,
    mode: str,
    threads: int,
) -> List[str]:
    out_prefix = f"{prefix}_{scenario.label}"
    resume = f"{out_prefix}_ckpt.json"
    cmd = [
        sys.executable,
        str(robust_fast),
        "--strategy",
        strategy,
        "--grid-json",
        grid_json,
        "--mode",
        mode,
        "--threads",
        str(threads),
        "--start",
        scenario.start,
        "--end",
        scenario.end,
        "--out-prefix",
        out_prefix,
        "--resume",
        resume,
    ]
    cmd.extend(base_args)
    return cmd


def run_scenario(cmd: List[str], workdir: Path) -> None:
    print(f"[scenario] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=workdir)
    if proc.returncode != 0:
        raise RuntimeError(f"robust_fast failed with return code {proc.returncode}")


def aggregate_results(
    scenarios: List[Scenario],
    prefix: str,
    workdir: Path,
    aggregated_path: Path,
) -> None:
    param_cols = [
        "base_kelly_frac",
        "target_vol",
        "bandit_alpha",
        "rebalance_every_days",
        "trade_cost_bps",
        "fut_fin_spread",
    ]
    scenario_dfs: List[pd.DataFrame] = []

    for sc in scenarios:
        summary_path = workdir / f"{prefix}_{sc.label}_summary.csv"
        if not summary_path.exists():
            raise FileNotFoundError(f"Missing summary for scenario {sc.label}: {summary_path}")
        df = pd.read_csv(summary_path)
        missing = [c for c in param_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Summary {summary_path} missing parameter columns: {missing}")
        metric_cols = [c for c in df.columns if c not in param_cols and c not in {"cfg_json", "early_stopped"}]
        rename_map = {c: f"{c}_{sc.label}" for c in metric_cols}
        df = df[param_cols + metric_cols].rename(columns=rename_map)
        scenario_dfs.append(df)

    aggregated = scenario_dfs[0]
    for df in scenario_dfs[1:]:
        aggregated = aggregated.merge(df, on=param_cols, how="outer")

    metric_groups: Dict[str, List[str]] = {}
    for col in aggregated.columns:
        if col in param_cols:
            continue
        base = col.rsplit("_", 1)[0]
        metric_groups.setdefault(base, []).append(col)

    for metric, cols in metric_groups.items():
        aggregated[f"{metric}_min"] = aggregated[cols].min(axis=1)
        aggregated[f"{metric}_max"] = aggregated[cols].max(axis=1)
        aggregated[f"{metric}_mean"] = aggregated[cols].mean(axis=1)

    sort_cols = [c for c in [f"CAGR_min", f"Sharpe_ex_rf0_min"] if c in aggregated.columns]
    if sort_cols:
        aggregated.sort_values(by=sort_cols, ascending=False, inplace=True)
    aggregated.to_csv(aggregated_path, index=False)
    print(f"[aggregate] saved {aggregated_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="High coverage robustness orchestrator.")
    parser.add_argument("--strategy", required=True, help="Strategy module path")
    parser.add_argument("--grid-json", required=True, help="Grid JSON for robust_fast")
    parser.add_argument("--prefix", required=True, help="Base prefix for outputs")
    parser.add_argument("--mode", default="baseline", help="robust_fast mode (baseline/dl)")
    parser.add_argument("--threads", type=int, default=4, help="Thread count for robust_fast")
    parser.add_argument("--scenarios-json", type=str, help="Optional JSON file describing scenarios")
    parser.add_argument("--extra-args", type=str, default="", help="Additional arguments to pass to robust_fast")
    parser.add_argument("--workdir", type=str, default=".", help="Working directory for runs")
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    scenarios = parse_scenarios(Path(args.scenarios_json) if args.scenarios_json else None, DEFAULT_SCENARIOS)
    robust_fast_path = (workdir / "robust_fast.py").resolve()
    if not robust_fast_path.exists():
        raise FileNotFoundError(f"Cannot locate robust_fast.py at {robust_fast_path}")

    base_args = shlex.split(args.extra_args) if args.extra_args else []
    for sc in scenarios:
        cmd = build_command(
            robust_fast=robust_fast_path,
            base_args=base_args,
            scenario=sc,
            prefix=args.prefix,
            strategy=args.strategy,
            grid_json=args.grid_json,
            mode=args.mode,
            threads=args.threads,
        )
        run_scenario(cmd, workdir=workdir)

    aggregated_path = workdir / f"{args.prefix}_aggregated_summary.csv"
    aggregate_results(scenarios, prefix=args.prefix, workdir=workdir, aggregated_path=aggregated_path)


if __name__ == "__main__":
    main()
