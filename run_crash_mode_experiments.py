#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import pandas as pd
import numpy as np


def compute_metrics(equity_df: pd.DataFrame) -> dict:
    df = equity_df.sort_values("date").copy()
    df["ret"] = df["equity"].pct_change()
    df = df.dropna(subset=["ret"])
    if df.empty:
        return {}

    total_ret = df["equity"].iloc[-1] / df["equity"].iloc[0] - 1.0
    n_days = len(df)
    years = n_days / 252.0 if n_days > 0 else 0.0
    cagr = (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else np.nan

    vol = df["ret"].std() * np.sqrt(252.0)
    sharpe = cagr / vol if vol and vol > 0 else np.nan

    cummax = df["equity"].cummax()
    dd = df["equity"] / cummax - 1.0
    maxdd = dd.min()
    calmar = cagr / (-maxdd) if maxdd is not None and maxdd < 0 else np.nan

    return dict(
        CAGR=cagr,
        Vol=vol,
        Sharpe=sharpe,
        MaxDD=maxdd,
        Calmar=calmar,
        Final_Equity=df["equity"].iloc[-1],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2015-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--base_prefix", type=str, default="exp")
    parser.add_argument(
        "--script",
        type=str,
        default="qqq_deep_learning_and_baseline_experimental.py",
    )
    args = parser.parse_args()

    experiments = [
        ("none", 0.05),
        ("monitor_only", 0.05),
        ("hard_cap", 0.05),
        ("soft_scale", 0.05),
        ("soft_scale", 0.10),
    ]

    rows = []
    for mode, tail in experiments:
        prefix = f"{args.base_prefix}_mode-{mode}_tail-{int(tail * 100)}"
        cmd = [
            sys.executable,
            args.script,
            "--mode",
            "dl",
            "--start",
            args.start,
            "--end",
            args.end,
            "--out_prefix",
            prefix,
            "--crash_mode",
            mode,
            "--crash_tail_frac",
            str(tail),
        ]
        print("Running:", " ".join(cmd))
        subprocess.run(cmd, check=True)

        equity_path = f"{prefix}_dl_equity.csv"
        if not os.path.exists(equity_path):
            print(f"WARNING: {equity_path} not found, skipping.")
            continue
        eq_df = pd.read_csv(equity_path)
        date_col = None
        for cand in ("date", "Date"):
            if cand in eq_df.columns:
                date_col = cand
                break
        if date_col is None:
            date_col = eq_df.columns[0]
        eq_df["date"] = pd.to_datetime(eq_df[date_col])
        equity_cols = [c for c in eq_df.columns if c.lower().endswith("_equity")]
        if not equity_cols:
            print(f"WARNING: no equity column found in {equity_path}, skipping.")
            continue
        eq_view = eq_df[["date", equity_cols[0]]].rename(columns={equity_cols[0]: "equity"})
        metrics = compute_metrics(eq_view)
        if not metrics:
            continue
        row = dict(crash_mode=mode, crash_tail_frac=tail)
        row.update(metrics)
        rows.append(row)

    if not rows:
        print("No experiment results collected.")
        return

    res_df = pd.DataFrame(rows)
    res_df = res_df.sort_values(["CAGR", "Calmar", "Sharpe"], ascending=[False, False, False])
    pd.set_option("display.max_columns", None)
    print("\n==== Experiment Summary (sorted by CAGR / Calmar / Sharpe) ====\n")
    print(res_df.to_string(index=False))


if __name__ == "__main__":
    main()
