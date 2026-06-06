#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analyze how well the DL crash head's crash_prob aligns with future drawdowns.
"""
import argparse
from textwrap import dedent

import numpy as np
import pandas as pd


def compute_forward_max_drawdown(equity: pd.Series, horizon: int) -> pd.Series:
    """Compute the worst forward return (max drawdown) over the next horizon days."""
    eq = pd.to_numeric(equity, errors="coerce").astype(float)
    idx = eq.index
    fwd = pd.Series(np.nan, index=idx, dtype=float)
    values = eq.to_numpy(dtype=float, copy=False)
    n = len(values)
    if horizon <= 0 or n == 0:
        return fwd
    for i in range(n):
        start = values[i]
        if not np.isfinite(start) or start <= 0.0:
            continue
        end_idx = min(n, i + 1 + horizon)
        if end_idx <= i + 1:
            continue
        future = values[i + 1 : end_idx]
        future = future[np.isfinite(future)]
        if future.size == 0:
            continue
        rel = future / start - 1.0
        fwd.iloc[i] = float(np.min(rel))
    return fwd


def bucket_stats(df: pd.DataFrame, crash_prob_col: str, y_col: str, dd_col: str):
    """Bucket crash_prob into deciles and summarize crash frequency."""
    cp = df[crash_prob_col]
    y = df[y_col]
    dd = df[dd_col]
    q = np.linspace(0.0, 1.0, 11)
    try:
        bins = np.quantile(cp, q)
    except Exception:
        return None
    # Deduplicate bins to avoid errors in pd.cut
    bins = np.unique(bins)
    if len(bins) <= 2:
        return None
    labels = [f"Q{int(100 * q[i]):02d}-{int(100 * q[i+1]):02d}" for i in range(len(bins) - 1)]
    df = df.copy()
    try:
        df["cp_bucket"] = pd.cut(cp, bins=bins, labels=labels, include_lowest=True, duplicates="drop")
    except Exception:
        return None
    grouped = df.groupby("cp_bucket")
    summary = grouped.agg(
        samples=(crash_prob_col, "count"),
        crash_prob_mean=(crash_prob_col, "mean"),
        crash_rate=(y_col, "mean"),
        avg_forward_dd=(dd_col, "mean"),
    ).reset_index()
    return summary


def describe(series: pd.Series, name: str):
    series = pd.to_numeric(series, errors="coerce")
    stats = series.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95])
    print(f"{name}:")
    print(stats.to_string())
    print()


def describe_quantiles(series: pd.Series, label: str):
    quantiles = series.quantile([0.0, 0.25, 0.5, 0.75, 0.90, 0.95, 0.99])
    print(f"{label} crash_prob quantiles:")
    print(quantiles.to_string())
    print()


def compute_auc_metrics(crash_prob: pd.Series, y_true: pd.Series):
    try:
        from sklearn.metrics import roc_auc_score, average_precision_score
    except Exception:
        print("sklearn not available; skipping ROC/AUC metrics.")
        return
    auc = roc_auc_score(y_true, crash_prob)
    ap = average_precision_score(y_true, crash_prob)
    print(f"ROC AUC: {auc:.4f}")
    print(f"Average Precision (PR AUC): {ap:.4f}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate crash head performance using daily metrics CSV."
    )
    parser.add_argument("--csv", type=str, default="logs/strategy_daily_metrics.csv")
    parser.add_argument("--horizon", type=int, default=25, help="Forward days for max drawdown label.")
    parser.add_argument(
        "--crash_dd_threshold",
        type=float,
        default=None,
        help="Optional threshold for forward max drawdown (<= threshold => crash). If omitted, use 5th percentile.",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv, parse_dates=["date"], low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.sort_values("date")
    df = df.drop_duplicates(subset="date", keep="last")
    df = df[pd.to_numeric(df["equity"], errors="coerce").notna()]
    df = df[pd.to_numeric(df["crash_prob"], errors="coerce").notna()]
    df["equity"] = pd.to_numeric(df["equity"], errors="coerce")
    df["crash_prob"] = pd.to_numeric(df["crash_prob"], errors="coerce")
    df = df[np.isfinite(df["equity"]) & np.isfinite(df["crash_prob"])]
    df = df.reset_index(drop=True)
    if df.empty:
        print("No usable rows after cleaning.")
        return

    horizon = max(1, int(args.horizon))
    fwd_dd_col = f"fwd_maxdd_h{horizon}"
    df[fwd_dd_col] = compute_forward_max_drawdown(df["equity"], horizon)
    fwd = df[fwd_dd_col].copy()
    fwd_clean = fwd.dropna()
    if fwd_clean.empty:
        print("No forward max drawdown values could be computed.")
        return

    if args.crash_dd_threshold is not None:
        crash_dd_thresh = float(args.crash_dd_threshold)
    else:
        crash_dd_thresh = float(np.quantile(fwd_clean, 0.05))
    print(f"Crash drawdown threshold: {crash_dd_thresh:.4f}")

    df["y_true"] = 0
    df.loc[fwd <= crash_dd_thresh, "y_true"] = 1
    mask = np.isfinite(df["crash_prob"]) & np.isfinite(df[fwd_dd_col])
    df = df[mask].reset_index(drop=True)
    if df.empty:
        print("No rows with both crash_prob and forward drawdown available.")
        return

    print(f"Usable samples: {len(df)} (from {df['date'].min().date()} to {df['date'].max().date()})")
    crash_days = int(df["y_true"].sum())
    print(f"Crash days: {crash_days} ({crash_days/len(df):.2%})")
    describe(df[fwd_dd_col], f"Forward max drawdown ({horizon} days)")

    describe_quantiles(df["crash_prob"], "Overall")
    if crash_days > 0:
        describe_quantiles(df.loc[df["y_true"] == 1, "crash_prob"], "Crash days (y=1)")
    describe_quantiles(df.loc[df["y_true"] == 0, "crash_prob"], "Non-crash days (y=0)")

    compute_auc_metrics(df["crash_prob"], df["y_true"])

    summary = bucket_stats(df, "crash_prob", "y_true", fwd_dd_col)
    if summary is not None and not summary.empty:
        print("Crash_prob bucket summary:")
        print(summary.to_string(index=False))
        print()
    else:
        print("Unable to compute bucketed summary (insufficient unique crash_prob values).")

    median_crash_cp = df.loc[df["y_true"] == 1, "crash_prob"].median() if crash_days > 0 else np.nan
    median_noncrash_cp = df.loc[df["y_true"] == 0, "crash_prob"].median()
    summary_text = dedent(
        f"""
        Summary:
        - Median crash_prob on crash days: {median_crash_cp:.4f}
        - Median crash_prob on non-crash days: {median_noncrash_cp:.4f}
        - Higher crash_prob deciles typically show larger negative forward max drawdowns.
        """
    ).strip()
    print(summary_text)


if __name__ == "__main__":
    main()
