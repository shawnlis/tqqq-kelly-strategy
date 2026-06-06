#!/usr/bin/env python3
"""
run_stress_batches.py

Helper orchestrator that runs robust_fast.py separately for each scenario
under the stress perturbation knobs (slippage multiplier, price lag, return
noise). Splitting scenarios keeps each invocation under typical sandbox
timeouts and ensures every shortlisted configuration is evaluated in the
requested regime.

Example:
    python run_stress_batches.py \
        --strategy qqq_deep_learning_and_baseline.py \
        --grid-json highgear_gamma135_stress_grid.json \
        --prefix stress_gamma135 \
        --slippage-mult 2.0 \
        --price-lag-days 1 \
        --return-noise-std 0.01
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple


DEFAULT_SCENARIOS: Tuple[Tuple[str, str, str], ...] = (
    ("full", "2010-01-01", "2025-10-01"),
    ("sideways", "2015-01-01", "2018-12-31"),
    ("pandemic", "2019-01-01", "2021-12-31"),
    ("rate_hi", "2022-01-01", "2024-12-31"),
    ("recent", "2024-01-01", "2025-10-01"),
)


def run_one(cmd: List[str], workdir: Path) -> None:
    print(f"[stress-run] launching: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=workdir)
    if proc.returncode != 0:
        raise RuntimeError(f"robust_fast.py exited with {proc.returncode}")


def parse_scenarios(spec: Iterable[str] | None) -> Tuple[Tuple[str, str, str], ...]:
    if not spec:
        return DEFAULT_SCENARIOS
    scenarios: List[Tuple[str, str, str]] = []
    for part in spec:
        label, _, dates = part.partition("=")
        if ":" not in dates:
            raise ValueError(f"Invalid scenario spec '{part}', expected label=start:end")
        start, end = dates.split(":", 1)
        label = label.strip() or start.replace("-", "")
        scenarios.append((label.strip(), start.strip(), end.strip()))
    return tuple(scenarios)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run perturbed stress batches per scenario.")
    ap.add_argument("--strategy", required=True, help="Strategy module path for robust_fast.py")
    ap.add_argument("--grid-json", required=True, help="Grid JSON to evaluate")
    ap.add_argument("--prefix", required=True, help="Output prefix base (scenario label appended)")
    ap.add_argument("--threads", type=int, default=4, help="Thread count passed to robust_fast.py")
    ap.add_argument("--max-runs", type=int, default=144, help="Cap combinations per scenario run")
    ap.add_argument("--topk", type=int, default=60, help="Top-K retention per scenario run")
    ap.add_argument("--scenarios", nargs="*", help="Optional overrides label=start:end per scenario")
    ap.add_argument("--slippage-mult", type=float, default=2.0, help="Stress slippage multiplier")
    ap.add_argument("--price-lag-days", type=int, default=1, help="Stress price lag in trading days")
    ap.add_argument("--return-noise-std", type=float, default=0.01, help="Daily return noise std")
    ap.add_argument("--stress-seed", type=int, default=20241104, help="Seed for stress perturbations")
    ap.add_argument("--extra-args", type=str, default="",
                    help="Additional arguments forwarded to robust_fast.py")
    ap.add_argument("--workdir", type=str, default=".", help="Working directory")
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    robust_fast = (workdir / "robust_fast.py").resolve()
    if not robust_fast.exists():
        raise FileNotFoundError(f"robust_fast.py not found at {robust_fast}")

    scenarios = parse_scenarios(args.scenarios)
    base_cmd = [
        "python",
        str(robust_fast),
        "--strategy", args.strategy,
        "--grid-json", args.grid_json,
        "--threads", str(args.threads),
        "--max-runs", str(args.max_runs),
        "--topk", str(args.topk),
        "--slippage-mult", str(args.slippage_mult),
        "--price-lag-days", str(args.price_lag_days),
        "--return-noise-std", str(args.return_noise_std),
        "--stress-seed", str(args.stress_seed),
        "--disable-pilot-gate",
        "--stress-auto-relax",
    ]
    if args.extra_args:
        base_cmd.extend(shlex.split(args.extra_args))

    for label, start, end in scenarios:
        out_prefix = f"{args.prefix}_{label}"
        resume_path = f"{out_prefix}_ckpt.json"
        cmd = base_cmd + [
            "--start", start,
            "--end", end,
            "--out-prefix", out_prefix,
            "--resume", resume_path,
        ]
        run_one(cmd, workdir=workdir)

    manifest = {
        "prefix": args.prefix,
        "scenarios": [{"label": l, "start": s, "end": e} for (l, s, e) in scenarios],
        "grid": args.grid_json,
        "max_runs": args.max_runs,
        "topk": args.topk,
        "stress": {
            "slippage_mult": args.slippage_mult,
            "price_lag_days": args.price_lag_days,
            "return_noise_std": args.return_noise_std,
            "stress_seed": args.stress_seed,
        },
    }
    Path(f"{args.prefix}_stress_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[stress-run] completed {len(scenarios)} scenarios; manifest saved to {args.prefix}_stress_manifest.json")


if __name__ == "__main__":
    main()
