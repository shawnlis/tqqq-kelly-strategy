#!/usr/bin/env python3
"""
stress_sampler.py

Lightweight driver that samples parameter combinations (Sobol/random) and
invokes robust_fast.py for each. Metrics are harvested into CSV so an
optimizer such as Optuna can learn which regions (gamma, target_vol, friction)
survive the stress knobs.

This script does not require Optuna, but if it is installed we offer an
optional --optuna flag to let the library suggest the next sample.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np

try:
    import optuna  # type: ignore
except Exception:
    optuna = None  # type: ignore


RANGES = {
    "base_kelly_frac": (0.45, 0.85),
    "target_vol": (0.28, 0.46),
    "bandit_alpha": (0.60, 0.95),
    "rebalance_every_days": [3, 5, 7, 9],
    "trade_cost_bps": (0.5, 1.5),
    "fut_fin_spread": (0.0010, 0.0025),
    "slippage_mult": (1.0, 2.5),
    "return_noise_std": (0.0, 0.015),
    "price_lag_days": [0, 1, 2],
}


@dataclass
class TrialConfig:
    params: Dict[str, float]
    tag: str
    out_prefix: str
    resume: str


def suggest_random(rng: np.random.Generator) -> Dict[str, float]:
    params: Dict[str, float] = {}
    for key, bounds in RANGES.items():
        if isinstance(bounds, list):
            params[key] = float(rng.choice(bounds))
        else:
            low, high = bounds
            params[key] = float(low + (high - low) * rng.random())
    params["rebalance_every_days"] = int(params["rebalance_every_days"])
    params["price_lag_days"] = int(params["price_lag_days"])
    return params


def suggest_optuna(trial: "optuna.trial.Trial") -> Dict[str, float]:
    params: Dict[str, float] = {}
    for key, bounds in RANGES.items():
        if isinstance(bounds, list):
            params[key] = float(trial.suggest_categorical(key, bounds))
        else:
            low, high = bounds
            params[key] = float(trial.suggest_float(key, low, high))
    params["rebalance_every_days"] = int(params["rebalance_every_days"])
    params["price_lag_days"] = int(params["price_lag_days"])
    return params


def write_grid(params: Dict[str, float], path: Path) -> None:
    grid = {
        "base_kelly_frac": [round(params["base_kelly_frac"], 4)],
        "target_vol": [round(params["target_vol"], 4)],
        "bandit_alpha": [round(params["bandit_alpha"], 4)],
        "rebalance_every_days": [int(params["rebalance_every_days"])],
        "trade_cost_bps": [round(params["trade_cost_bps"], 4)],
        "fut_fin_spread": [round(params["fut_fin_spread"], 5)],
    }
    path.write_text(json.dumps(grid))


def run_trial(cfg: TrialConfig, args) -> Optional[Dict[str, float]]:
    grid_path = Path(f"_sampler_grid_{cfg.tag}.json")
    write_grid(cfg.params, grid_path)

    cmd = [
        sys.executable,
        "robust_fast.py",
        "--strategy", args.strategy,
        "--grid-json", str(grid_path),
        "--start", args.start,
        "--end", args.end,
        "--mode", args.mode,
        "--threads", str(args.threads),
        "--max-runs", "1",
        "--topk", "1",
        "--slippage-mult", f"{cfg.params['slippage_mult']:.4f}",
        "--return-noise-std", f"{cfg.params['return_noise_std']:.5f}",
        "--price-lag-days", str(int(cfg.params["price_lag_days"])),
        "--stress-seed", str(args.stress_seed),
        "--stress-auto-relax",
        "--out-prefix", cfg.out_prefix,
        "--resume", cfg.resume,
        "--fractions", args.fractions,
    ]
    if args.disable_pilot_gate:
        cmd.append("--disable-pilot-gate")
    if args.extra_args:
        if len(args.extra_args) == 1:
            import shlex
            cmd.extend(shlex.split(args.extra_args[0]))
        else:
            cmd.extend(args.extra_args)

    print(f"[sampler] run {cfg.tag}: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    grid_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        print(f"[sampler] warning: run {cfg.tag} exited with {proc.returncode}")
        return None

    summary_path = Path(f"{cfg.out_prefix}_summary.csv")
    if not summary_path.exists():
        print(f"[sampler] warning: summary missing for {cfg.tag}")
        return None
    reader = csv.DictReader(summary_path.open())
    try:
        row = next(reader)
    except StopIteration:
        print(f"[sampler] warning: summary empty for {cfg.tag}")
        return None
    def take(name: str) -> float:
        try:
            return float(row.get(name, "nan"))
        except Exception:
            return math.nan

    metrics = {
        "CAGR": take("CAGR"),
        "Sharpe_ex_rf0": take("Sharpe_ex_rf0"),
        "MaxDD": take("MaxDD"),
        "DSR": take("DSR"),
    }
    for k, v in cfg.params.items():
        metrics[f"param_{k}"] = v
    metrics["tag"] = cfg.tag
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser(description="Sample robust_fast stress configurations.")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default="2025-10-01")
    ap.add_argument("--mode", default="baseline")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--stress-seed", type=int, default=20241104)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--fractions", default="0.35,0.55,0.70,1.00")
    ap.add_argument("--sampler-seed", type=int, default=1337)
    ap.add_argument("--disable-pilot-gate", action="store_true")
    ap.add_argument("--optuna", action="store_true", help="Use Optuna if available")
    ap.add_argument("--study-name", default="stress_sampler")
    ap.add_argument("--storage", default=None, help="Optuna storage URL (optional)")
    ap.add_argument("--extra-args", nargs=argparse.REMAINDER, help="Additional args for robust_fast.py")
    args = ap.parse_args()

    rng = np.random.default_rng(args.sampler_seed)
    log_path = Path("stress_sampler_runs.csv")
    fieldnames = [
        "tag", "CAGR", "Sharpe_ex_rf0", "MaxDD", "DSR",
    ] + [f"param_{k}" for k in RANGES]
    log_exists = log_path.exists()
    out_file = log_path.open("a", newline="")
    writer = csv.DictWriter(out_file, fieldnames=fieldnames)
    if not log_exists:
        writer.writeheader()

    history: List[Dict[str, float]] = []

    def objective(trial) -> float:
        params = suggest_optuna(trial) if optuna and args.optuna else suggest_random(rng)
        tag = f"opt{trial.number:04d}" if optuna and args.optuna else f"rand{len(history):04d}"
        cfg = TrialConfig(params=params, tag=tag,
                          out_prefix=f"stress_sampler_{tag}",
                          resume=f"stress_sampler_{tag}_ckpt.json")
        metrics = run_trial(cfg, args)
        if metrics is None:
            return 10.0  # penalise failures
        history.append(metrics)
        writer.writerow(metrics)
        out_file.flush()
        # Optimise for negative Sharpe (we minimise), penalise NaNs heavily
        sharpe = metrics.get("Sharpe_ex_rf0", math.nan)
        dsr = metrics.get("DSR", math.nan)
        if math.isnan(sharpe) or math.isnan(dsr):
            return 5.0
        return -float(sharpe) - 0.1 * float(dsr)

    if optuna and args.optuna:
        study = optuna.create_study(
            study_name=args.study_name,
            storage=args.storage,
            direction="minimize",
            load_if_exists=True,
        )
        study.optimize(objective, n_trials=args.runs)
        print(f"[sampler] best value {study.best_value:.4f}, params={study.best_params}")
    else:
        for _ in range(args.runs):
            params = suggest_random(rng)
            tag = f"rand{len(history):04d}"
            cfg = TrialConfig(params=params, tag=tag,
                              out_prefix=f"stress_sampler_{tag}",
                              resume=f"stress_sampler_{tag}_ckpt.json")
            metrics = run_trial(cfg, args)
            if metrics:
                history.append(metrics)
                writer.writerow(metrics)
                out_file.flush()
        print(f"[sampler] completed {len(history)} runs; log at {log_path.as_posix()}")

    out_file.close()


if __name__ == "__main__":
    main()
