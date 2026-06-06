#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ma_enhanced_tqqq.py

Hard-coded enhanced MA strategy for TQQQ, using the best params from the
augmented search (band + slope filter + partial position + vol-target leverage).

Defaults (from full-history fit 2010-02-11 ~ 2024-12-30):
  TQQQ (offensive, risk window OFF by default):
    fast=5, slow=80, band=1.2%, pos_mid=0.7, slope_min=0.0005,
    vol_target=0.80, lev_max=3.5, dd_throttle=None, risk_mode=False
  SOXL: 默认切换为 MA（无杠杆、无风险窗），fast=15, slow=150, band=1.0%, pos_mid=0。
  costs: trade_cost_bps=0.5, slip_bps=3.0, expense=0.0095

Usage:
  python ma_enhanced_tqqq.py
  python ma_enhanced_tqqq.py --start 2015-01-01 --end 2025-12-31
"""

import argparse
import math
import os
import sys
from typing import Dict, Optional

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

# Optional plotting
try:
    import matplotlib.pyplot as plt  # type: ignore
    _PLOT_OK = True
except Exception:
    _PLOT_OK = False


def open_file_with_default_viewer(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", path])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def compute_metrics(equity: pd.Series) -> Dict[str, float]:
    equity = equity.astype(float)
    equity = equity.replace([np.inf, -np.inf], np.nan).dropna()
    if len(equity) < 2:
        return dict(CAGR=0.0, Vol=0.0, Sharpe=0.0, MaxDD=0.0, Calmar=0.0)
    start_val = float(equity.iloc[0])
    end_val = float(equity.iloc[-1])
    n_days = (equity.index[-1] - equity.index[0]).days
    n_years = n_days / 365.25 if n_days > 0 else 0.0
    cagr = (end_val / start_val) ** (1.0 / n_years) - 1.0 if n_years > 0 and start_val > 0 else 0.0
    rets = equity.pct_change().dropna()
    vol = rets.std() * math.sqrt(252.0) if len(rets) > 1 else 0.0
    sharpe = rets.mean() / rets.std() * math.sqrt(252.0) if vol > 1e-12 else 0.0
    dd = (equity / equity.cummax()) - 1.0
    maxdd = float(dd.min()) if len(dd) else 0.0
    calmar = cagr / abs(maxdd) if maxdd < 0 else 0.0
    return dict(CAGR=cagr, Vol=vol, Sharpe=sharpe, MaxDD=maxdd, Calmar=calmar)


def load_prices(ticker: str, start: str, end: str) -> pd.Series:
    if yf is None:
        raise RuntimeError("yfinance not available; install via pip install yfinance")
    df = yf.download(ticker, start=start, end=end, progress=False)
    if df is None or df.empty:
        raise RuntimeError(f"Failed to download data for {ticker}.")
    if isinstance(df.columns, pd.MultiIndex):
        if ("Adj Close", ticker) in df.columns:
            px = df[("Adj Close", ticker)]
        elif ("Close", ticker) in df.columns:
            px = df[("Close", ticker)]
        else:
            px = df.xs("Close", level=0, axis=1).iloc[:, 0]
    else:
        if "Adj Close" in df.columns:
            px = df["Adj Close"]
        elif "Close" in df.columns:
            px = df["Close"]
        else:
            px = df.iloc[:, 0]
    px = pd.to_numeric(px, errors="coerce").dropna()
    px.name = ticker
    return px


def run_strategy(
    px: pd.Series,
    fast: int = 5,
    slow: int = 80,
    band: float = 0.012,
    pos_mid: float = 0.7,
    slope_min: float = 0.0005,
    vol_target: float = 0.80,
    lev_max: float = 3.5,
    dd_throttle: Optional[float] = None,
    trade_cost_bps: float = 0.5,
    slip_bps: float = 3.0,
    tqqq_expense: float = 0.0095,
    vol_lookback: int = 20,
    # Risk window overrides (temporary defensive mode)
    risk_mode: bool = False,
    risk_window: int = 5,
    risk_band: Optional[float] = None,
    risk_pos_mid: Optional[float] = None,
    risk_vol_target: Optional[float] = None,
    risk_lev_max: Optional[float] = None,
    risk_drop: float = -0.03,
    risk_vol_ratio: float = 1.5,
) -> tuple[pd.Series, int, pd.Series, pd.Series]:
    px = px.astype(float)
    ret_raw = px.pct_change().fillna(0.0) - (tqqq_expense / 252.0)
    rv_series = ret_raw.rolling(vol_lookback).std() * np.sqrt(252.0)

    total_cost = (trade_cost_bps + slip_bps) / 10000.0
    equity = [1.0]
    trades = 0
    peak = 1.0
    risk_count = 0  # days remaining in risk window (applies to NEXT day)
    pos_track = []
    lev_track = []

    # Precompute MAs once (band may change, but MAs don't)
    ma_fast = px.rolling(fast).mean()
    ma_slow = px.rolling(slow).mean()
    slope_all = ma_fast.diff().fillna(0.0)

    # Initialize with base pos_mid, lev=1.0 for first day
    prev_pos = pos_mid
    lev_live = 1.0
    pos_track.append(prev_pos)
    lev_track.append(lev_live)

    for i in range(1, len(px)):
        # --- Today PnL uses yesterday's position/lev ---
        r = float(ret_raw.iloc[i])
        turnover = 0.0  # will compute after deciding next position

        # --- Decide next-day position and leverage using today's close data ---
        if risk_mode:
            # decay existing window
            risk_count = max(risk_count - 1, 0)
            vol20 = ret_raw.iloc[max(0, i - vol_lookback + 1):i+1].std() * np.sqrt(252.0)
            vol252 = ret_raw.iloc[max(0, i - 252 + 1):i+1].std() * np.sqrt(252.0)
            drop_today = ret_raw.iloc[i]
            trigger = False
            if pd.notna(vol20) and pd.notna(vol252) and vol252 > 0 and (vol20 / vol252) >= risk_vol_ratio:
                trigger = True
            if drop_today <= risk_drop:
                trigger = True
            if trigger:
                risk_count = risk_window  # applies from next day onward
        risk_active_next = risk_mode and (risk_count > 0)

        use_band = risk_band if (risk_active_next and risk_band is not None) else band
        use_pos_mid = risk_pos_mid if (risk_active_next and risk_pos_mid is not None) else pos_mid
        use_vt = risk_vol_target if (risk_active_next and risk_vol_target is not None) else vol_target
        use_lev_cap = risk_lev_max if (risk_active_next and risk_lev_max is not None) else lev_max

        spread = (ma_fast.iloc[i] - ma_slow.iloc[i]) / ma_slow.iloc[i] if ma_slow.iloc[i] else np.nan
        sig_raw = np.nan
        if pd.notna(spread):
            if spread > use_band:
                sig_raw = 1.0
            elif spread < -use_band:
                sig_raw = 0.0
        if pd.isna(sig_raw):
            sig_raw = use_pos_mid
        if slope_min > 0:
            slope_val = slope_all.iloc[i]
            if pd.notna(slope_val) and slope_val < slope_min:
                sig_raw = use_pos_mid if use_pos_mid > 0 else 0.0

        pos_next = float(sig_raw)
        if pos_next != prev_pos:
            trades += 1

        rv_val = rv_series.iloc[i]
        if pd.isna(rv_val) or rv_val <= 0:
            lev_next = 1.0
        else:
            lev_next = min(use_lev_cap, use_vt / max(rv_val, 1e-6))

        # Drawdown throttle (static check) influences next position
        if dd_throttle is not None:
            cur_eq = equity[-1]
            peak = max(peak, cur_eq)
            dd = cur_eq / peak - 1.0
            if dd <= -dd_throttle:
                pos_next = use_pos_mid

        # Trading cost for adjusting to next position today
        turnover = abs(pos_next - prev_pos)
        cost = turnover * total_cost

        # PnL for today uses yesterday's pos/lev, cost applied today
        eq_prev = equity[-1]
        eq_today = eq_prev * (1.0 + prev_pos * lev_live * r - cost)
        equity.append(max(eq_today, 1e-12))

        # roll to next day
        prev_pos = pos_next
        lev_live = lev_next
        pos_track.append(prev_pos)
        lev_track.append(lev_live)

    pos_series = pd.Series(pos_track, index=px.index, name="Position")
    lev_series = pd.Series(lev_track, index=px.index, name="Leverage")
    return pd.Series(equity, index=px.index, name="Equity"), trades, pos_series, lev_series


def print_summary_block(title: str, stats: Dict[str, float], trades: int, final_eq: float,
                        bh_stats: Dict[str, float], bh_final_eq: float, bench_label: str) -> None:
    print(f"--- {title} ---")
    print(f"CAGR: {stats.get('CAGR', float('nan')): .4f}")
    print(f"Vol: {stats.get('Vol', float('nan')): .4f}")
    print(f"Sharpe_ex_rf0: {stats.get('Sharpe', float('nan')): .4f}")
    print(f"MaxDD: {stats.get('MaxDD', float('nan')): .4f}")
    print(f"Calmar: {stats.get('Calmar', float('nan')): .4f}")
    print(f"Trades: {float(trades): .4f}")
    print(f"Final_Equity: {final_eq: .4f}")
    print(f"{bench_label}_CAGR: {bh_stats.get('CAGR', float('nan')): .4f}")
    print(f"{bench_label}_Vol: {bh_stats.get('Vol', float('nan')): .4f}")
    print(f"{bench_label}_Sharpe: {bh_stats.get('Sharpe', float('nan')): .4f}")
    print(f"{bench_label}_MaxDD: {bh_stats.get('MaxDD', float('nan')): .4f}")
    print(f"{bench_label}_Calmar: {bh_stats.get('Calmar', float('nan')): .4f}")
    print(f"{bench_label}_Final_Equity: {bh_final_eq: .4f}")


def plot_comparison(curves: Dict[str, pd.Series], out_path: str) -> Optional[str]:
    if not _PLOT_OK:
        print("[plot] matplotlib not available; skipping plot.")
        return None
    if not curves:
        return None
    try:
        # Union index and forward-fill to align
        idx = sorted(set().union(*[series.index for series in curves.values()]))
        df = pd.DataFrame({k: v.reindex(idx).ffill() for k, v in curves.items()})
        df = df.dropna(how="all")
        # Normalize each column by its own first valid point
        first_valid = df.apply(lambda s: s.loc[s.first_valid_index()] if s.first_valid_index() is not None else np.nan)
        norm = df.divide(first_valid, axis=1)
        dd = norm / norm.cummax() - 1.0
        fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
        norm.plot(ax=ax[0], lw=1.2)
        ax[0].set_title("Equity (normalized)")
        ax[0].grid(True, alpha=0.3)
        dd.plot(ax=ax[1], lw=1.0)
        ax[1].set_title("Drawdowns")
        ax[1].grid(True, alpha=0.3)
        plt.tight_layout()
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[plot] Saved: {out_path}")
        return out_path
    except Exception as exc:
        print(f"[plot] Failed: {exc}")
        return None


def run_grid_search(px: pd.Series) -> None:
    """
    Quick grid for TQQQ to balance防守与收益（无前视版本）。
    Criteria: primary Calmar, secondary CAGR, then Sharpe.
    """
    candidates = []
    bands = [0.012, 0.015, 0.018]
    pos_mids = [0.5, 0.6, 0.7]  # 更进攻，减少空仓
    vol_targets = [0.80, 0.90, 1.00]
    lev_caps = [3.5, 4.0, 4.5]
    risk_flags = [False]  # 关闭风险窗，纯进攻
    risk_params = dict(risk_window=5, risk_band=0.035, risk_pos_mid=0.0,
                       risk_vol_target=0.55, risk_lev_max=1.6, risk_drop=-0.03, risk_vol_ratio=1.5)
    for b in bands:
        for pm in pos_mids:
            for vt in vol_targets:
                for lv in lev_caps:
                    for rf in risk_flags:
                        eq, trades, _, _ = run_strategy(
                            px,
                            fast=5, slow=80, band=b, pos_mid=pm, slope_min=0.0005,
                            vol_target=vt, lev_max=lv, dd_throttle=None,
                            tqqq_expense=0.0095,
                            risk_mode=rf,
                            **risk_params
                        )
                        stats = compute_metrics(eq)
                        row = dict(
                            band=b, pos_mid=pm, vol_target=vt, lev_max=lv,
                            risk_mode=rf, trades=trades,
                            CAGR=stats.get("CAGR", 0.0),
                            Vol=stats.get("Vol", 0.0),
                            Sharpe=stats.get("Sharpe", 0.0),
                            MaxDD=stats.get("MaxDD", 0.0),
                            Calmar=stats.get("Calmar", 0.0),
                            Final=eq.iloc[-1]
                        )
                        candidates.append(row)
    df = pd.DataFrame(candidates)
    df.sort_values(by=["Calmar", "CAGR", "Sharpe"], ascending=[False, False, False], inplace=True)
    best = df.head(10)
    print("\n[Grid] Top 10 by Calmar -> CAGR -> Sharpe")
    print(best.to_string(index=False, formatters={
        "CAGR": "{:.4f}".format,
        "Vol": "{:.4f}".format,
        "Sharpe": "{:.4f}".format,
        "MaxDD": "{:.4f}".format,
        "Calmar": "{:.4f}".format,
        "Final": "{:.2f}".format
    }))
    best.to_csv("ma_enhanced_grid_top10.csv", index=False)
    print("Saved grid top10 to ma_enhanced_grid_top10.csv")


def run_soxl_ma_grid(px: pd.Series, expense: float = 0.0095, advanced: bool = False) -> None:
    """
    MA grid search for SOXL. If advanced=True, also scans leverage/vol_target to
    try to beat buy&hold with controlled leverage.
    """
    candidates = []
    fast_list = [5, 8, 10, 15]
    slow_list = [80, 120, 150, 200]
    band_list = [0.0, 0.01, 0.02, 0.03]
    pos_mid_list = [0.0, 0.3, 0.6]
    vol_targets = [1.0] if not advanced else [0.8, 1.0, 1.2]
    lev_caps = [1.0] if not advanced else [1.0, 1.5, 2.0]
    for f in fast_list:
        for s in slow_list:
            if s <= f:
                continue
            for b in band_list:
                for pm in pos_mid_list:
                    for vt in vol_targets:
                        for lv in lev_caps:
                            eq, trades, _, _ = run_strategy(
                                px,
                                fast=f,
                                slow=s,
                                band=b,
                                pos_mid=pm,
                                slope_min=0.0,
                                vol_target=vt,
                                lev_max=lv,
                                dd_throttle=None,
                                trade_cost_bps=0.5,
                                slip_bps=3.0,
                                tqqq_expense=expense,
                                risk_mode=False,
                                risk_window=0,
                                risk_band=None,
                                risk_pos_mid=None,
                                risk_vol_target=None,
                                risk_lev_max=None,
                            )
                            stats = compute_metrics(eq)
                            candidates.append({
                                "fast": f,
                                "slow": s,
                                "band": b,
                                "pos_mid": pm,
                                "vol_target": vt,
                                "lev_max": lv,
                                "CAGR": stats.get("CAGR", 0.0),
                                "Vol": stats.get("Vol", 0.0),
                                "Sharpe": stats.get("Sharpe", 0.0),
                                "MaxDD": stats.get("MaxDD", 0.0),
                                "Calmar": stats.get("Calmar", 0.0),
                                "Trades": trades,
                                "Final": eq.iloc[-1],
                            })
    df = pd.DataFrame(candidates)
    df.sort_values(by=["Calmar", "CAGR", "Sharpe"], ascending=[False, False, False], inplace=True)
    best = df.head(15)
    print(f"\n[SOXL MA Grid{' (adv)' if advanced else ''}] Top 15 by Calmar -> CAGR -> Sharpe")
    print(best.to_string(index=False, formatters={
        "CAGR": "{:.4f}".format,
        "Vol": "{:.4f}".format,
        "Sharpe": "{:.4f}".format,
        "MaxDD": "{:.4f}".format,
        "Calmar": "{:.4f}".format,
        "Final": "{:.2f}".format
    }))
    out_name = "soxl_ma_grid_top15_adv.csv" if advanced else "soxl_ma_grid_top15.csv"
    best.to_csv(out_name, index=False)
    print(f"Saved grid to {out_name}")


def run_soxl_breakout(
    px: pd.Series,
    trade_cost_bps: float = 0.5,
    slip_bps: float = 3.0,
    tqqq_expense: float = 0.0095,
    atr_window: int = 20,
    atr_thresh: float = 0.12,
    risk_drop: float = -0.06,
    risk_vol_ratio: float = 1.5,
    dd_step1: float = 0.20,
    dd_step2: float = 0.30,
    pos_full: float = 1.0,
    pos_mid: float = 0.60,
    pos_low: float = 0.0,
    max_hold: int = 60,
    trail_lookback: int = 200,
) -> tuple[pd.Series, int, pd.Series, pd.Series]:
    """
    SOXL 专用突破+压缩+回撤阶梯策略（无杠杆）。
    - 入场：价>EMA120 且 EMA20>EMA60 且 ATR20/Close<atr_thresh。
    - 风险：当日跌幅<=risk_drop 或 vol20/vol252>=risk_vol_ratio，则次日清仓。
    - 回撤阶梯：相对 trail_lookback 高点的回撤>dd_step2 清仓；>dd_step1 降到 pos_mid。
    - 持仓超 max_hold 且未创新高则减半。
    - 当日信号用于下一日持仓，PNL 使用前一日仓位。
    """
    px = px.astype(float)
    ret = px.pct_change().fillna(0.0) - (tqqq_expense / 252.0)
    ema20 = px.ewm(span=20, adjust=False).mean()
    ema60 = px.ewm(span=60, adjust=False).mean()
    ema120 = px.ewm(span=120, adjust=False).mean()
    high_trail = px.rolling(trail_lookback, min_periods=1).max()
    tr = pd.concat([
        (px - px.shift(1)).abs(),
        (px - px.shift(1)).abs()  # simple proxy for high-low since only close available
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_window).mean().fillna(0.0)
    atr_ratio = atr / px.replace(0, np.nan)
    vol20 = ret.rolling(20).std() * math.sqrt(252.0)
    vol252 = ret.rolling(252).std() * math.sqrt(252.0)

    total_cost = (trade_cost_bps + slip_bps) / 10000.0
    equity = [1.0]
    pos_track = []
    lev_track = []
    trades = 0
    prev_pos = 0.0
    hold_days = 0

    # init
    pos_track.append(prev_pos)
    lev_track.append(1.0)

    for i in range(1, len(px)):
        r = ret.iloc[i]
        # decide next position using today's close
        risk_flag = False
        if pd.notna(vol20.iloc[i]) and pd.notna(vol252.iloc[i]) and vol252.iloc[i] > 0:
            if (vol20.iloc[i] / vol252.iloc[i]) >= risk_vol_ratio:
                risk_flag = True
        if r <= risk_drop:
            risk_flag = True

        trend_ok = (px.iloc[i] > ema120.iloc[i]) and (ema20.iloc[i] > ema60.iloc[i])
        vol_ok = (atr_ratio.iloc[i] < atr_thresh) if pd.notna(atr_ratio.iloc[i]) else False
        base_pos = pos_full if (trend_ok and vol_ok and not risk_flag) else 0.0

        # drawdown gating (applies to next day)
        dd = (px.iloc[i] / high_trail.iloc[i]) - 1.0 if high_trail.iloc[i] > 0 else 0.0
        if dd <= -dd_step2:
            base_pos = pos_low
            hold_days = 0
        elif dd <= -dd_step1:
            base_pos = min(base_pos, pos_mid)

        # holding time decay
        if prev_pos > 0:
            hold_days += 1
        else:
            hold_days = 0
        if hold_days >= max_hold and base_pos > 0:
            base_pos = base_pos * 0.5

        pos_next = base_pos
        if pos_next != prev_pos:
            trades += 1

        # PnL with yesterday position
        cost = abs(pos_next - prev_pos) * total_cost * equity[-1]
        eq_today = equity[-1] * (1.0 + prev_pos * r)
        eq_today = max(eq_today - cost, 1e-12)
        equity.append(eq_today)
        prev_pos = pos_next
        pos_track.append(prev_pos)
        lev_track.append(1.0)  # no leverage

    pos_series = pd.Series(pos_track, index=px.index, name="Position")
    lev_series = pd.Series(lev_track, index=px.index, name="Leverage")
    return pd.Series(equity, index=px.index, name="Equity"), trades, pos_series, lev_series


def main():
    ap = argparse.ArgumentParser(description="Enhanced MA strategy (hard-coded best params).")
    ap.add_argument("--ticker", type=str, default="TQQQ", help="Ticker to run (default TQQQ).")
    ap.add_argument("--start", type=str, default="2010-02-11")
    ap.add_argument("--end", type=str, default="2025-12-31")
    ap.add_argument("--print-metrics", dest="print_metrics", action="store_true", help="Print CAGR/Vol/Sharpe/MaxDD/Calmar.")
    ap.add_argument("--plot", dest="plot", action="store_true", default=True, help="Generate comparison plot (default on).")
    ap.add_argument("--no-plot", dest="plot", action="store_false", help="Disable plot generation.")
    ap.add_argument("--open", dest="open_plot", action="store_true", default=True, help="Open plot after saving (default on).")
    ap.add_argument("--no-open", dest="open_plot", action="store_false", help="Do not open plot.")
    ap.add_argument("--compare-both", action="store_true", help="Run both TQQQ and SOXL presets and plot together.")
    ap.add_argument("--include-soxl", dest="include_soxl", action="store_true", default=True,
                    help="When not using --compare-both, also overlay SOXL strategy/buy&hold in the plot (default on).")
    ap.add_argument("--no-include-soxl", dest="include_soxl", action="store_false",
                    help="Do not overlay SOXL when not using --compare-both.")
    # Optional overrides for TQQQ (for quick parameter experiments)
    ap.add_argument("--fast", type=int, default=None, help="Override TQQQ fast MA.")
    ap.add_argument("--slow", type=int, default=None, help="Override TQQQ slow MA.")
    ap.add_argument("--band", type=float, default=None, help="Override TQQQ band (e.g., 0.015 = 1.5%).")
    ap.add_argument("--pos-mid", type=float, dest="pos_mid", default=None, help="Override TQQQ pos_mid.")
    ap.add_argument("--vol-target", type=float, dest="vol_target", default=None, help="Override TQQQ vol target.")
    ap.add_argument("--lev-max", type=float, dest="lev_max", default=None, help="Override TQQQ leverage cap.")
    ap.add_argument("--dd-throttle", type=float, dest="dd_throttle", default=None, help="Override TQQQ dd_throttle.")
    ap.add_argument("--grid-search", dest="grid_search", action="store_true",
                    help="Run a small grid search for TQQQ params and print ranked results.")
    ap.add_argument("--soxl-breakout", dest="soxl_breakout", action="store_true", default=False,
                    help="Use SOXL breakout strategy instead of MA (default off).")
    ap.add_argument("--no-soxl-breakout", dest="soxl_breakout", action="store_false",
                    help="Disable breakout and use MA for SOXL (default).")
    ap.add_argument("--soxl-ma-grid", dest="soxl_ma_grid", action="store_true",
                    help="Run simple MA grid search for SOXL (no leverage, no risk window).")
    ap.add_argument("--soxl-ma-grid-adv", dest="soxl_ma_grid_adv", action="store_true",
                    help="Run advanced SOXL MA grid (scans vol_target/lev_max).")
    # Risk window (temporary defensive mode)
    ap.add_argument("--risk-mode", dest="risk_mode", action="store_true", default=None,
                    help="Enable risk window (default OFF for TQQQ, ON for SOXL when not specified).")
    ap.add_argument("--no-risk-mode", dest="risk_mode", action="store_false", help="Disable risk window.")
    ap.add_argument("--risk-window", type=int, default=5, help="Risk window length in days.")
    ap.add_argument("--risk-band", type=float, default=0.02, help="Band during risk window (None to keep base).")
    ap.add_argument("--risk-pos-mid", type=float, default=0.0, help="pos_mid during risk window (None to keep base).")
    ap.add_argument("--risk-vol-target", type=float, default=0.45, help="Vol target during risk window (None to keep base).")
    ap.add_argument("--risk-lev-max", type=float, default=1.3, help="Leverage cap during risk window (None to keep base).")
    ap.add_argument("--risk-drop", type=float, default=-0.03, help="Trigger if daily return <= this (e.g., -0.03).")
    ap.add_argument("--risk-vol-ratio", type=float, default=1.5, help="Trigger if vol20/vol252 >= this.")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    risk_mode_flag = args.risk_mode  # None = use defaults per ticker
    # presets per ticker
    if ticker == "TQQQ":
        t_fast = args.fast if args.fast is not None else 5
        t_slow = args.slow if args.slow is not None else 80
        t_band = args.band if args.band is not None else 0.012
        t_pos_mid = args.pos_mid if args.pos_mid is not None else 0.7
        t_vol_target = args.vol_target if args.vol_target is not None else 0.80
        t_lev_max = args.lev_max if args.lev_max is not None else 3.5
        t_dd_throttle = args.dd_throttle if args.dd_throttle is not None else None
        params = dict(
            fast=t_fast, slow=t_slow, band=t_band, pos_mid=t_pos_mid, slope_min=0.0005,
            vol_target=t_vol_target, lev_max=t_lev_max, dd_throttle=t_dd_throttle,
            risk_mode=False if risk_mode_flag is None else risk_mode_flag,
            risk_window=args.risk_window if args.risk_window is not None else 5,
            risk_band=args.risk_band, risk_pos_mid=args.risk_pos_mid,
            risk_vol_target=args.risk_vol_target, risk_lev_max=args.risk_lev_max,
            risk_drop=args.risk_drop, risk_vol_ratio=args.risk_vol_ratio,
        )
        expense = 0.0095
        out_prefix = "ma_enhanced_tqqq"
    elif ticker == "SOXL":
        use_breakout = bool(args.soxl_breakout)
        if not use_breakout:
            params = dict(
                fast=15, slow=150, band=0.01, pos_mid=0.0, slope_min=0.0,
                vol_target=1.0, lev_max=1.0, dd_throttle=None,
                risk_mode=False,
                risk_window=0,
                risk_band=None,
                risk_pos_mid=None,
                risk_vol_target=None,
                risk_lev_max=None,
                risk_drop=args.risk_drop,
                risk_vol_ratio=args.risk_vol_ratio,
            )
        else:
            params = None  # breakout path
        expense = 0.0095  # use same expense; adjust if you have actual SOXL ER
        out_prefix = "ma_enhanced_soxl"
    else:
        raise SystemExit(f"Unsupported preset for ticker {ticker}; use TQQQ or SOXL.")

    if args.compare_both:
        # Run both TQQQ and SOXL presets in one shot
        t_fast = args.fast if args.fast is not None else 5
        t_slow = args.slow if args.slow is not None else 80
        t_band = args.band if args.band is not None else 0.012
        t_pos_mid = args.pos_mid if args.pos_mid is not None else 0.7
        t_vol_target = args.vol_target if args.vol_target is not None else 0.80
        t_lev_max = args.lev_max if args.lev_max is not None else 3.5
        t_dd_throttle = args.dd_throttle if args.dd_throttle is not None else None
        use_breakout = bool(args.soxl_breakout)
        presets = [
            ("TQQQ", dict(
                fast=t_fast, slow=t_slow, band=t_band, pos_mid=t_pos_mid, slope_min=0.0005,
                vol_target=t_vol_target, lev_max=t_lev_max, dd_throttle=t_dd_throttle,
                risk_mode=False if risk_mode_flag is None else risk_mode_flag,
                risk_window=args.risk_window if args.risk_window is not None else 5,
                risk_band=args.risk_band, risk_pos_mid=args.risk_pos_mid,
                risk_vol_target=args.risk_vol_target, risk_lev_max=args.risk_lev_max,
                risk_drop=args.risk_drop, risk_vol_ratio=args.risk_vol_ratio,
            ), 0.0095, "ma_enhanced_tqqq"),
            ("SOXL", dict(
                fast=15, slow=150, band=0.01, pos_mid=0.0, slope_min=0.0,
                vol_target=1.0, lev_max=1.0, dd_throttle=None,
                risk_mode=False,
                risk_window=0,
                risk_band=None,
                risk_pos_mid=None,
                risk_vol_target=None,
                risk_lev_max=None,
                risk_drop=args.risk_drop, risk_vol_ratio=args.risk_vol_ratio,
            ) if not use_breakout else None, 0.0095, "ma_enhanced_soxl"),
        ]
        curves = {}
        for tk, p, exp, prefix in presets:
            px = load_prices(tk, args.start, args.end)
            if p is None:  # breakout for SOXL
                eq, trades, pos_ser, lev_ser = run_soxl_breakout(px, trade_cost_bps=0.5, slip_bps=3.0, tqqq_expense=exp)
            else:
                eq, trades, pos_ser, lev_ser = run_strategy(px, tqqq_expense=exp, **p)
            ret_bh = px.pct_change().fillna(0.0) - (exp / 252.0)
            eq_bh = (1.0 + ret_bh).cumprod()
            curves[f"{tk} Strategy"] = eq
            curves[f"{tk} Buy&Hold"] = eq_bh
            if args.print_metrics:
                stats_strat = compute_metrics(eq)
                stats_bh = compute_metrics(eq_bh)
                print(f"\n[{tk}] rows={len(eq)} start={eq.index[0].date()} end={eq.index[-1].date()}")
                print_summary_block(f"{tk}", stats_strat, trades, eq.iloc[-1], stats_bh, eq_bh.iloc[-1], f"{tk}_BH")
            out = pd.DataFrame({tk: px, "Equity": eq, "BuyHold": eq_bh, "Position": pos_ser, "Leverage": lev_ser})
            out.to_csv(f"{prefix}_equity.csv")
            print(f"[{tk}] Saved equity to {prefix}_equity.csv")
        if args.plot:
            plot_path = plot_comparison(curves, "ma_enhanced_compare.png")
            if plot_path and args.open_plot:
                open_file_with_default_viewer(plot_path)
        return

    if args.grid_search:
        # Only TQQQ grid on provided start/end
        px = load_prices("TQQQ", args.start, args.end)
        run_grid_search(px)
        return
    if args.soxl_ma_grid_adv:
        px = load_prices("SOXL", args.start, args.end)
        run_soxl_ma_grid(px, expense=0.0095, advanced=True)
        return
    if args.soxl_ma_grid:
        px = load_prices("SOXL", args.start, args.end)
        run_soxl_ma_grid(px, expense=0.0095)
        return

    px = load_prices(ticker, args.start, args.end)
    if ticker == "SOXL" and params is None:
        eq, trades, pos_ser, lev_ser = run_soxl_breakout(px, trade_cost_bps=0.5, slip_bps=3.0, tqqq_expense=expense)
    else:
        eq, trades, pos_ser, lev_ser = run_strategy(px, tqqq_expense=expense, **params)

    # Buy & hold benchmark
    ret_bh = px.pct_change().fillna(0.0) - (expense / 252.0)
    eq_bh = (1.0 + ret_bh).cumprod()
    stats_strat = compute_metrics(eq)
    stats_bh = compute_metrics(eq_bh)

    if args.print_metrics:
        print(f"Equity rows: {len(eq)}, start {eq.index[0].date()} end {eq.index[-1].date()}")
        print_summary_block(ticker, stats_strat, trades, eq.iloc[-1], stats_bh, eq_bh.iloc[-1], f"{ticker}_BH")

    out = pd.DataFrame({ticker: px, "Equity": eq, "BuyHold": eq_bh, "Position": pos_ser, "Leverage": lev_ser})
    out.to_csv(f"{out_prefix}_equity.csv")
    print(f"Saved equity to {out_prefix}_equity.csv")
    if args.plot:
        curves = {f"{ticker} Strategy": eq, f"{ticker} Buy&Hold": eq_bh}
        if args.include_soxl and ticker != "SOXL":
            # Also overlay SOXL using its preset
            px_s = load_prices("SOXL", args.start, args.end)
            params_s = dict(
                fast=8, slow=150, band=0.03, pos_mid=0.0, slope_min=0.0,
                vol_target=0.90, lev_max=3.0, dd_throttle=None,
                risk_mode=True if risk_mode_flag is None else risk_mode_flag,
                risk_window=args.risk_window if args.risk_window is not None else 5,
                risk_band=args.risk_band if args.risk_band is not None else 0.04,
                risk_pos_mid=args.risk_pos_mid if args.risk_pos_mid is not None else 0.0,
                risk_vol_target=args.risk_vol_target if args.risk_vol_target is not None else 0.60,
                risk_lev_max=args.risk_lev_max if args.risk_lev_max is not None else 1.80,
                risk_drop=args.risk_drop, risk_vol_ratio=args.risk_vol_ratio,
            )
            eq_s, trades_s, pos_s, lev_s = run_strategy(px_s, tqqq_expense=0.0095, **params_s)
            ret_bh_s = px_s.pct_change().fillna(0.0) - (0.0095 / 252.0)
            eq_bh_s = (1.0 + ret_bh_s).cumprod()
            curves["SOXL Strategy"] = eq_s
            curves["SOXL Buy&Hold"] = eq_bh_s
            out_s = pd.DataFrame({"SOXL": px_s, "Equity": eq_s, "BuyHold": eq_bh_s, "Position": pos_s, "Leverage": lev_s})
            out_s.to_csv("ma_enhanced_soxl_equity.csv")
            print("[SOXL] Saved equity to ma_enhanced_soxl_equity.csv")
        plot_path = plot_comparison(curves, f"{out_prefix}_plot.png")
        if plot_path and args.open_plot:
            open_file_with_default_viewer(plot_path)


if __name__ == "__main__":
    main()
