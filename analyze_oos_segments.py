#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_oos_segments.py

Helper script to evaluate in-sample vs out-of-sample performance
for the baseline and deep-learning strategies, and compare them to
TQQQ buy-and-hold over the same dates.

Usage example:

    python analyze_oos_segments.py \
        --metrics_csv optionB_no_lookahead_metrics.csv \
        --split_date 2020-01-01 \
        --tqqq_ticker TQQQ
"""

import argparse
import numpy as np
import pandas as pd
import yfinance as yf


def compute_stats(daily_ret: np.ndarray, equity: np.ndarray):
    n = len(daily_ret)
    if n == 0:
        return dict(CAGR=np.nan, Vol=np.nan, Sharpe=np.nan, MaxDD=np.nan, N=0)
    # CAGR
    gross = float(np.prod(1.0 + daily_ret))
    cagr = gross ** (252.0 / n) - 1.0
    # Vol and Sharpe (rf ~ 0)
    vol = float(np.std(daily_ret, ddof=1) * np.sqrt(252.0))
    sharpe = float((np.mean(daily_ret) / (np.std(daily_ret, ddof=1) + 1e-12)) * np.sqrt(252.0))
    # Max drawdown
    peak = np.maximum.accumulate(equity)
    dd = equity / (peak + 1e-12) - 1.0
    maxdd = float(np.min(dd))
    return dict(CAGR=cagr, Vol=vol, Sharpe=sharpe, MaxDD=maxdd, N=n)


def summarize_segment(metrics_df: pd.DataFrame,
                      tqqq_ret: pd.Series,
                      start_date: str,
                      end_date: str,
                      mode: str):
    mask = (
        (metrics_df["mode"] == mode)
        & (metrics_df["date"] >= start_date)
        & (metrics_df["date"] <= end_date)
    )
    seg = metrics_df.loc[mask].copy()
    seg.sort_values("date", inplace=True)
    daily_ret = seg["daily_ret"].to_numpy()
    equity = seg["equity"].to_numpy()
    stats_strat = compute_stats(daily_ret, equity)

    # TQQQ over the same dates
    t_mask = (tqqq_ret.index >= seg["date"].min()) & (tqqq_ret.index <= seg["date"].max())
    t_seg = tqqq_ret.loc[t_mask].fillna(0.0)
    t_eq = (1.0 + t_seg).cumprod().to_numpy()
    stats_tqqq = compute_stats(t_seg.to_numpy(), t_eq)

    return stats_strat, stats_tqqq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics_csv", type=str, required=True,
                    help="Path to the daily metrics CSV from qqq_deep_learning_and_baseline_experimental.py")
    ap.add_argument("--split_date", type=str, default="2020-01-01",
                    help="Split date between in-sample and out-of-sample, e.g. 2020-01-01")
    ap.add_argument("--tqqq_ticker", type=str, default="TQQQ",
                    help="TQQQ ticker (default: TQQQ)")
    args = ap.parse_args()

    m = pd.read_csv(args.metrics_csv)
    # Ensure dates are datetime64 and then convert back to date-only index for consistency
    m["date"] = pd.to_datetime(m["date"]).dt.tz_localize(None)

    start_all = m["date"].min()
    end_all = m["date"].max()
    split = pd.to_datetime(args.split_date)

    # Download TQQQ for the full window
    tqqq_px = yf.download(args.tqqq_ticker, start=start_all, end=end_all + pd.Timedelta(days=2), auto_adjust=True)
    tqqq_ret = tqqq_px["Close"].pct_change().fillna(0.0)
    tqqq_ret.index = tqqq_ret.index.tz_localize(None)

    for label, (s, e) in {
        "In-sample  (2015–split)": (start_all, split - pd.Timedelta(days=1)),
        "OOS       (split–end)"  : (split, end_all),
    }.items():
        s_str = s.strftime("%Y-%m-%d")
        e_str = e.strftime("%Y-%m-%d")
        print("\n=== {}: {} to {} ===".format(label, s_str, e_str))

        for mode in ["baseline", "dl"]:
            stats_strat, stats_tqqq = summarize_segment(m, tqqq_ret, s_str, e_str, mode)
            print(f"[{mode}]   CAGR={stats_strat['CAGR']*100:6.2f}% | "
                  f"Sharpe={stats_strat['Sharpe']:5.2f} | "
                  f"MaxDD={stats_strat['MaxDD']*100:6.2f}% | N={stats_strat['N']:4d}")
            print(f"[TQQQ]    CAGR={stats_tqqq['CAGR']*100:6.2f}% | "
                  f"Sharpe={stats_tqqq['Sharpe']:5.2f} | "
                  f"MaxDD={stats_tqqq['MaxDD']*100:6.2f}%")

if __name__ == "__main__":
    main()
