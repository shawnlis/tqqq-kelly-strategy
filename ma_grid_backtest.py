#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ma_grid_backtest.py

Simple MA crossover grid search for TQQQ:
- short_ma in [3,5,7,10,12,15,20]
- long_ma  in [20,30,40,50,60,80,100,120], long > short
Rule: short_MA up-cross long_MA -> full TQQQ; down-cross -> cash.
Costs: same structure as DL (trade_cost_bps + slip_bps per notional).
Uses 2010-01-01 ~ 2017-12-31 as in-sample, 2018-01-01 ~ 2024-12-31 as OOS.

Usage:
    python ma_grid_backtest.py
or
    python ma_grid_backtest.py --tqqq_csv path/to/TQQQ.csv \
        --trade_cost_bps 0.5 --slip_bps 3.0 --tqqq_expense 0.0095
"""

import argparse
import math
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None


# --------- Data loading ---------

def load_tqqq_prices(start: str, end: str, csv_path: str = None) -> pd.DataFrame:
    """
    Load daily prices for TQQQ between [start, end].

    If csv_path is provided, expects a CSV with at least a 'Date' column
    and either 'Adj Close' or 'Close'. Otherwise uses yfinance.
    """
    if csv_path:
        df = pd.read_csv(csv_path, parse_dates=["Date"])
        df = df.set_index("Date").sort_index()
    else:
        if yf is None:
            raise RuntimeError("yfinance is not installed and no --tqqq_csv provided.")
        df = yf.download("TQQQ", start=start, end=end)
        if df.empty:
            raise RuntimeError("Failed to download TQQQ data.")
        df.index.name = "Date"

    # Prefer Adj Close if available
    if "Adj Close" in df.columns:
        px = df["Adj Close"].copy()
    elif "Close" in df.columns:
        px = df["Close"].copy()
    else:
        raise RuntimeError("CSV must contain 'Adj Close' or 'Close' column.")

    # yfinance 2.x may return a DataFrame for a single ticker; squeeze to Series
    if isinstance(px, pd.DataFrame):
        if "TQQQ" in px.columns:
            px = px["TQQQ"]
        elif px.shape[1] == 1:
            px = px.iloc[:, 0]
        else:
            px = px.iloc[:, 0]
    px = px.dropna()
    px.name = "TQQQ"
    return px.to_frame(name="TQQQ")


# --------- Core metrics ---------

def compute_metrics(equity: pd.Series) -> Dict[str, float]:
    """
    Compute CAGR, Vol, Sharpe (rf=0), MaxDD, Calmar for a given equity curve.
    Equity should be a Series indexed by dates, already normalized as desired
    for the period (we'll treat equity.iloc[0] as starting capital).
    """
    if equity is None or len(equity) < 2:
        return dict(CAGR=0.0, Vol=0.0, Sharpe=0.0, MaxDD=0.0, Calmar=0.0)

    # Remove any non-positive values to avoid log issues
    equity = equity.astype(float)
    equity = equity.replace([np.inf, -np.inf], np.nan).dropna()
    if len(equity) < 2:
        return dict(CAGR=0.0, Vol=0.0, Sharpe=0.0, MaxDD=0.0, Calmar=0.0)

    start_val = float(equity.iloc[0])
    end_val = float(equity.iloc[-1])

    n_days = (equity.index[-1] - equity.index[0]).days
    n_years = n_days / 365.25 if n_days > 0 else 0.0

    if n_years > 0 and start_val > 0:
        cagr = (end_val / start_val) ** (1.0 / n_years) - 1.0
    else:
        cagr = 0.0

    # Daily strategy returns from equity
    rets = equity.pct_change().dropna()
    if len(rets) > 1 and rets.std() > 1e-12:
        vol = rets.std() * math.sqrt(252.0)
        sharpe = rets.mean() / rets.std() * math.sqrt(252.0)
    else:
        vol = 0.0
        sharpe = 0.0

    # Drawdown
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    maxdd = float(dd.min()) if len(dd) > 0 else 0.0

    if maxdd < 0:
        calmar = cagr / abs(maxdd) if abs(maxdd) > 1e-12 else 0.0
    else:
        calmar = 0.0

    return dict(CAGR=cagr, Vol=vol, Sharpe=sharpe, MaxDD=maxdd, Calmar=calmar)


# --------- MA backtest ---------

def backtest_ma_strategy(
    px: pd.Series,
    short_window: int,
    long_window: int,
    trade_cost_bps: float,
    slip_bps: float,
    tqqq_expense: float,
) -> pd.DataFrame:
    """
    Simple MA crossover backtest:
    - Signal: 1 if short_MA > long_MA, else 0
    - Use lagged signal (t-1) for position on day t
    - Equity starts at 1.0
    - Costs: when position changes, cost = |Δposition| * (trade_cost_bps + slip_bps)/1e4
    - Expense: subtract tqqq_expense/252 from raw daily return
    Returns DataFrame with columns: equity, position, daily_ret.
    """
    px = px.astype(float).copy()
    # Moving averages
    ma_short = px.rolling(short_window).mean()
    ma_long = px.rolling(long_window).mean()

    raw_signal = (ma_short > ma_long).astype(int)
    # Use previous day's signal to avoid lookahead
    signal = raw_signal.shift(1).fillna(0).astype(int)

    # Raw daily return and net of expense
    raw_ret = px.pct_change().fillna(0.0)
    net_ret = raw_ret - (tqqq_expense / 252.0)

    equity = pd.Series(index=px.index, dtype=float)
    position = pd.Series(index=px.index, dtype=float)
    daily_ret = pd.Series(index=px.index, dtype=float)

    equity.iloc[0] = 1.0
    position.iloc[0] = 0.0
    daily_ret.iloc[0] = 0.0

    prev_pos = 0.0
    total_cost_bps = (trade_cost_bps or 0.0) + (slip_bps or 0.0)

    for i in range(1, len(px)):
        pos = float(signal.iloc[i])
        r = float(net_ret.iloc[i])

        # Trade cost when position changes
        turnover = abs(pos - prev_pos)  # since positions are 0 or 1
        cost = turnover * (total_cost_bps / 10000.0)

        # Use yesterday's position for today's P&L
        eq_prev = equity.iloc[i - 1]
        eq_today = eq_prev * (1.0 + prev_pos * r - cost)

        equity.iloc[i] = eq_today
        position.iloc[i] = pos
        daily_ret.iloc[i] = eq_today / eq_prev - 1.0 if eq_prev > 0 else 0.0

        prev_pos = pos

    df = pd.DataFrame(
        {"equity": equity, "position": position, "daily_ret": daily_ret},
        index=px.index,
    )
    return df


def compute_period_equity(df: pd.DataFrame, start: str, end: str) -> pd.Series:
    """
    Take a full equity curve df, slice [start, end], and renormalize
    so that the first value = 1.0. Returns the normalized equity series.
    """
    sub = df.loc[start:end].copy()
    if sub.empty:
        return pd.Series([], dtype=float)
    base = float(sub["equity"].iloc[0])
    if base <= 0:
        base = 1.0
    return sub["equity"] / base


# --------- Buy & Hold for reference ---------

def backtest_buy_and_hold(
    px: pd.Series,
    start: str,
    end: str,
    tqqq_expense: float,
) -> Dict[str, float]:
    """
    Full TQQQ buy&hold from start to end with same expense assumption.
    """
    sub = px.loc[start:end].astype(float).copy()
    if len(sub) < 2:
        return dict(CAGR=0.0, Vol=0.0, Sharpe=0.0, MaxDD=0.0, Calmar=0.0)

    raw_ret = sub.pct_change().fillna(0.0)
    net_ret = raw_ret - (tqqq_expense / 252.0)

    eq = pd.Series(index=sub.index, dtype=float)
    eq.iloc[0] = 1.0
    for i in range(1, len(sub)):
        eq.iloc[i] = eq.iloc[i - 1] * (1.0 + net_ret.iloc[i])

    return compute_metrics(eq)


# --------- Main grid search ---------

def run_grid(
    px: pd.Series,
    trade_cost_bps: float,
    slip_bps: float,
    tqqq_expense: float,
    train_start: str,
    train_end: str,
    oos_start: str,
    oos_end: str,
):
    short_list = [3, 5, 7, 10, 12, 15, 20]
    long_list = [20, 30, 40, 50, 60, 80, 100, 120]

    results: List[Dict] = []

    for s in short_list:
        for l in long_list:
            if l <= s:
                continue
            df_bt = backtest_ma_strategy(
                px,
                short_window=s,
                long_window=l,
                trade_cost_bps=trade_cost_bps,
                slip_bps=slip_bps,
                tqqq_expense=tqqq_expense,
            )
            # In-sample equity normalized
            eq_train = compute_period_equity(df_bt, train_start, train_end)
            train_stats = compute_metrics(eq_train)

            # OOS equity normalized
            eq_oos = compute_period_equity(df_bt, oos_start, oos_end)
            oos_stats = compute_metrics(eq_oos)

            trades = int(df_bt["position"].diff().abs().sum())

            results.append(
                dict(
                    short=s,
                    long=l,
                    trades=trades,
                    train_CAGR=train_stats["CAGR"],
                    train_Sharpe=train_stats["Sharpe"],
                    train_MaxDD=train_stats["MaxDD"],
                    train_Calmar=train_stats["Calmar"],
                    oos_CAGR=oos_stats["CAGR"],
                    oos_Sharpe=oos_stats["Sharpe"],
                    oos_MaxDD=oos_stats["MaxDD"],
                    oos_Calmar=oos_stats["Calmar"],
                )
            )

    df_res = pd.DataFrame(results)

    # Rank by train Sharpe (desc), then by train MaxDD (less negative is better -> sort desc)
    df_sorted = df_res.sort_values(
        by=["train_Sharpe", "train_MaxDD"], ascending=[False, False]
    ).reset_index(drop=True)

    top5 = df_sorted.head(5).copy()

    print("==== In-sample (2010-01-01 ~ 2017-12-31) ranking ====")
    print(
        top5[[
            "short",
            "long",
            "trades",
            "train_CAGR",
            "train_Sharpe",
            "train_MaxDD",
            "train_Calmar",
        ]].to_string(index=False, float_format=lambda x: f"{x: .4f}")
    )
    print()

    print("==== OOS (2018-01-01 ~ 2024-12-31) performance of top-5 IS strategies ====")
    print(
        top5[[
            "short",
            "long",
            "trades",
            "oos_CAGR",
            "oos_Sharpe",
            "oos_MaxDD",
            "oos_Calmar",
        ]].to_string(index=False, float_format=lambda x: f"{x: .4f}")
    )
    print()

    return df_sorted, top5


def enhanced_ma_backtest(
    px: pd.Series,
    fast: int,
    slow: int,
    band: float,
    pos_mid: float,
    slope_min: float,
    vol_target: float,
    lev_max: float,
    dd_throttle: float | None,
    trade_cost_bps: float,
    slip_bps: float,
    tqqq_expense: float,
    vol_lookback: int = 20,
) -> dict:
    """
    MA band + slope filter + partial position + vol-target leverage.
    """
    px = px.astype(float)
    ma_fast = px.rolling(fast).mean()
    ma_slow = px.rolling(slow).mean()
    spread = (ma_fast - ma_slow) / ma_slow
    slope = ma_fast.diff().fillna(0.0)

    upper = band
    lower = -band
    raw_pos = np.where(spread > upper, 1.0, np.where(spread < lower, 0.0, np.nan))
    pos_series = pd.Series(raw_pos, index=px.index).fillna(pos_mid)
    if slope_min > 0:
        pos_series[slope < slope_min] = pos_mid if pos_mid > 0 else 0.0

    signal = pos_series.shift(1).fillna(0.0)

    # Vol-target leverage
    ret_raw = px.pct_change().fillna(0.0) - (tqqq_expense / 252.0)
    rv = ret_raw.rolling(vol_lookback).std() * np.sqrt(252.0)
    lev = (vol_target / rv.clip(lower=1e-6)).clip(upper=lev_max)
    lev = lev.fillna(1.0)

    total_cost = (trade_cost_bps + slip_bps) / 10000.0
    equity = [1.0]
    prev_pos = float(signal.iloc[0])
    peak = equity[0]

    for i in range(1, len(px)):
        pos = float(signal.iloc[i])
        if dd_throttle is not None:
            cur_eq = equity[-1]
            peak = max(peak, cur_eq)
            dd = cur_eq / peak - 1.0
            if dd <= -dd_throttle:
                pos = pos_mid
        r = float(ret_raw.iloc[i])
        lev_i = float(lev.iloc[i]) if i < len(lev) else 1.0
        turnover = abs(pos - prev_pos)
        cost = turnover * total_cost
        eq_prev = equity[-1]
        eq_today = eq_prev * (1.0 + prev_pos * lev_i * r - cost)
        equity.append(max(eq_today, 1e-12))
        prev_pos = pos

    equity = pd.Series(equity, index=px.index, name="Equity")
    stats = compute_metrics(equity)
    stats["Trades"] = int(pd.Series(signal).diff().abs().sum())
    stats.update(
        dict(
            pos_mid=pos_mid,
            band=band,
            slope_min=slope_min,
            vol_target=vol_target,
            lev_max=lev_max,
            dd_throttle=dd_throttle,
            fast=fast,
            slow=slow,
        )
    )
    return stats


def run_enhanced_grid(
    px: pd.Series,
    trade_cost_bps: float,
    slip_bps: float,
    tqqq_expense: float,
) -> pd.DataFrame:
    short_list = [5, 10, 15, 20]
    long_list = [50, 80, 120, 150]
    band_list = [0.0, 0.005, 0.01]
    pos_mid_list = [0.0, 0.5]
    slope_list = [0.0, 0.0005]
    vol_targets = [0.7, 0.9]
    lev_caps = [1.5, 2.0]
    dd_throttles: list[float | None] = [None, 0.15]

    rows: list[dict] = []
    for s in short_list:
        for l in long_list:
            if l <= s:
                continue
            for band in band_list:
                for pos_mid in pos_mid_list:
                    for slope_min in slope_list:
                        for vt in vol_targets:
                            for lc in lev_caps:
                                for dd_th in dd_throttles:
                                    stats = enhanced_ma_backtest(
                                        px=px,
                                        fast=s,
                                        slow=l,
                                        band=band,
                                        pos_mid=pos_mid,
                                        slope_min=slope_min,
                                        vol_target=vt,
                                        lev_max=lc,
                                        dd_throttle=dd_th,
                                        trade_cost_bps=trade_cost_bps,
                                        slip_bps=slip_bps,
                                        tqqq_expense=tqqq_expense,
                                    )
                                    rows.append(stats)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="MA grid backtest for TQQQ.")
    parser.add_argument(
        "--mode",
        choices=["simple", "enhanced"],
        default="simple",
        help="simple: original on/off MA; enhanced: band+slope+vol-target grid.",
    )
    parser.add_argument(
        "--tqqq_csv",
        type=str,
        default=None,
        help="Optional TQQQ CSV path (with Date + Adj Close/Close). "
             "If omitted, uses yfinance.",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2010-01-01",
        help="Backtest start date (for data loading).",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2024-12-31",
        help="Backtest end date.",
    )
    parser.add_argument(
        "--train_start",
        type=str,
        default="2010-01-01",
        help="In-sample start date.",
    )
    parser.add_argument(
        "--train_end",
        type=str,
        default="2017-12-31",
        help="In-sample end date.",
    )
    parser.add_argument(
        "--oos_start",
        type=str,
        default="2018-01-01",
        help="Out-of-sample start date.",
    )
    parser.add_argument(
        "--oos_end",
        type=str,
        default="2024-12-31",
        help="Out-of-sample end date.",
    )
    parser.add_argument(
        "--trade_cost_bps",
        type=float,
        default=0.5,
        help="Per-side trade cost in bps (set equal to DL trade_cost_bps).",
    )
    parser.add_argument(
        "--slip_bps",
        type=float,
        default=3.0,
        help="Additional slippage in bps (set equal to DL slip_bps for TQQQ).",
    )
    parser.add_argument(
        "--tqqq_expense",
        type=float,
        default=0.0095,
        help="TQQQ annual expense ratio (e.g. 0.0095 for 0.95%%). "
             "Use same as in DL backtest.",
    )

    args = parser.parse_args()

    px_df = load_tqqq_prices(args.start, args.end, csv_path=args.tqqq_csv)
    px = px_df["TQQQ"]

    print(f"Loaded TQQQ data from {px.index[0].date()} to {px.index[-1].date()}, {len(px)} rows.")

    if args.mode == "simple":
        all_results, top5 = run_grid(
            px=px,
            trade_cost_bps=args.trade_cost_bps,
            slip_bps=args.slip_bps,
            tqqq_expense=args.tqqq_expense,
            train_start=args.train_start,
            train_end=args.train_end,
            oos_start=args.oos_start,
            oos_end=args.oos_end,
        )

        # OOS TQQQ buy&hold as benchmark
        bh_oos = backtest_buy_and_hold(
            px=px,
            start=args.oos_start,
            end=args.oos_end,
            tqqq_expense=args.tqqq_expense,
        )
        print("==== TQQQ Buy&Hold OOS (2018-01-01 ~ 2024-12-31) ====")
        print(
            f"CAGR {bh_oos['CAGR']:.4f}, Vol {bh_oos['Vol']:.4f}, "
            f"Sharpe {bh_oos['Sharpe']:.4f}, MaxDD {bh_oos['MaxDD']:.4f}, "
            f"Calmar {bh_oos['Calmar']:.4f}"
        )
        print()
        print("Now run your DL backtest on 2018-01-01 ~ 2024-12-31 with the same costs,")
        print("and compare its OOS metrics against these top-5 MA strategies and TQQQ.")
    else:
        df = run_enhanced_grid(
            px=px,
            trade_cost_bps=args.trade_cost_bps,
            slip_bps=args.slip_bps,
            tqqq_expense=args.tqqq_expense,
        )
        best_sharpe = df.sort_values("Sharpe", ascending=False).head(5)
        best_calmar = df.sort_values("Calmar", ascending=False).head(5)
        best_cagr = df.sort_values("CAGR", ascending=False).head(5)
        print("==== Enhanced MA (full history) Top Sharpe ====")
        print(best_sharpe.to_string(index=False, float_format=lambda x: f"{x: .4f}"))
        print("\n==== Enhanced MA (full history) Top Calmar ====")
        print(best_calmar.to_string(index=False, float_format=lambda x: f"{x: .4f}"))
        print("\n==== Enhanced MA (full history) Top CAGR ====")
        print(best_cagr.to_string(index=False, float_format=lambda x: f"{x: .4f}"))


if __name__ == "__main__":
    main()
