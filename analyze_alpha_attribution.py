#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd


def _load_metrics(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=['date'])
    df.sort_values('date', inplace=True)
    return df


def _print_contribution_stats(df: pd.DataFrame) -> None:
    cols = ['ret_contrib_tqqq', 'ret_contrib_qqq5', 'ret_contrib_fut']
    available = [c for c in cols if c in df.columns]
    if not available:
        print("[attrib] Contribution columns missing; skipping attribution section.")
        return
    print("\n[Mean Daily Contribution]")
    print(df[available].mean())
    print("\n[Cumulative Contribution]")
    print(df[available].sum())


def _decile_gap(df: pd.DataFrame, col: str, label: str) -> None:
    if col not in df.columns or 'L_after_crash' not in df.columns:
        print(f"[attrib] Missing columns for {label} deciles.")
        return
    subset = df[['L_after_crash', col]].dropna()
    if subset.empty:
        print(f"[attrib] No data for {label} deciles.")
        return
    ranks = subset[col].rank(method='first') / len(subset)
    subset = subset.assign(decile=pd.cut(ranks, bins=np.linspace(0, 1, 11), labels=False, include_lowest=True))
    subset['L_gap_vs_3x'] = subset['L_after_crash'] - 3.0
    table = subset.groupby('decile')['L_gap_vs_3x'].mean().rename(f'{label}_L_gap_mean')
    print(f"\n[{label} return deciles: mean(L_after_crash - 3x)]")
    print(table)


def _top_up_days(df: pd.DataFrame) -> None:
    if 'qqq_ret' not in df.columns:
        print("[attrib] Missing qqq_ret column; cannot analyze top up-days.")
        return
    thresh = df['qqq_ret'].quantile(0.95)
    top = df[df['qqq_ret'] >= thresh]
    rest = df[df['qqq_ret'] < thresh]
    cols = ['L_after_crash', 'w_tqqq', 'w_qqq5']
    print("\n[Top 5% up-days vs remainder]")
    for col in cols:
        top_mean = top[col].mean() if col in top.columns else np.nan
        rest_mean = rest[col].mean() if col in rest.columns else np.nan
        print(f"{col}: top5% mean={top_mean:.3f}, others mean={rest_mean:.3f}, diff={top_mean-rest_mean:.3f}")
    if len(top) > 0:
        print(f"Top bucket size: {len(top)} days (threshold qqq_ret >= {thresh:.2%})")
    else:
        print("No days in top bucket.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_alpha_attribution.py <dl_daily_metrics.csv>")
        sys.exit(1)
    path = sys.argv[1]
    df = _load_metrics(path)
    _print_contribution_stats(df)
    _decile_gap(df, 'qqq_ret', '1d')
    _decile_gap(df, 'qqq_ret_5d', '5d')
    _top_up_days(df)
    print("\n[Conclusion]")
    if 'qqq_ret' in df.columns:
        thr = df['qqq_ret'].quantile(0.95)
        top = df[df['qqq_ret'] >= thr]
        rest = df[df['qqq_ret'] < thr]
        top_l = top['L_after_crash'].mean() if len(top) else np.nan
        rest_l = rest['L_after_crash'].mean() if len(rest) else np.nan
        delta = top_l - rest_l
        if np.isnan(delta):
            msg = "Insufficient data to judge leverage response on top up-days."
        elif delta >= 0:
            msg = f"L_after_crash averages {top_l:.2f} on top up-days vs {rest_l:.2f} elsewhere (no delevering in rallies)."
        else:
            msg = f"L_after_crash drops to {top_l:.2f} on top up-days vs {rest_l:.2f} elsewhere (evidence of trimming into strength)."
        print(msg)
    else:
        print("qqq_ret missing; cannot compare leverage on top up-days.")


if __name__ == "__main__":
    main()
