#!/usr/bin/env python
"""Minimal strict-OOS TQQQ / QQQ / cash risk-management baselines.

This module intentionally avoids the complex strategy stack: no DL, no Bandit,
no Kelly, no QQQ5, and no synthetic products. Signals are daily close signals
that are shifted one bar before they affect returns.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from qqq_deep_learning_and_baseline_experimental import (
    compute_performance_metrics,
    get_close_series_from_yf,
)


PERIODS_PER_YEAR = 252
DEFAULT_RF_ANN = 0.02


def _require_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise ValueError("df must contain QQQ and TQQQ prices.")
    missing = {"QQQ", "TQQQ"} - set(df.columns)
    if missing:
        raise ValueError(f"df missing required columns: {sorted(missing)}")
    out = df[["QQQ", "TQQQ"]].copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out.apply(pd.to_numeric, errors="coerce").dropna()
    if out.empty:
        raise ValueError("df has no valid QQQ/TQQQ rows after cleaning.")
    return out


def _normalize(series: pd.Series) -> pd.Series:
    ser = pd.Series(series).dropna().astype(float)
    if ser.empty:
        return ser
    first = float(ser.iloc[0])
    if not np.isfinite(first) or abs(first) <= 1e-12:
        return ser
    return ser / first


def _price_returns(df: pd.DataFrame) -> pd.DataFrame:
    px = _require_price_frame(df)
    return px.pct_change().fillna(0.0)


def _equity_from_returns(daily_returns: pd.Series) -> pd.Series:
    ret = pd.Series(daily_returns).fillna(0.0).astype(float)
    return (1.0 + ret).cumprod().rename("Equity")


def _weights_frame(index, w_tqqq, w_qqq, w_cash, strategy: str) -> pd.DataFrame:
    weights = pd.DataFrame(
        {
            "w_tqqq": pd.Series(w_tqqq, index=index, dtype=float),
            "w_qqq": pd.Series(w_qqq, index=index, dtype=float),
            "w_cash": pd.Series(w_cash, index=index, dtype=float),
        },
        index=index,
    ).fillna(0.0)
    weights["effective_leverage"] = 3.0 * weights["w_tqqq"] + weights["w_qqq"]
    weights["strategy"] = strategy
    if (weights[["w_tqqq", "w_qqq", "w_cash"]] < -1e-12).any().any():
        raise ValueError("weights must be non-negative.")
    if (weights[["w_tqqq", "w_qqq", "w_cash"]].sum(axis=1) > 1.0 + 1e-10).any():
        raise ValueError("weights must sum to <= 1.0.")
    if weights["effective_leverage"].max() > 3.0 + 1e-10:
        raise ValueError("effective leverage exceeds 3x.")
    return weights


def _result_from_weights(df: pd.DataFrame, weights: pd.DataFrame, strategy: str) -> dict:
    ret = _price_returns(df)
    daily = weights["w_tqqq"].reindex(ret.index).fillna(0.0) * ret["TQQQ"]
    daily = daily + weights["w_qqq"].reindex(ret.index).fillna(0.0) * ret["QQQ"]
    equity = _equity_from_returns(daily)
    tqqq_equity = _equity_from_returns(ret["TQQQ"]).rename("TQQQ_BuyHold")
    report = compute_performance_metrics(
        equity,
        benchmark_equity=tqqq_equity,
        rf_ann=DEFAULT_RF_ANN,
        periods_per_year=PERIODS_PER_YEAR,
    )
    report.update(
        {
            "Strategy": strategy,
            "AvgEffectiveLeverage": float(weights["effective_leverage"].mean()),
            "MaxEffectiveLeverage": float(weights["effective_leverage"].max()),
            "QQQ5_Used": False,
        }
    )
    return {
        "strategy": strategy,
        "equity": equity,
        "weights": weights,
        "daily_returns": daily.rename("DailyReturn"),
        "report": report,
        "benchmark_equity": tqqq_equity,
    }


def _confirmed_ma_signal(price: pd.Series, ma: pd.Series, confirmation_days: int = 1) -> pd.Series:
    """Close-only MA regime target, optionally requiring consecutive confirmations."""
    confirmation_days = int(confirmation_days)
    if confirmation_days < 1:
        raise ValueError("confirmation_days must be >= 1.")
    above = (price > ma).fillna(False)
    if confirmation_days == 1:
        return above.astype(float)

    state = False
    above_count = 0
    below_count = 0
    out = []
    for is_above in above.astype(bool):
        if is_above:
            above_count += 1
            below_count = 0
        else:
            below_count += 1
            above_count = 0
        if not state and above_count >= confirmation_days:
            state = True
        elif state and below_count >= confirmation_days:
            state = False
        out.append(1.0 if state else 0.0)
    return pd.Series(out, index=price.index, dtype=float)


def run_static_blend_backtest(
    df: pd.DataFrame,
    tqqq_weight: float,
    qqq_weight: float,
    cash_weight: float,
) -> dict:
    """Run a fixed TQQQ / QQQ / cash blend."""
    total = float(tqqq_weight) + float(qqq_weight) + float(cash_weight)
    if any(float(x) < -1e-12 for x in [tqqq_weight, qqq_weight, cash_weight]):
        raise ValueError("static blend weights must be non-negative.")
    if abs(total - 1.0) > 1e-10:
        raise ValueError("static blend weights must sum to 1.")
    px = _require_price_frame(df)
    strategy = f"static_{int(round(tqqq_weight * 100))}tqqq_{int(round(qqq_weight * 100))}qqq_{int(round(cash_weight * 100))}cash"
    weights = _weights_frame(
        px.index,
        float(tqqq_weight),
        float(qqq_weight),
        float(cash_weight),
        strategy,
    )
    return _result_from_weights(px, weights, strategy)


def run_ma_regime_backtest(df: pd.DataFrame, fast_or_slow_config=None) -> dict:
    """Hold TQQQ when QQQ is above its MA; otherwise hold QQQ or cash.

    The close-based MA signal is shifted one day before it affects returns.
    """
    cfg = dict(fast_or_slow_config or {})
    ma_window = int(cfg.get("ma_window", cfg.get("slow_window", 200)))
    risk_off_asset = str(cfg.get("risk_off_asset", "cash")).lower()
    confirmation_days = int(cfg.get("confirmation_days", 1))
    if risk_off_asset not in {"cash", "qqq"}:
        raise ValueError("risk_off_asset must be 'cash' or 'qqq'.")
    px = _require_price_frame(df)
    ma = px["QQQ"].rolling(ma_window).mean()
    raw_signal = _confirmed_ma_signal(px["QQQ"], ma, confirmation_days)
    risk_on_for_pnl = raw_signal.shift(1).fillna(0.0)
    w_tqqq = risk_on_for_pnl
    w_qqq = (1.0 - risk_on_for_pnl) if risk_off_asset == "qqq" else 0.0
    w_cash = 1.0 - w_tqqq - (w_qqq if isinstance(w_qqq, pd.Series) else 0.0)
    strategy = f"ma_regime_qqq_ma{ma_window}_riskoff_{risk_off_asset}"
    if confirmation_days > 1:
        strategy = f"{strategy}_confirm{confirmation_days}d"
    weights = _weights_frame(px.index, w_tqqq, w_qqq, w_cash, strategy)
    weights["raw_signal"] = raw_signal
    weights["signal_lagged"] = risk_on_for_pnl
    weights["confirmation_days"] = confirmation_days
    return _result_from_weights(px, weights, strategy)


def run_vol_target_backtest(df: pd.DataFrame, target_vol: float, vol_window: int) -> dict:
    """Vol-targeted TQQQ with one-day lag and 0%-100% TQQQ weight cap."""
    px = _require_price_frame(df)
    ret = _price_returns(px)
    realized_vol = ret["TQQQ"].rolling(int(vol_window)).std() * math.sqrt(PERIODS_PER_YEAR)
    raw_weight = (float(target_vol) / realized_vol).replace([np.inf, -np.inf], np.nan)
    w_tqqq = raw_weight.shift(1).clip(lower=0.0, upper=1.0).fillna(0.0)
    w_qqq = pd.Series(0.0, index=px.index)
    w_cash = 1.0 - w_tqqq
    strategy = f"vol_target_tqqq_{int(round(target_vol * 100))}vol_{int(vol_window)}d"
    weights = _weights_frame(px.index, w_tqqq, w_qqq, w_cash, strategy)
    weights["realized_vol"] = realized_vol
    weights["raw_tqqq_weight"] = raw_weight
    return _result_from_weights(px, weights, strategy)


def run_drawdown_control_backtest(
    df: pd.DataFrame,
    drawdown_threshold: float,
    recovery_rule=None,
) -> dict:
    """Simple QQQ drawdown control with one-day lag.

    Default rule: risk off when QQQ drawdown is worse than threshold, then
    return to risk-on when drawdown recovers to half the threshold.
    """
    cfg = dict(recovery_rule or {})
    risk_off_asset = str(cfg.get("risk_off_asset", "qqq")).lower()
    risk_off_tqqq_weight = float(cfg.get("risk_off_tqqq_weight", 0.0))
    recovery_fraction = float(cfg.get("recovery_fraction", 0.5))
    if risk_off_asset not in {"cash", "qqq"}:
        raise ValueError("risk_off_asset must be 'cash' or 'qqq'.")
    if not 0.0 <= risk_off_tqqq_weight <= 1.0:
        raise ValueError("risk_off_tqqq_weight must be between 0 and 1.")
    if not 0.0 < recovery_fraction <= 1.0:
        raise ValueError("recovery_fraction must be in (0, 1].")

    px = _require_price_frame(df)
    threshold = abs(float(drawdown_threshold))
    qqq_drawdown = px["QQQ"] / px["QQQ"].cummax() - 1.0
    risk_on_target = []
    risk_on = True
    for dd in qqq_drawdown:
        if risk_on and dd <= -threshold:
            risk_on = False
        elif not risk_on and dd >= -(threshold * recovery_fraction):
            risk_on = True
        risk_on_target.append(1.0 if risk_on else 0.0)
    target = pd.Series(risk_on_target, index=px.index)
    risk_on_for_pnl = target.shift(1).fillna(1.0)
    w_tqqq = risk_on_for_pnl + (1.0 - risk_on_for_pnl) * risk_off_tqqq_weight
    if risk_off_asset == "qqq":
        w_qqq = (1.0 - risk_on_for_pnl) * (1.0 - risk_off_tqqq_weight)
        w_cash = 1.0 - w_tqqq - w_qqq
    else:
        w_qqq = pd.Series(0.0, index=px.index)
        w_cash = 1.0 - w_tqqq
    strategy = f"drawdown_control_qqq_{int(round(threshold * 100))}dd_{risk_off_asset}"
    weights = _weights_frame(px.index, w_tqqq, w_qqq, w_cash, strategy)
    weights["qqq_drawdown"] = qqq_drawdown
    weights["risk_on_target"] = target
    weights["risk_on_lagged"] = risk_on_for_pnl
    return _result_from_weights(px, weights, strategy)


def default_strict_slices(df: pd.DataFrame | None = None) -> list[dict]:
    latest = None
    if df is not None and len(df) > 0:
        latest = pd.Timestamp(df.index.max()).strftime("%Y-%m-%d")
    latest = latest or pd.Timestamp.today().strftime("%Y-%m-%d")
    return [
        {
            "slice": "2019_2021",
            "train_start": "2015-01-01",
            "train_end": "2018-12-31",
            "test_start": "2019-01-01",
            "test_end": "2021-12-31",
        },
        {
            "slice": "2022_2023",
            "train_start": "2015-01-01",
            "train_end": "2021-12-31",
            "test_start": "2022-01-01",
            "test_end": "2023-12-31",
        },
        {
            "slice": "2024_latest",
            "train_start": "2015-01-01",
            "train_end": "2023-12-31",
            "test_start": "2024-01-01",
            "test_end": latest,
        },
    ]


def _load_slices_from_csv(path: str | None, df: pd.DataFrame) -> list[dict]:
    if not path:
        strict_summary = Path("reports/strict_audit/strict_audit_summary.csv")
        if strict_summary.exists():
            src = pd.read_csv(strict_summary)
            src = src.drop_duplicates("slice")
            return [
                {
                    "slice": str(r["slice"]),
                    "train_start": str(r["train_start"]),
                    "train_end": str(r["train_end"]),
                    "test_start": str(r["test_start"]),
                    "test_end": str(r.get("input_end_used", r["test_end"])),
                }
                for _, r in src.iterrows()
            ]
        return default_strict_slices(df)
    src = pd.read_csv(path)
    required = {"slice", "train_start", "train_end", "test_start", "test_end"}
    missing = required - set(src.columns)
    if missing:
        raise ValueError(f"slices csv missing columns: {sorted(missing)}")
    return [{k: str(r[k]) for k in required} for _, r in src.iterrows()]


def _strategy_specs():
    return [
        ("Static 100% TQQQ", "static", lambda d: run_static_blend_backtest(d, 1.0, 0.0, 0.0)),
        ("Static 80% TQQQ + 20% cash", "static", lambda d: run_static_blend_backtest(d, 0.8, 0.0, 0.2)),
        ("Static 70% TQQQ + 30% QQQ", "static", lambda d: run_static_blend_backtest(d, 0.7, 0.3, 0.0)),
        ("Static 50% TQQQ + 50% QQQ", "static", lambda d: run_static_blend_backtest(d, 0.5, 0.5, 0.0)),
        ("Static 30% TQQQ + 70% QQQ", "static", lambda d: run_static_blend_backtest(d, 0.3, 0.7, 0.0)),
        ("MA200 QQQ regime, risk-off cash", "ma_regime", lambda d: run_ma_regime_backtest(d, {"ma_window": 200, "risk_off_asset": "cash"})),
        ("MA200 QQQ regime, risk-off QQQ", "ma_regime", lambda d: run_ma_regime_backtest(d, {"ma_window": 200, "risk_off_asset": "qqq"})),
        ("Vol target TQQQ 35%, 20d", "vol_target", lambda d: run_vol_target_backtest(d, 0.35, 20)),
        ("Vol target TQQQ 35%, 60d", "vol_target", lambda d: run_vol_target_backtest(d, 0.35, 60)),
        (
            "QQQ drawdown control 20%, risk-off QQQ",
            "drawdown_control",
            lambda d: run_drawdown_control_backtest(
                d,
                0.20,
                {"risk_off_asset": "qqq", "recovery_fraction": 0.5, "risk_off_tqqq_weight": 0.0},
            ),
        ),
    ]


def _slice_result_to_row(result: dict, label: str, family: str, sl: dict, df_slice: pd.DataFrame) -> dict:
    test_start = pd.Timestamp(sl["test_start"])
    test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(df_slice.index.max()))
    oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
    if len(oos_index) < 2:
        raise ValueError(f"slice {sl.get('slice')} has fewer than two OOS rows.")

    equity_oos = _normalize(result["equity"].reindex(oos_index)).rename("Equity")
    tqqq_oos = _normalize(result["benchmark_equity"].reindex(oos_index)).rename("TQQQ_BuyHold")
    metrics = compute_performance_metrics(
        equity_oos,
        benchmark_equity=tqqq_oos,
        rf_ann=DEFAULT_RF_ANN,
        periods_per_year=PERIODS_PER_YEAR,
    )
    weights_oos = result["weights"].reindex(oos_index).dropna(how="all")
    avg_eff = float(weights_oos["effective_leverage"].mean())
    max_eff = float(weights_oos["effective_leverage"].max())
    row = {
        "slice": sl["slice"],
        "strategy": label,
        "family": family,
        "train_start": sl["train_start"],
        "train_end": sl["train_end"],
        "test_start": sl["test_start"],
        "test_end": test_end.strftime("%Y-%m-%d"),
        "CAGR": metrics["CAGR"],
        "MaxDD": metrics["MaxDD"],
        "Calmar": metrics["Calmar"],
        "Sharpe_DailyExcess": metrics["Sharpe_DailyExcess"],
        "CAGR_over_Vol": metrics["CAGR_over_Vol"],
        "Final_Equity": metrics["Final_Equity"],
        "AvgEffectiveLeverage": avg_eff,
        "MaxEffectiveLeverage": max_eff,
        "Benchmark_TQQQ_CAGR": metrics["Benchmark_CAGR"],
        "Benchmark_TQQQ_MaxDD": metrics["Benchmark_MaxDD"],
        "Benchmark_TQQQ_Calmar": metrics["TQQQ_Calmar"],
        "Benchmark_TQQQ_Sharpe_DailyExcess": metrics["Benchmark_Sharpe_DailyExcess"],
        "Benchmark_TQQQ_Final_Equity": metrics["Benchmark_Final_Equity"],
        "QQQ5_Used": False,
        "DL_Used": False,
        "Bandit_Used": False,
        "Kelly_Used": False,
    }
    row.update(
        {
            "strategy_cagr": row["CAGR"],
            "strategy_maxdd": row["MaxDD"],
            "strategy_calmar": row["Calmar"],
            "strategy_sharpe_daily_excess": row["Sharpe_DailyExcess"],
            "strategy_final_equity": row["Final_Equity"],
            "avg_effective_leverage": row["AvgEffectiveLeverage"],
            "max_effective_leverage": row["MaxEffectiveLeverage"],
            "tqqq_cagr": row["Benchmark_TQQQ_CAGR"],
            "tqqq_maxdd": row["Benchmark_TQQQ_MaxDD"],
            "tqqq_calmar": row["Benchmark_TQQQ_Calmar"],
            "tqqq_sharpe_daily_excess": row["Benchmark_TQQQ_Sharpe_DailyExcess"],
            "tqqq_final_equity": row["Benchmark_TQQQ_Final_Equity"],
        }
    )
    return row


def run_minimal_baseline_suite(df: pd.DataFrame, strict_slices: Iterable[dict] | None = None) -> pd.DataFrame:
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    rows = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        for label, family, fn in _strategy_specs():
            result = fn(df_slice)
            rows.append(_slice_result_to_row(result, label, family, sl, df_slice))
    out = pd.DataFrame(rows)
    if not out.empty:
        out["Rank_Calmar_In_Slice"] = out.groupby("slice")["Calmar"].rank(method="min", ascending=False)
        out["Rank_Sharpe_In_Slice"] = out.groupby("slice")["Sharpe_DailyExcess"].rank(method="min", ascending=False)
        out["Rank_CAGR_In_Slice"] = out.groupby("slice")["CAGR"].rank(method="min", ascending=False)
        out["Rank_MaxDD_In_Slice"] = out.groupby("slice")["MaxDD"].rank(method="min", ascending=False)
    return out


def _ma_variant_specs() -> list[tuple[str, dict]]:
    return [
        ("MA200 risk-off QQQ", {"ma_window": 200, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ("MA200 risk-off cash", {"ma_window": 200, "risk_off_asset": "cash", "confirmation_days": 1}),
        ("MA150 risk-off QQQ", {"ma_window": 150, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ("MA250 risk-off QQQ", {"ma_window": 250, "risk_off_asset": "qqq", "confirmation_days": 1}),
        (
            "MA200 risk-off QQQ with 5-day confirmation",
            {"ma_window": 200, "risk_off_asset": "qqq", "confirmation_days": 5},
        ),
    ]


def _average_run_length(mask: pd.Series, value: bool) -> float:
    ser = pd.Series(mask).dropna().astype(bool)
    if ser.empty:
        return float("nan")
    runs = []
    current_value = bool(ser.iloc[0])
    current_len = 1
    for item in ser.iloc[1:]:
        item = bool(item)
        if item == current_value:
            current_len += 1
        else:
            if current_value == value:
                runs.append(current_len)
            current_value = item
            current_len = 1
    if current_value == value:
        runs.append(current_len)
    return float(np.mean(runs)) if runs else 0.0


def _monthly_active_extremes(strategy_returns: pd.Series, tqqq_returns: pd.Series) -> dict:
    aligned = pd.concat(
        [strategy_returns.rename("strategy"), tqqq_returns.rename("tqqq")],
        axis=1,
    ).dropna()
    if aligned.empty:
        return {
            "worst_missed_up_month": "",
            "worst_missed_up_month_gap": float("nan"),
            "best_avoided_down_month": "",
            "best_avoided_down_month_gain": float("nan"),
        }
    period = aligned.index.to_period("M")
    monthly = (1.0 + aligned).groupby(period).prod() - 1.0
    monthly["active"] = monthly["strategy"] - monthly["tqqq"]

    up = monthly[monthly["tqqq"] > 0.0]
    if up.empty:
        missed_month = ""
        missed_gap = float("nan")
    else:
        missed_idx = up["active"].idxmin()
        missed_month = str(missed_idx)
        missed_gap = float(up.loc[missed_idx, "active"])

    down = monthly[monthly["tqqq"] < 0.0]
    if down.empty:
        avoided_month = ""
        avoided_gain = float("nan")
    else:
        avoided_idx = down["active"].idxmax()
        avoided_month = str(avoided_idx)
        avoided_gain = float(down.loc[avoided_idx, "active"])

    return {
        "worst_missed_up_month": missed_month,
        "worst_missed_up_month_gap": missed_gap,
        "best_avoided_down_month": avoided_month,
        "best_avoided_down_month_gain": avoided_gain,
    }


def _ma_behavior_stats(result: dict, sl: dict, df_slice: pd.DataFrame) -> dict:
    test_start = pd.Timestamp(sl["test_start"])
    test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(df_slice.index.max()))
    oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
    weights_oos = result["weights"].reindex(oos_index).dropna(how="all")
    if weights_oos.empty:
        return {
            "time_in_risk_on": float("nan"),
            "time_in_risk_off": float("nan"),
            "switches_per_year": float("nan"),
            "avg_days_in_risk_on": float("nan"),
            "avg_days_in_risk_off": float("nan"),
            "worst_missed_up_month": "",
            "worst_missed_up_month_gap": float("nan"),
            "best_avoided_down_month": "",
            "best_avoided_down_month_gain": float("nan"),
        }

    risk_on = weights_oos["w_tqqq"].astype(float) > 0.5
    switches = float(risk_on.astype(int).diff().abs().fillna(0.0).sum())
    years = max(len(weights_oos) / PERIODS_PER_YEAR, 1e-12)
    ret = _price_returns(df_slice).reindex(oos_index).fillna(0.0)
    strategy_ret = result["daily_returns"].reindex(oos_index).fillna(0.0)
    extremes = _monthly_active_extremes(strategy_ret, ret["TQQQ"])
    return {
        "time_in_risk_on": float(risk_on.mean()),
        "time_in_risk_off": float((~risk_on).mean()),
        "switches_per_year": switches / years,
        "avg_days_in_risk_on": _average_run_length(risk_on, True),
        "avg_days_in_risk_off": _average_run_length(risk_on, False),
        **extremes,
    }


def run_ma_regime_variants(df: pd.DataFrame, strict_slices: Iterable[dict] | None = None) -> pd.DataFrame:
    """Run the fixed, pre-declared MA regime variants on strict OOS slices."""
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    rows = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        for label, cfg in _ma_variant_specs():
            result = run_ma_regime_backtest(df_slice, cfg)
            row = _slice_result_to_row(result, label, "ma_regime_variant", sl, df_slice)
            row.update(
                {
                    "ma_window": int(cfg["ma_window"]),
                    "risk_off_asset": str(cfg["risk_off_asset"]),
                    "confirmation_days": int(cfg["confirmation_days"]),
                }
            )
            row.update(_ma_behavior_stats(result, sl, df_slice))
            rows.append(row)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["Rank_Calmar_In_Slice"] = out.groupby("slice")["Calmar"].rank(method="min", ascending=False)
        out["Rank_Sharpe_In_Slice"] = out.groupby("slice")["Sharpe_DailyExcess"].rank(method="min", ascending=False)
        out["Rank_CAGR_In_Slice"] = out.groupby("slice")["CAGR"].rank(method="min", ascending=False)
        out["Rank_MaxDD_In_Slice"] = out.groupby("slice")["MaxDD"].rank(method="min", ascending=False)
    return out


def _best_ma_variant(summary: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    ranked = summary.copy()
    ranked["CompositeRank"] = (
        ranked["Rank_Calmar_In_Slice"]
        + ranked["Rank_Sharpe_In_Slice"]
        + ranked["Rank_MaxDD_In_Slice"]
        + 0.5 * ranked["Rank_CAGR_In_Slice"]
    )
    agg = ranked.groupby("strategy", as_index=False).agg(
        MeanCompositeRank=("CompositeRank", "mean"),
        MeanCalmar=("Calmar", "mean"),
        MeanSharpe=("Sharpe_DailyExcess", "mean"),
        MeanCAGR=("CAGR", "mean"),
        WorstMaxDD=("MaxDD", "min"),
        MeanRiskOn=("time_in_risk_on", "mean"),
        MeanSwitchesPerYear=("switches_per_year", "mean"),
        Slices=("slice", "nunique"),
    )
    agg = agg.sort_values(["MeanCompositeRank", "MeanCalmar"], ascending=[True, False])
    return str(agg.iloc[0]["strategy"]), agg


def analyze_ma_regime_behavior(df: pd.DataFrame, results: pd.DataFrame) -> dict:
    """Return a compact, evidence-first interpretation of the MA variant audit."""
    if results.empty:
        return {
            "best_variant": "",
            "worth_research": False,
            "paper_trading_ready": False,
            "summary": "No MA variant results were available.",
        }
    best_variant, agg = _best_ma_variant(results)
    best = results[results["strategy"] == best_variant]
    beats_tqqq_cagr = int((best["CAGR"] >= best["Benchmark_TQQQ_CAGR"]).sum())
    lowers_dd = int((best["MaxDD"] > best["Benchmark_TQQQ_MaxDD"]).sum())
    improves_calmar = int((best["Calmar"] > best["Benchmark_TQQQ_Calmar"]).sum())
    slices = int(best["slice"].nunique())
    worth_research = improves_calmar > 0 or lowers_dd > 0
    paper_ready = beats_tqqq_cagr == slices and lowers_dd == slices and improves_calmar == slices
    return {
        "best_variant": best_variant,
        "variant_ranking": agg,
        "beats_tqqq_cagr_slices": beats_tqqq_cagr,
        "lowers_tqqq_drawdown_slices": lowers_dd,
        "improves_tqqq_calmar_slices": improves_calmar,
        "slices": slices,
        "worth_research": bool(worth_research),
        "paper_trading_ready": bool(paper_ready),
        "summary": (
            f"{best_variant} beat TQQQ CAGR in {beats_tqqq_cagr}/{slices} slices, "
            f"had lower drawdown in {lowers_dd}/{slices}, and improved Calmar in "
            f"{improves_calmar}/{slices}."
        ),
    }


def _underwater_run_lengths(drawdown: pd.Series, eps: float = 1e-12) -> list[int]:
    underwater = pd.Series(drawdown).fillna(0.0).astype(float) < -eps
    runs = []
    current = 0
    for flag in underwater:
        if bool(flag):
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _worst_rolling_return(equity: pd.Series, window: int) -> float:
    ser = pd.Series(equity).dropna().astype(float)
    if len(ser) <= int(window):
        return float("nan")
    rolling = ser / ser.shift(int(window)) - 1.0
    rolling = rolling.dropna()
    return float(rolling.min()) if not rolling.empty else float("nan")


def _best_missed_rolling_return_vs_tqqq(equity: pd.Series, benchmark_equity: pd.Series, window: int = 21) -> float:
    aligned = pd.concat(
        [pd.Series(equity).rename("strategy"), pd.Series(benchmark_equity).rename("tqqq")],
        axis=1,
    ).dropna()
    if len(aligned) <= int(window):
        return float("nan")
    strategy_ret = aligned["strategy"] / aligned["strategy"].shift(int(window)) - 1.0
    tqqq_ret = aligned["tqqq"] / aligned["tqqq"].shift(int(window)) - 1.0
    missed = (tqqq_ret - strategy_ret)[tqqq_ret > 0.0].dropna()
    if missed.empty:
        return 0.0
    return float(max(0.0, missed.max()))


def compute_underwater_pain_metrics(equity: pd.Series, benchmark_equity: pd.Series | None = None) -> dict:
    """Compute path-dependent drawdown and pain metrics from daily equity."""
    eq = _normalize(pd.Series(equity).dropna().astype(float))
    if eq.empty:
        raise ValueError("equity must contain at least one valid observation.")
    running_max = eq.cummax()
    drawdown = eq / running_max - 1.0
    runs = _underwater_run_lengths(drawdown)
    maxdd_pos = int(np.argmin(drawdown.values))
    maxdd_date = eq.index[maxdd_pos]
    maxdd = float(drawdown.iloc[maxdd_pos])
    peak_value = float(running_max.iloc[maxdd_pos])
    recovery_slice = eq.iloc[maxdd_pos:]
    recovered = recovery_slice[recovery_slice >= peak_value - 1e-12]
    if abs(maxdd) <= 1e-12:
        recovery_days = 0.0
        recovered_flag = True
        recovery_end_pos = maxdd_pos
    elif recovered.empty:
        recovery_days = float("nan")
        recovered_flag = False
        recovery_end_pos = len(eq) - 1
    else:
        recovery_end_pos = int(eq.index.get_loc(recovered.index[0]))
        recovery_days = float(recovery_end_pos - maxdd_pos)
        recovered_flag = True

    if benchmark_equity is None:
        bench = eq.copy()
    else:
        bench = _normalize(pd.Series(benchmark_equity).dropna().astype(float)).reindex(eq.index).ffill().dropna()
        aligned = pd.concat([eq.rename("strategy"), bench.rename("benchmark")], axis=1).dropna()
        eq = aligned["strategy"]
        bench = aligned["benchmark"]
        running_max = eq.cummax()
        drawdown = eq / running_max - 1.0
        runs = _underwater_run_lengths(drawdown)
        maxdd_pos = int(np.argmin(drawdown.values))
        maxdd_date = eq.index[maxdd_pos]
        maxdd = float(drawdown.iloc[maxdd_pos])
        peak_value = float(running_max.iloc[maxdd_pos])
        recovery_slice = eq.iloc[maxdd_pos:]
        recovered = recovery_slice[recovery_slice >= peak_value - 1e-12]
        if abs(maxdd) <= 1e-12:
            recovery_days = 0.0
            recovered_flag = True
            recovery_end_pos = maxdd_pos
        elif recovered.empty:
            recovery_days = float("nan")
            recovered_flag = False
            recovery_end_pos = len(eq) - 1
        else:
            recovery_end_pos = int(eq.index.get_loc(recovered.index[0]))
            recovery_days = float(recovery_end_pos - maxdd_pos)
            recovered_flag = True

    start_value = float(eq.iloc[maxdd_pos])
    end_value = float(eq.iloc[recovery_end_pos])
    bench_start = float(bench.iloc[maxdd_pos])
    bench_end = float(bench.iloc[recovery_end_pos])
    strategy_recovery_return = end_value / start_value - 1.0 if abs(start_value) > 1e-12 else float("nan")
    benchmark_recovery_return = bench_end / bench_start - 1.0 if abs(bench_start) > 1e-12 else float("nan")
    if pd.isna(benchmark_recovery_return) or abs(benchmark_recovery_return) <= 1e-12:
        recovery_participation = float("nan")
    else:
        recovery_participation = float(strategy_recovery_return / benchmark_recovery_return)

    previous_high = eq.cummax().shift(1)
    new_highs = int(((eq > previous_high.fillna(-np.inf) + 1e-12)).sum())
    pct_below_high = float((drawdown < -1e-12).mean())
    dd_magnitude = drawdown.clip(upper=0.0).abs()
    return {
        "MaxDD": maxdd,
        "MaxDDDate": str(pd.Timestamp(maxdd_date).date()),
        "LongestUnderwaterDays": int(max(runs) if runs else 0),
        "AverageUnderwaterDays": float(np.mean(runs) if runs else 0.0),
        "TimeToRecoveryAfterMaxDDDays": recovery_days,
        "MaxDDRecovered": bool(recovered_flag),
        "UlcerIndex": float(np.sqrt(np.mean(np.square(drawdown.clip(upper=0.0))))),
        "PainIndex": float(dd_magnitude.mean()),
        "Worst1MReturn": _worst_rolling_return(eq, 21),
        "Worst3MReturn": _worst_rolling_return(eq, 63),
        "Worst6MReturn": _worst_rolling_return(eq, 126),
        "BestMissed1MReturnVsTQQQ": _best_missed_rolling_return_vs_tqqq(eq, bench, 21),
        "RecoveryParticipationAfterDrawdown": recovery_participation,
        "NewEquityHighs": new_highs,
        "PctTimeBelowPreviousHigh": pct_below_high,
    }


def _underwater_strategy_specs() -> list[tuple[str, str, callable]]:
    return [
        ("TQQQ buy-and-hold", "raw_tqqq", lambda d: run_static_blend_backtest(d, 1.0, 0.0, 0.0)),
        ("70/30 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.7, 0.3, 0.0)),
        ("50/50 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.5, 0.5, 0.0)),
        (
            "MA150 risk-off QQQ",
            "ma_regime",
            lambda d: run_ma_regime_backtest(d, {"ma_window": 150, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ),
    ]


def _strict_complex_summary_rows() -> pd.DataFrame:
    path = Path("reports/strict_audit/strict_audit_summary.csv")
    if not path.exists():
        return pd.DataFrame()
    src = pd.read_csv(path)
    src = src[src.get("mode", pd.Series(dtype=str)) == "baseline"].copy()
    if src.empty:
        return src
    rows = []
    for _, r in src.iterrows():
        rows.append(
            {
                "slice": str(r["slice"]),
                "strategy": "current strict complex baseline",
                "family": "complex_baseline",
                "train_start": str(r.get("train_start", "")),
                "train_end": str(r.get("train_end", "")),
                "test_start": str(r.get("test_start", "")),
                "test_end": str(r.get("input_end_used", r.get("test_end", ""))),
                "CAGR": float(r.get("strategy_cagr", np.nan)),
                "MaxDD": float(r.get("strategy_maxdd", np.nan)),
                "Calmar": float(r.get("strategy_calmar", np.nan)),
                "Sharpe_DailyExcess": float(r.get("strategy_sharpe_daily_excess", np.nan)),
                "Final_Equity": float(r.get("strategy_final_equity", np.nan)),
                "LongestUnderwaterDays": np.nan,
                "AverageUnderwaterDays": np.nan,
                "TimeToRecoveryAfterMaxDDDays": np.nan,
                "MaxDDRecovered": "",
                "UlcerIndex": np.nan,
                "PainIndex": np.nan,
                "Worst1MReturn": np.nan,
                "Worst3MReturn": np.nan,
                "Worst6MReturn": np.nan,
                "BestMissed1MReturnVsTQQQ": np.nan,
                "RecoveryParticipationAfterDrawdown": np.nan,
                "NewEquityHighs": np.nan,
                "PctTimeBelowPreviousHigh": np.nan,
                "MaxDDDate": "",
                "data_availability": "summary_only_no_daily_equity",
            }
        )
    return pd.DataFrame(rows)


def run_underwater_pain_audit(df: pd.DataFrame, strict_slices: Iterable[dict] | None = None) -> pd.DataFrame:
    """Run path-dependent underwater/pain metrics for fixed minimal baselines."""
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    rows = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_start = pd.Timestamp(sl["test_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
        if len(oos_index) < 2:
            continue
        for label, family, fn in _underwater_strategy_specs():
            result = fn(df_slice)
            equity_oos = _normalize(result["equity"].reindex(oos_index)).rename("Equity")
            tqqq_oos = _normalize(result["benchmark_equity"].reindex(oos_index)).rename("TQQQ_BuyHold")
            perf = compute_performance_metrics(
                equity_oos,
                benchmark_equity=tqqq_oos,
                rf_ann=DEFAULT_RF_ANN,
                periods_per_year=PERIODS_PER_YEAR,
            )
            pain = compute_underwater_pain_metrics(equity_oos, tqqq_oos)
            rows.append(
                {
                    "slice": sl["slice"],
                    "strategy": label,
                    "family": family,
                    "train_start": sl["train_start"],
                    "train_end": sl["train_end"],
                    "test_start": sl["test_start"],
                    "test_end": test_end.strftime("%Y-%m-%d"),
                    "CAGR": perf["CAGR"],
                    "Calmar": perf["Calmar"],
                    "Sharpe_DailyExcess": perf["Sharpe_DailyExcess"],
                    "Final_Equity": perf["Final_Equity"],
                    "Benchmark_TQQQ_CAGR": perf["Benchmark_CAGR"],
                    "Benchmark_TQQQ_MaxDD": perf["Benchmark_MaxDD"],
                    "data_availability": "daily_equity_recomputed",
                    **pain,
                }
            )
    out = pd.DataFrame(rows)
    complex_rows = _strict_complex_summary_rows()
    if not complex_rows.empty:
        requested_slices = {str(sl["slice"]) for sl in slices}
        complex_rows = complex_rows[complex_rows["slice"].isin(requested_slices)].copy()
    if not complex_rows.empty:
        out = pd.concat([out, complex_rows], ignore_index=True, sort=False)
    return out


def _slug(text: str) -> str:
    chars = []
    for ch in str(text).lower():
        if ch.isalnum():
            chars.append(ch)
        else:
            chars.append("_")
    slug = "_".join(part for part in "".join(chars).split("_") if part)
    return slug or "item"


def _daily_artifact_specs() -> list[tuple[str, str, callable]]:
    return [
        ("TQQQ buy-and-hold", "raw_tqqq", lambda d: run_static_blend_backtest(d, 1.0, 0.0, 0.0)),
        ("70/30 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.7, 0.3, 0.0)),
        ("50/50 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.5, 0.5, 0.0)),
        (
            "MA150 risk-off QQQ",
            "ma_regime",
            lambda d: run_ma_regime_backtest(d, {"ma_window": 150, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ),
    ]


def _daily_artifact_frame(result: dict, df_slice: pd.DataFrame, sl: dict, label: str, family: str, run_id: str) -> pd.DataFrame:
    test_start = pd.Timestamp(sl["test_start"])
    test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(df_slice.index.max()))
    oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
    weights = result["weights"].reindex(oos_index).copy()
    equity = _normalize(result["equity"].reindex(oos_index)).rename("equity")
    benchmark = _normalize(result["benchmark_equity"].reindex(oos_index)).rename("tqqq_buyhold_equity")
    ret = _price_returns(df_slice).reindex(oos_index).fillna(0.0)
    daily = result["daily_returns"].reindex(oos_index).fillna(0.0).rename("strategy_daily_return")
    out = pd.DataFrame(index=oos_index)
    out.index.name = "Date"
    out["run_id"] = str(run_id)
    out["slice"] = str(sl["slice"])
    out["strategy"] = str(label)
    out["family"] = str(family)
    out["train_start"] = str(sl["train_start"])
    out["train_end"] = str(sl["train_end"])
    out["test_start"] = str(sl["test_start"])
    out["test_end"] = test_end.strftime("%Y-%m-%d")
    out["QQQ"] = df_slice["QQQ"].reindex(oos_index)
    out["TQQQ"] = df_slice["TQQQ"].reindex(oos_index)
    out["qqq_return"] = ret["QQQ"]
    out["tqqq_return"] = ret["TQQQ"]
    out["strategy_daily_return"] = daily
    out["equity"] = equity
    out["tqqq_buyhold_equity"] = benchmark
    out["drawdown"] = equity / equity.cummax() - 1.0
    out["tqqq_buyhold_drawdown"] = benchmark / benchmark.cummax() - 1.0
    for col in ["w_tqqq", "w_qqq", "w_cash", "effective_leverage"]:
        out[col] = weights[col] if col in weights else np.nan
    out["signal_state"] = np.where(out["w_tqqq"].fillna(0.0) > 0.5, "risk_on", "risk_off")
    out["raw_signal"] = weights["raw_signal"] if "raw_signal" in weights else np.nan
    out["signal_lagged"] = weights["signal_lagged"] if "signal_lagged" in weights else np.nan
    out["qqq_drawdown"] = df_slice["QQQ"].reindex(oos_index) / df_slice["QQQ"].reindex(oos_index).cummax() - 1.0
    return out


def save_daily_artifacts(
    df: pd.DataFrame,
    strict_slices: Iterable[dict] | None = None,
    artifact_dir: str | Path = "reports/minimal_baseline/raw",
    run_id: str = "minimal_v01",
) -> list[Path]:
    """Save explicit daily OOS artifacts for fixed minimal comparison strategies."""
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    out_dir = Path(artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        for label, family, fn in _daily_artifact_specs():
            result = fn(df_slice)
            artifact = _daily_artifact_frame(result, df_slice, sl, label, family, str(run_id))
            if artifact.empty:
                continue
            filename = f"{_slug(run_id)}_{_slug(sl['slice'])}_{_slug(label)}_daily_artifacts.csv"
            path = out_dir / filename
            artifact.to_csv(path)
            paths.append(path)
    return paths


def _cost_strategy_specs() -> list[tuple[str, str, callable]]:
    return [
        ("TQQQ buy-and-hold", "raw_tqqq", lambda d: run_static_blend_backtest(d, 1.0, 0.0, 0.0)),
        ("70/30 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.7, 0.3, 0.0)),
        ("50/50 TQQQ/QQQ", "static_blend", lambda d: run_static_blend_backtest(d, 0.5, 0.5, 0.0)),
        (
            "MA150 risk-off QQQ",
            "ma_regime",
            lambda d: run_ma_regime_backtest(d, {"ma_window": 150, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ),
        (
            "MA200 risk-off QQQ",
            "ma_regime",
            lambda d: run_ma_regime_backtest(d, {"ma_window": 200, "risk_off_asset": "qqq", "confirmation_days": 1}),
        ),
        (
            "MA150 risk-off cash",
            "ma_regime",
            lambda d: run_ma_regime_backtest(d, {"ma_window": 150, "risk_off_asset": "cash", "confirmation_days": 1}),
        ),
    ]


def _effective_weights_for_cost(weights: pd.DataFrame, execution_delay_days: int = 0) -> pd.DataFrame:
    cols = ["w_tqqq", "w_qqq", "w_cash"]
    missing = set(cols) - set(weights.columns)
    if missing:
        raise ValueError(f"weights missing columns: {sorted(missing)}")
    delay = int(execution_delay_days)
    if delay < 0:
        raise ValueError("execution_delay_days must be >= 0.")
    out = weights[cols].copy().astype(float)
    if delay > 0:
        out = out.shift(delay)
        out["w_tqqq"] = out["w_tqqq"].fillna(0.0)
        out["w_qqq"] = out["w_qqq"].fillna(0.0)
        out["w_cash"] = out["w_cash"].fillna(1.0)
    out = out.fillna({"w_tqqq": 0.0, "w_qqq": 0.0, "w_cash": 1.0})
    out["effective_leverage"] = 3.0 * out["w_tqqq"] + out["w_qqq"]
    return out


def calculate_turnover_stats(weights: pd.DataFrame, cost_bps: float = 0.0) -> dict:
    """Calculate one-way turnover from risky asset weight changes."""
    eff = _effective_weights_for_cost(weights, 0)
    risky = eff[["w_tqqq", "w_qqq"]]
    prev = risky.shift(1).fillna(0.0)
    turnover = (risky - prev).abs().sum(axis=1)
    years = max(len(eff) / PERIODS_PER_YEAR, 1e-12)
    cost_rate = turnover * (float(cost_bps) / 10000.0)
    return {
        "turnover_series": turnover,
        "cost_rate_series": cost_rate,
        "total_turnover": float(turnover.sum()),
        "annualized_turnover": float(turnover.sum() / years),
        "trade_days": int((turnover > 1e-12).sum()),
        "avg_turnover_on_trade_days": float(turnover[turnover > 1e-12].mean()) if (turnover > 1e-12).any() else 0.0,
        "total_cost_rate": float(cost_rate.sum()),
    }


def apply_trade_cost_to_equity(
    df: pd.DataFrame,
    weights: pd.DataFrame,
    cost_bps: float = 0.0,
    execution_delay_days: int = 0,
    oos_index: Iterable | None = None,
) -> dict:
    """Apply one-way transaction costs to daily equity from target weights.

    Costs are charged only on changes in risky asset weights. The first OOS row
    includes the cost of establishing the current risky allocation from cash.
    """
    px = _require_price_frame(df)
    ret = _price_returns(px)
    eff_weights = _effective_weights_for_cost(weights.reindex(px.index), int(execution_delay_days))
    if oos_index is None:
        idx = px.index
    else:
        idx = pd.Index(pd.to_datetime(list(oos_index)))
    eff_weights = eff_weights.reindex(idx).fillna({"w_tqqq": 0.0, "w_qqq": 0.0, "w_cash": 1.0, "effective_leverage": 0.0})
    ret = ret.reindex(idx).fillna(0.0)

    gross_ret = eff_weights["w_tqqq"] * ret["TQQQ"] + eff_weights["w_qqq"] * ret["QQQ"]
    if len(gross_ret) > 0:
        gross_ret.iloc[0] = 0.0

    risky = eff_weights[["w_tqqq", "w_qqq"]]
    prev = risky.shift(1).fillna(0.0)
    turnover = (risky - prev).abs().sum(axis=1)
    cost_rate = turnover * (float(cost_bps) / 10000.0)
    net_ret = (1.0 + gross_ret) * (1.0 - cost_rate) - 1.0
    net_equity = _equity_from_returns(net_ret)
    gross_equity = _equity_from_returns(gross_ret)
    return {
        "equity": net_equity,
        "gross_equity": gross_equity,
        "daily_returns": net_ret.rename("DailyReturnAfterCost"),
        "gross_daily_returns": gross_ret.rename("GrossDailyReturn"),
        "turnover": turnover.rename("Turnover"),
        "cost_rate": cost_rate.rename("TradeCostRate"),
        "weights": eff_weights,
        "final_cost_drag": float(gross_equity.iloc[-1] - net_equity.iloc[-1]) if len(net_equity) else 0.0,
        "total_cost_rate": float(cost_rate.sum()),
    }


def _parse_cost_bps_list(value: str | Iterable[float] | None) -> list[float]:
    if value is None:
        return [0.0, 5.0, 10.0, 25.0, 50.0]
    if isinstance(value, str):
        return [float(x.strip()) for x in value.split(",") if x.strip()]
    return [float(x) for x in value]


def _cost_scenarios(cost_bps_list: str | Iterable[float] | None = None, execution_delay_days: int = 0) -> list[dict]:
    scenarios = []
    seen = set()
    for bps in _parse_cost_bps_list(cost_bps_list):
        key = (float(bps), int(execution_delay_days))
        if key in seen:
            continue
        seen.add(key)
        if float(bps) == 0.0 and int(execution_delay_days) == 0:
            name = "base_0bps"
        else:
            name = f"cost_{int(float(bps)) if float(bps).is_integer() else bps}bps_delay{int(execution_delay_days)}d"
        scenarios.append({"scenario": name, "cost_bps": float(bps), "execution_delay_days": int(execution_delay_days)})
    if int(execution_delay_days) == 0:
        for bps in [10.0, 25.0]:
            key = (bps, 1)
            if key not in seen:
                seen.add(key)
                scenarios.append({"scenario": f"stress_{int(bps)}bps_delay1d", "cost_bps": bps, "execution_delay_days": 1})
    return scenarios


def run_cost_stress_suite(
    df: pd.DataFrame,
    strict_slices: Iterable[dict] | None = None,
    cost_bps_list: str | Iterable[float] | None = None,
    execution_delay_days: int = 0,
) -> pd.DataFrame:
    """Run fixed cost and delay stress scenarios for minimal baselines."""
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    scenarios = _cost_scenarios(cost_bps_list, execution_delay_days)
    rows = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_start = pd.Timestamp(sl["test_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
        if len(oos_index) < 2:
            continue
        benchmark = _equity_from_returns(_price_returns(df_slice).reindex(oos_index).fillna(0.0)["TQQQ"])
        if len(benchmark) > 0:
            benchmark = _normalize(benchmark)

        for label, family, fn in _cost_strategy_specs():
            result = fn(df_slice)
            for scenario in scenarios:
                cost_result = apply_trade_cost_to_equity(
                    df_slice,
                    result["weights"],
                    cost_bps=scenario["cost_bps"],
                    execution_delay_days=scenario["execution_delay_days"],
                    oos_index=oos_index,
                )
                equity = cost_result["equity"]
                metrics = compute_performance_metrics(
                    equity,
                    benchmark_equity=benchmark,
                    rf_ann=DEFAULT_RF_ANN,
                    periods_per_year=PERIODS_PER_YEAR,
                )
                turnover_stats = calculate_turnover_stats(cost_result["weights"], scenario["cost_bps"])
                rows.append(
                    {
                        "slice": sl["slice"],
                        "strategy": label,
                        "family": family,
                        "scenario": scenario["scenario"],
                        "cost_bps": scenario["cost_bps"],
                        "execution_delay_days": scenario["execution_delay_days"],
                        "train_start": sl["train_start"],
                        "train_end": sl["train_end"],
                        "test_start": sl["test_start"],
                        "test_end": test_end.strftime("%Y-%m-%d"),
                        "CAGR": metrics["CAGR"],
                        "MaxDD": metrics["MaxDD"],
                        "Calmar": metrics["Calmar"],
                        "Sharpe_DailyExcess": metrics["Sharpe_DailyExcess"],
                        "Final_Equity": metrics["Final_Equity"],
                        "Benchmark_TQQQ_CAGR": metrics["Benchmark_CAGR"],
                        "Benchmark_TQQQ_MaxDD": metrics["Benchmark_MaxDD"],
                        "total_turnover": turnover_stats["total_turnover"],
                        "annualized_turnover": turnover_stats["annualized_turnover"],
                        "trade_days": turnover_stats["trade_days"],
                        "avg_turnover_on_trade_days": turnover_stats["avg_turnover_on_trade_days"],
                        "total_cost_rate": turnover_stats["total_cost_rate"],
                        "final_cost_drag": cost_result["final_cost_drag"],
                        "MaxEffectiveLeverage": float(cost_result["weights"]["effective_leverage"].max()),
                        "AvgEffectiveLeverage": float(cost_result["weights"]["effective_leverage"].mean()),
                        "PeriodicRebalance": False,
                    }
                )
    return pd.DataFrame(rows)


def _read_price_csv(path: str, name: str) -> pd.Series:
    src = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
    col = "Adj Close" if "Adj Close" in src.columns else "Close"
    if col not in src.columns:
        col = src.columns[0]
    ser = pd.to_numeric(src[col], errors="coerce").dropna()
    ser.name = name
    return ser


def load_price_data(start: str, end: str | None, qqq_csv: str | None = None, tqqq_csv: str | None = None) -> pd.DataFrame:
    if qqq_csv and tqqq_csv:
        qqq = _read_price_csv(qqq_csv, "QQQ")
        tqqq = _read_price_csv(tqqq_csv, "TQQQ")
    elif qqq_csv or tqqq_csv:
        raise ValueError("provide both --qqq_csv and --tqqq_csv, or neither.")
    else:
        qqq = get_close_series_from_yf("QQQ", start=start, end=end, auto_adjust=True)
        tqqq = get_close_series_from_yf("TQQQ", start=start, end=end, auto_adjust=True)
    df = pd.concat([qqq, tqqq], axis=1).dropna()
    if start:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end and str(end).lower() != "auto":
        df = df.loc[df.index <= pd.Timestamp(end)]
    return _require_price_frame(df)


def _fmt_pct(x):
    return "n/a" if pd.isna(x) else f"{float(x):.2%}"


def _fmt_num(x):
    return "n/a" if pd.isna(x) else f"{float(x):.3f}"


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def _best_strategy(summary: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    ranked = summary.copy()
    ranked["CompositeRank"] = (
        ranked["Rank_Calmar_In_Slice"]
        + ranked["Rank_Sharpe_In_Slice"]
        + ranked["Rank_MaxDD_In_Slice"]
        + 0.5 * ranked["Rank_CAGR_In_Slice"]
    )
    agg = ranked.groupby("strategy", as_index=False).agg(
        MeanCompositeRank=("CompositeRank", "mean"),
        MeanCalmar=("Calmar", "mean"),
        MeanSharpe=("Sharpe_DailyExcess", "mean"),
        MeanCAGR=("CAGR", "mean"),
        WorstMaxDD=("MaxDD", "min"),
        Slices=("slice", "nunique"),
    )
    agg = agg.sort_values(["MeanCompositeRank", "MeanCalmar"], ascending=[True, False])
    return str(agg.iloc[0]["strategy"]), agg


def build_minimal_baseline_report(summary: pd.DataFrame, out_prefix: str) -> str:
    best_name, agg = _best_strategy(summary)
    best = summary[summary["strategy"] == best_name].copy()

    strict_path = Path("reports/strict_audit/strict_audit_summary.csv")
    strict = pd.read_csv(strict_path) if strict_path.exists() else pd.DataFrame()
    baseline = strict[strict.get("mode", pd.Series(dtype=str)) == "baseline"].copy() if not strict.empty else pd.DataFrame()
    dl = strict[strict.get("mode", pd.Series(dtype=str)) == "dl"].copy() if not strict.empty else pd.DataFrame()

    comparison_text = "No current complex strategy summary was available for comparison."
    exact_required_text = "Partial evidence only; further research required."
    decision = "HOLD"
    if not baseline.empty:
        baseline_cmp = baseline[
            ["slice", "strategy_cagr", "strategy_maxdd", "strategy_calmar", "strategy_sharpe_daily_excess"]
        ].rename(
            columns={
                "strategy_cagr": "complex_cagr",
                "strategy_maxdd": "complex_maxdd",
                "strategy_calmar": "complex_calmar",
                "strategy_sharpe_daily_excess": "complex_sharpe_daily_excess",
            }
        )
        merged = best.merge(
            baseline_cmp,
            on="slice",
            how="inner",
        )
        cagr_wins = int((merged["CAGR"] > merged["complex_cagr"]).sum())
        calmar_wins = int((merged["Calmar"] > merged["complex_calmar"]).sum())
        sharpe_wins = int((merged["Sharpe_DailyExcess"] > merged["complex_sharpe_daily_excess"]).sum())
        dd_wins = int((merged["MaxDD"] > merged["complex_maxdd"]).sum())
        denom = max(1, int(len(merged)))
        if cagr_wins >= 2 and calmar_wins >= 2 and sharpe_wins >= 2 and dd_wins >= 2:
            exact_required_text = "Simple baseline outperforms current complex strategy; complex strategy remains unjustified."
            decision = "GO FOR FURTHER RESEARCH"
        elif cagr_wins >= 2 and (calmar_wins >= 2 or sharpe_wins >= 2):
            exact_required_text = "Partial evidence only; further research required."
            decision = "GO FOR FURTHER RESEARCH"
        elif cagr_wins > 0 or calmar_wins > 0 or sharpe_wins > 0 or dd_wins > 0:
            exact_required_text = "Partial evidence only; further research required."
            decision = "HOLD"
        else:
            exact_required_text = "No simple risk-management baseline passed strict OOS."
            decision = "HOLD"
        comparison_text = (
            f"Best minimal strategy `{best_name}` beat current baseline strict strategy in "
            f"{cagr_wins}/{denom} CAGR slices, {calmar_wins}/{denom} Calmar slices, "
            f"{sharpe_wins}/{denom} Sharpe slices, and {dd_wins}/{denom} MaxDD slices."
        )

    show = summary[
        [
            "slice",
            "strategy",
            "family",
            "CAGR",
            "MaxDD",
            "Calmar",
            "Sharpe_DailyExcess",
            "Final_Equity",
            "AvgEffectiveLeverage",
            "MaxEffectiveLeverage",
            "Rank_Calmar_In_Slice",
            "Rank_Sharpe_In_Slice",
            "Rank_CAGR_In_Slice",
        ]
    ].copy()
    for col in ["CAGR", "MaxDD"]:
        show[col] = show[col].map(_fmt_pct)
    for col in ["Calmar", "Sharpe_DailyExcess", "Final_Equity", "AvgEffectiveLeverage", "MaxEffectiveLeverage"]:
        show[col] = show[col].map(_fmt_num)
    for col in ["Rank_Calmar_In_Slice", "Rank_Sharpe_In_Slice", "Rank_CAGR_In_Slice"]:
        show[col] = show[col].map(lambda x: "" if pd.isna(x) else str(int(float(x))))

    best_show = best[["slice", "CAGR", "MaxDD", "Calmar", "Sharpe_DailyExcess", "AvgEffectiveLeverage", "MaxEffectiveLeverage"]].copy()
    for col in ["CAGR", "MaxDD"]:
        best_show[col] = best_show[col].map(_fmt_pct)
    for col in ["Calmar", "Sharpe_DailyExcess", "AvgEffectiveLeverage", "MaxEffectiveLeverage"]:
        best_show[col] = best_show[col].map(_fmt_num)

    lines = [
        "# Minimal TQQQ Risk-Management Baseline Report",
        "",
        "## Executive Verdict",
        "",
        exact_required_text,
        "",
        f"Decision: {decision}",
        "",
        "This is not a TQQQ killer result and must not be presented as paper-trading approval.",
        "The current complex strategy remains NO-GO for paper trading.",
        "",
        "## Scope",
        "",
        "- Uses only QQQ, TQQQ, and cash.",
        "- Uses raw adjusted price returns with no extra TQQQ expense deduction.",
        "- Does not use DL, Bandit, Kelly, QQQ5, synthetic products, options, or futures.",
        "- All MA, volatility, and drawdown signals are shifted one day before affecting returns.",
        "- Effective leverage is capped by construction at <= 3x.",
        "- Strict OOS slices match the prior audit where the reference files are available.",
        "",
        "## Best Simple Strategy",
        "",
        f"Best composite strategy: `{best_name}`.",
        "",
        _markdown_table(best_show),
        "",
        "The best strategy is selected by a composite rank across Calmar, Sharpe_DailyExcess, MaxDD, and CAGR. It is not selected by CAGR alone.",
        "",
        "## Comparison Against Current Complex Strategy",
        "",
        comparison_text,
        "",
        "The DL strict strategy remains unsupported: prior strict audit rows showed DL below baseline in all retained CAGR slices, with an initial fallback limitation.",
        "Synthetic or hybrid QQQ5 is not used and is not part of this conclusion.",
        "",
        "## Full Minimal Suite",
        "",
        _markdown_table(show),
        "",
        "## Decision",
        "",
        f"{decision}",
        "",
        "GO FOR FURTHER RESEARCH means the simple baseline is worth studying under stricter daily artifact capture. It does not mean paper trading approval.",
        "HOLD means keep the result as a benchmark but do not allocate further implementation effort until a clearer hurdle is met.",
        "KILL would mean no simple baseline showed useful strict OOS evidence.",
        "",
        "## Next Research Controls",
        "",
        "1. Freeze this minimal suite as the benchmark hurdle for future complex modules.",
        "2. Add daily OOS equity, weights, notes, and trades for attribution before changing any rules.",
        "3. Compare any future simplified candidate against this suite and the current complex strategy before considering paper trading.",
        "",
        f"CSV output: `{out_prefix}_summary.csv`",
    ]
    return "\n".join(lines) + "\n"


def _comparison_wins(left: pd.DataFrame, right: pd.DataFrame, right_label: str) -> str:
    if left.empty or right.empty:
        return f"No comparable rows were available for {right_label}."
    cols = ["slice", "CAGR", "MaxDD", "Calmar", "Sharpe_DailyExcess"]
    right_cols = [c for c in cols if c in right.columns]
    if "slice" not in right_cols:
        return f"No comparable rows were available for {right_label}."
    merged = left[cols].merge(
        right[right_cols].rename(
            columns={
                "CAGR": "other_cagr",
                "MaxDD": "other_maxdd",
                "Calmar": "other_calmar",
                "Sharpe_DailyExcess": "other_sharpe",
            }
        ),
        on="slice",
        how="inner",
    )
    if merged.empty:
        return f"No comparable rows were available for {right_label}."
    cagr = int((merged["CAGR"] > merged["other_cagr"]).sum())
    maxdd = int((merged["MaxDD"] > merged["other_maxdd"]).sum())
    calmar = int((merged["Calmar"] > merged["other_calmar"]).sum())
    sharpe = int((merged["Sharpe_DailyExcess"] > merged["other_sharpe"]).sum())
    denom = int(len(merged))
    return (
        f"Versus {right_label}: CAGR wins {cagr}/{denom}, MaxDD wins {maxdd}/{denom}, "
        f"Calmar wins {calmar}/{denom}, Sharpe wins {sharpe}/{denom}."
    )


def _strict_baseline_as_metrics() -> pd.DataFrame:
    path = Path("reports/strict_audit/strict_audit_summary.csv")
    if not path.exists():
        return pd.DataFrame()
    src = pd.read_csv(path)
    src = src[src.get("mode", pd.Series(dtype=str)) == "baseline"].copy()
    if src.empty:
        return src
    return src.rename(
        columns={
            "strategy_cagr": "CAGR",
            "strategy_maxdd": "MaxDD",
            "strategy_calmar": "Calmar",
            "strategy_sharpe_daily_excess": "Sharpe_DailyExcess",
        }
    )


def build_ma_regime_behavior_report(results: pd.DataFrame, out_dir: Path) -> str:
    analysis = analyze_ma_regime_behavior(pd.DataFrame(), results)
    best_name = analysis["best_variant"]
    best = results[results["strategy"] == best_name].copy() if best_name else pd.DataFrame()

    minimal_path = out_dir / "minimal_tqqq_summary.csv"
    minimal = pd.read_csv(minimal_path) if minimal_path.exists() else pd.DataFrame()
    static_7030 = minimal[minimal.get("strategy", pd.Series(dtype=str)) == "Static 70% TQQQ + 30% QQQ"].copy() if not minimal.empty else pd.DataFrame()
    static_5050 = minimal[minimal.get("strategy", pd.Series(dtype=str)) == "Static 50% TQQQ + 50% QQQ"].copy() if not minimal.empty else pd.DataFrame()
    current_best = minimal[minimal.get("strategy", pd.Series(dtype=str)) == "MA200 QQQ regime, risk-off QQQ"].copy() if not minimal.empty else pd.DataFrame()
    complex_baseline = _strict_baseline_as_metrics()

    ranking = analysis.get("variant_ranking", pd.DataFrame())
    ranking_show = ranking[
        [
            "strategy",
            "MeanCompositeRank",
            "MeanCalmar",
            "MeanSharpe",
            "MeanCAGR",
            "WorstMaxDD",
            "MeanRiskOn",
            "MeanSwitchesPerYear",
        ]
    ].copy() if not ranking.empty else pd.DataFrame()
    for col in ["MeanCAGR", "WorstMaxDD", "MeanRiskOn"]:
        if col in ranking_show:
            ranking_show[col] = ranking_show[col].map(_fmt_pct)
    for col in ["MeanCompositeRank", "MeanCalmar", "MeanSharpe", "MeanSwitchesPerYear"]:
        if col in ranking_show:
            ranking_show[col] = ranking_show[col].map(_fmt_num)

    show_cols = [
        "slice",
        "strategy",
        "CAGR",
        "Benchmark_TQQQ_CAGR",
        "MaxDD",
        "Benchmark_TQQQ_MaxDD",
        "Calmar",
        "Sharpe_DailyExcess",
        "Final_Equity",
        "time_in_risk_on",
        "switches_per_year",
        "avg_days_in_risk_on",
        "avg_days_in_risk_off",
        "worst_missed_up_month",
        "worst_missed_up_month_gap",
        "best_avoided_down_month",
        "best_avoided_down_month_gain",
    ]
    show = results[show_cols].copy()
    for col in ["CAGR", "Benchmark_TQQQ_CAGR", "MaxDD", "Benchmark_TQQQ_MaxDD", "time_in_risk_on", "worst_missed_up_month_gap", "best_avoided_down_month_gain"]:
        show[col] = show[col].map(_fmt_pct)
    for col in ["Calmar", "Sharpe_DailyExcess", "Final_Equity", "switches_per_year", "avg_days_in_risk_on", "avg_days_in_risk_off"]:
        show[col] = show[col].map(_fmt_num)

    best_show = best[
        [
            "slice",
            "CAGR",
            "MaxDD",
            "Calmar",
            "Sharpe_DailyExcess",
            "time_in_risk_on",
            "switches_per_year",
            "worst_missed_up_month",
            "best_avoided_down_month",
        ]
    ].copy() if not best.empty else pd.DataFrame()
    if not best_show.empty:
        for col in ["CAGR", "MaxDD", "time_in_risk_on"]:
            best_show[col] = best_show[col].map(_fmt_pct)
        for col in ["Calmar", "Sharpe_DailyExcess", "switches_per_year"]:
            best_show[col] = best_show[col].map(_fmt_num)

    qqq_vs_cash = "Risk-off QQQ generally preserves rebound participation better than cash, while cash is more defensive during deep drawdowns."
    if not results.empty:
        qqq_rows = results[results["strategy"] == "MA200 risk-off QQQ"]
        cash_rows = results[results["strategy"] == "MA200 risk-off cash"]
        if not qqq_rows.empty and not cash_rows.empty:
            qqq_mean = float(qqq_rows["CAGR"].mean())
            cash_mean = float(cash_rows["CAGR"].mean())
            qqq_dd = float(qqq_rows["MaxDD"].mean())
            cash_dd = float(cash_rows["MaxDD"].mean())
            qqq_vs_cash = (
                f"Risk-off QQQ had mean CAGR {_fmt_pct(qqq_mean)} and mean MaxDD {_fmt_pct(qqq_dd)}; "
                f"risk-off cash had mean CAGR {_fmt_pct(cash_mean)} and mean MaxDD {_fmt_pct(cash_dd)}. "
                "QQQ risk-off keeps equity beta in recoveries; cash risk-off is cleaner defense but can miss rebounds."
            )

    confirm_text = "The 5-day confirmation is evaluated from consecutive close-only observations and then shifted one day before PnL."
    confirm_rows = results[results["strategy"] == "MA200 risk-off QQQ with 5-day confirmation"]
    base_rows = results[results["strategy"] == "MA200 risk-off QQQ"]
    if not confirm_rows.empty and not base_rows.empty:
        confirm_switch = float(confirm_rows["switches_per_year"].mean())
        base_switch = float(base_rows["switches_per_year"].mean())
        confirm_cagr = float(confirm_rows["CAGR"].mean())
        base_cagr = float(base_rows["CAGR"].mean())
        confirm_text = (
            f"5-day confirmation changed average switches/year from {_fmt_num(base_switch)} to {_fmt_num(confirm_switch)} "
            f"and mean CAGR from {_fmt_pct(base_cagr)} to {_fmt_pct(confirm_cagr)}. It can reduce whipsaw only if this drop in switch frequency offsets slower re-entry."
        )

    conclusion = "MA regime is worth continued research as simple trend-following exposure control, but it is not paper-trading ready."
    if analysis["paper_trading_ready"]:
        conclusion = "MA regime has unusually strong strict-slice evidence, but this report still does not approve paper trading."
    elif not analysis["worth_research"]:
        conclusion = "MA regime did not show enough strict-slice evidence to prioritize further research."

    lines = [
        "# MA Regime Behavior Audit",
        "",
        "## Executive Verdict",
        "",
        conclusion,
        "",
        f"Best fixed variant: `{best_name}`.",
        "",
        "Paper-trading status: NOT READY.",
        "This is not a TQQQ killer result. It is a simple trend-following exposure-control candidate.",
        "",
        "## Best Variant By Slice",
        "",
        _markdown_table(best_show),
        "",
        "## Variant Ranking",
        "",
        _markdown_table(ranking_show),
        "",
        "## Behavior Findings",
        "",
        f"- {analysis['summary']}",
        f"- {qqq_vs_cash}",
        f"- {confirm_text}",
        "- 2022 defense should be judged by MaxDD, Calmar, and down-month avoidance, not by CAGR alone.",
        "- 2024-latest recovery behavior should be judged by missed positive months and risk-on time; slow re-entry remains a key risk.",
        "",
        "## Comparison Hurdles",
        "",
        _comparison_wins(best, static_7030, "70/30 TQQQ/QQQ"),
        "",
        _comparison_wins(best, static_5050, "50/50 TQQQ/QQQ"),
        "",
        _comparison_wins(best, complex_baseline, "current strict complex baseline"),
        "",
        _comparison_wins(best, current_best, "current best minimal baseline"),
        "",
        "## Full Variant Summary",
        "",
        _markdown_table(show),
        "",
        "## Decision",
        "",
        "MA regime is worth continued research only as a minimal, explainable risk-management baseline. It does not have paper-trading qualification yet.",
        "",
        "## Next Research Questions",
        "",
        "1. Does MA regime still beat simple blends after adding retained daily OOS equity and weight artifacts for each slice?",
        "2. Is the 2024-latest re-entry lag acceptable versus a fixed 70/30 or 50/50 blend?",
        "3. Does the same MA regime survive a volatility-matched benchmark and tax/slippage stress?",
    ]
    return "\n".join(lines) + "\n"


def write_ma_behavior_outputs(results: pd.DataFrame, out_prefix: str) -> tuple[Path, Path]:
    out_dir = Path(out_prefix).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ma_regime_variant_summary.csv"
    report_path = out_dir / "MA_REGIME_BEHAVIOR_AUDIT.md"
    results.to_csv(csv_path, index=False)
    report_path.write_text(build_ma_regime_behavior_report(results, out_dir), encoding="utf-8")
    return csv_path, report_path


def _pain_comparison(left: pd.DataFrame, right: pd.DataFrame, right_label: str) -> str:
    if left.empty or right.empty:
        return f"No comparable daily pain rows were available for {right_label}."
    merged = left.merge(right, on="slice", how="inner", suffixes=("", "_other"))
    if merged.empty:
        return f"No comparable daily pain rows were available for {right_label}."
    denom = int(len(merged))
    cagr = int((merged["CAGR"] > merged["CAGR_other"]).sum()) if "CAGR_other" in merged else 0
    maxdd = int((merged["MaxDD"] > merged["MaxDD_other"]).sum()) if "MaxDD_other" in merged else 0
    ulcer = int((merged["UlcerIndex"] < merged["UlcerIndex_other"]).sum()) if "UlcerIndex_other" in merged else 0
    pain = int((merged["PainIndex"] < merged["PainIndex_other"]).sum()) if "PainIndex_other" in merged else 0
    underwater = (
        int((merged["PctTimeBelowPreviousHigh"] < merged["PctTimeBelowPreviousHigh_other"]).sum())
        if "PctTimeBelowPreviousHigh_other" in merged
        else 0
    )
    return (
        f"Versus {right_label}: CAGR wins {cagr}/{denom}, MaxDD wins {maxdd}/{denom}, "
        f"UlcerIndex wins {ulcer}/{denom}, PainIndex wins {pain}/{denom}, "
        f"less time underwater wins {underwater}/{denom}."
    )


def build_underwater_pain_report(results: pd.DataFrame) -> str:
    ma = results[results["strategy"] == "MA150 risk-off QQQ"].copy()
    tqqq = results[results["strategy"] == "TQQQ buy-and-hold"].copy()
    blend70 = results[results["strategy"] == "70/30 TQQQ/QQQ"].copy()
    blend50 = results[results["strategy"] == "50/50 TQQQ/QQQ"].copy()
    complex_rows = results[results["strategy"] == "current strict complex baseline"].copy()

    ma_wins_vs_tqqq = _pain_comparison(ma, tqqq, "TQQQ buy-and-hold")
    ma_wins_vs_70 = _pain_comparison(ma, blend70, "70/30 TQQQ/QQQ")
    ma_wins_vs_50 = _pain_comparison(ma, blend50, "50/50 TQQQ/QQQ")

    complex_text = "Current strict complex baseline daily equity was unavailable; only CAGR/MaxDD/Calmar/Sharpe summary comparison is possible."
    if not ma.empty and not complex_rows.empty:
        merged = ma.merge(complex_rows, on="slice", how="inner", suffixes=("", "_complex"))
        if not merged.empty:
            cagr = int((merged["CAGR"] > merged["CAGR_complex"]).sum())
            maxdd = int((merged["MaxDD"] > merged["MaxDD_complex"]).sum())
            calmar = int((merged["Calmar"] > merged["Calmar_complex"]).sum())
            denom = int(len(merged))
            complex_text = (
                "Current strict complex baseline daily equity was unavailable, so underwater duration, UlcerIndex, "
                "PainIndex, rolling-loss, and recovery metrics are not computed for it. Summary-only comparison: "
                f"MA150 wins CAGR {cagr}/{denom}, MaxDD {maxdd}/{denom}, Calmar {calmar}/{denom}."
            )

    table_cols = [
        "slice",
        "strategy",
        "CAGR",
        "MaxDD",
        "Calmar",
        "UlcerIndex",
        "PainIndex",
        "LongestUnderwaterDays",
        "AverageUnderwaterDays",
        "TimeToRecoveryAfterMaxDDDays",
        "Worst1MReturn",
        "Worst3MReturn",
        "Worst6MReturn",
        "BestMissed1MReturnVsTQQQ",
        "RecoveryParticipationAfterDrawdown",
        "NewEquityHighs",
        "PctTimeBelowPreviousHigh",
        "data_availability",
    ]
    show = results[[c for c in table_cols if c in results.columns]].copy()
    for col in [
        "CAGR",
        "MaxDD",
        "UlcerIndex",
        "PainIndex",
        "Worst1MReturn",
        "Worst3MReturn",
        "Worst6MReturn",
        "BestMissed1MReturnVsTQQQ",
        "PctTimeBelowPreviousHigh",
    ]:
        if col in show:
            show[col] = show[col].map(_fmt_pct)
    for col in [
        "Calmar",
        "AverageUnderwaterDays",
        "TimeToRecoveryAfterMaxDDDays",
        "RecoveryParticipationAfterDrawdown",
        "NewEquityHighs",
    ]:
        if col in show:
            show[col] = show[col].map(_fmt_num)

    ma_table = ma[
        [
            "slice",
            "CAGR",
            "MaxDD",
            "UlcerIndex",
            "PainIndex",
            "LongestUnderwaterDays",
            "TimeToRecoveryAfterMaxDDDays",
            "PctTimeBelowPreviousHigh",
            "BestMissed1MReturnVsTQQQ",
        ]
    ].copy() if not ma.empty else pd.DataFrame()
    if not ma_table.empty:
        for col in ["CAGR", "MaxDD", "UlcerIndex", "PainIndex", "PctTimeBelowPreviousHigh", "BestMissed1MReturnVsTQQQ"]:
            ma_table[col] = ma_table[col].map(_fmt_pct)
        ma_table["TimeToRecoveryAfterMaxDDDays"] = ma_table["TimeToRecoveryAfterMaxDDDays"].map(_fmt_num)

    if ma.empty:
        verdict = "No MA150 rows were available; underwater audit is inconclusive."
    else:
        verdict = "MA150 improves pain versus raw TQQQ and simple blends on several path metrics, but it is still not paper-trading ready."

    lines = [
        "# Underwater / Pain Audit for MA150 Risk-Off QQQ",
        "",
        "## Executive Verdict",
        "",
        verdict,
        "",
        "This audit measures investor tolerance metrics, not just CAGR or Calmar. It does not approve paper trading.",
        "",
        "## Data Availability",
        "",
        "- TQQQ, 70/30, 50/50, and MA150 rows use recomputed daily OOS equity from QQQ/TQQQ prices.",
        "- Current strict complex baseline has only retained summary CSV rows; daily underwater metrics are marked unavailable and are not invented.",
        "- Worst 1M/3M/6M returns use 21/63/126 trading-day rolling returns.",
        "",
        "## MA150 Pain Summary",
        "",
        _markdown_table(ma_table),
        "",
        "## Comparison",
        "",
        ma_wins_vs_tqqq,
        "",
        ma_wins_vs_70,
        "",
        ma_wins_vs_50,
        "",
        complex_text,
        "",
        "## Behavior Interpretation",
        "",
        "- MA150 is simple trend-following exposure control: it helps primarily by spending less time in fully levered TQQQ during sustained downtrends.",
        "- If MaxDD improves but recovery days or missed positive months worsen, the strategy may feel safer but still frustrate investors during recoveries.",
        "- Compared with simple blends, the key question is whether lower UlcerIndex/PainIndex compensates for missed rebounds and higher switching uncertainty.",
        "- Compared with the current complex baseline, this audit cannot prove daily pain superiority because the complex daily equity artifacts were not retained.",
        "",
        "## Paper-Trading Readiness",
        "",
        "NOT READY. The MA150 baseline has useful path-dependent evidence, but paper trading still requires retained daily artifacts, slippage/tax stress, and a frozen go/no-go hurdle.",
        "",
        "## Full Summary",
        "",
        _markdown_table(show),
    ]
    return "\n".join(lines) + "\n"


def write_underwater_pain_outputs(results: pd.DataFrame, out_prefix: str) -> tuple[Path, Path]:
    out_dir = Path(out_prefix).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "underwater_pain_summary.csv"
    report_path = out_dir / "UNDERWATER_PAIN_AUDIT.md"
    results.to_csv(csv_path, index=False)
    report_path.write_text(build_underwater_pain_report(results), encoding="utf-8")
    return csv_path, report_path


def _cost_win_line(results: pd.DataFrame, scenario: str, benchmark_strategy: str) -> str:
    ma = results[(results["strategy"] == "MA150 risk-off QQQ") & (results["scenario"] == scenario)].copy()
    other = results[(results["strategy"] == benchmark_strategy) & (results["scenario"] == scenario)].copy()
    if ma.empty or other.empty:
        return f"{scenario} versus {benchmark_strategy}: unavailable."
    merged = ma.merge(other, on="slice", how="inner", suffixes=("", "_other"))
    if merged.empty:
        return f"{scenario} versus {benchmark_strategy}: unavailable."
    denom = int(len(merged))
    return (
        f"{scenario} versus {benchmark_strategy}: CAGR wins {int((merged['CAGR'] > merged['CAGR_other']).sum())}/{denom}, "
        f"Calmar wins {int((merged['Calmar'] > merged['Calmar_other']).sum())}/{denom}, "
        f"MaxDD wins {int((merged['MaxDD'] > merged['MaxDD_other']).sum())}/{denom}, "
        f"Final equity wins {int((merged['Final_Equity'] > merged['Final_Equity_other']).sum())}/{denom}."
    )


def _delay_impact_line(results: pd.DataFrame, cost_bps: float) -> str:
    no_delay_name = f"cost_{int(cost_bps)}bps_delay0d"
    delay_name = f"stress_{int(cost_bps)}bps_delay1d"
    ma_base = results[(results["strategy"] == "MA150 risk-off QQQ") & (results["scenario"] == no_delay_name)].copy()
    ma_delay = results[(results["strategy"] == "MA150 risk-off QQQ") & (results["scenario"] == delay_name)].copy()
    if ma_base.empty or ma_delay.empty:
        return f"{int(cost_bps)} bps delay impact: unavailable."
    merged = ma_base.merge(ma_delay, on="slice", how="inner", suffixes=("_no_delay", "_delay"))
    if merged.empty:
        return f"{int(cost_bps)} bps delay impact: unavailable."
    calmar_delta = float((merged["Calmar_delay"] - merged["Calmar_no_delay"]).mean())
    cagr_delta = float((merged["CAGR_delay"] - merged["CAGR_no_delay"]).mean())
    final_delta = float((merged["Final_Equity_delay"] - merged["Final_Equity_no_delay"]).mean())
    worse = int((merged["Final_Equity_delay"] < merged["Final_Equity_no_delay"]).sum())
    return (
        f"{int(cost_bps)} bps + one-day delay: final equity worsened in {worse}/{len(merged)} slices; "
        f"mean CAGR delta {_fmt_pct(cagr_delta)}, mean Calmar delta {_fmt_num(calmar_delta)}, "
        f"mean final-equity delta {_fmt_num(final_delta)}."
    )


def build_cost_stress_report(results: pd.DataFrame) -> str:
    ma = results[results["strategy"] == "MA150 risk-off QQQ"].copy()
    static = results[results["family"].isin(["raw_tqqq", "static_blend"])].copy()
    ma_base = ma[ma["scenario"] == "base_0bps"].copy()

    if ma.empty:
        verdict = "Cost stress audit is inconclusive because no MA150 rows were generated."
    else:
        verdict = "MA150 remains a research candidate under moderate costs, but cost/delay stress is mixed and it is still NOT READY FOR PAPER TRADING."

    scenario_lines = []
    for scenario in ["cost_10bps_delay0d", "cost_25bps_delay0d", "cost_50bps_delay0d"]:
        scenario_lines.append(_cost_win_line(results, scenario, "70/30 TQQQ/QQQ"))
        scenario_lines.append(_cost_win_line(results, scenario, "50/50 TQQQ/QQQ"))

    delay_lines = [_delay_impact_line(results, 10.0), _delay_impact_line(results, 25.0)]

    turnover_text = "Turnover unavailable."
    if not ma.empty:
        ma_turnover = ma.groupby("scenario", as_index=False).agg(
            MeanAnnualizedTurnover=("annualized_turnover", "mean"),
            MeanTradeDays=("trade_days", "mean"),
            MeanCostDrag=("final_cost_drag", "mean"),
        )
        static_turnover = static.groupby("strategy", as_index=False).agg(
            MeanTradeDays=("trade_days", "mean"),
            MeanAnnualizedTurnover=("annualized_turnover", "mean"),
        )
        turnover_text = (
            "MA turnover comes from regime switches plus the initial OOS allocation. "
            "Static blends are modeled as buy-and-hold with no periodic rebalance, so they only incur initial allocation cost."
        )
    else:
        ma_turnover = pd.DataFrame()
        static_turnover = pd.DataFrame()

    show_cols = [
        "slice",
        "strategy",
        "scenario",
        "cost_bps",
        "execution_delay_days",
        "CAGR",
        "MaxDD",
        "Calmar",
        "Final_Equity",
        "total_turnover",
        "annualized_turnover",
        "trade_days",
        "final_cost_drag",
    ]
    show = results[show_cols].copy() if not results.empty else pd.DataFrame()
    if not show.empty:
        for col in ["CAGR", "MaxDD", "final_cost_drag"]:
            show[col] = show[col].map(_fmt_pct)
        for col in ["Calmar", "Final_Equity", "total_turnover", "annualized_turnover", "trade_days"]:
            show[col] = show[col].map(_fmt_num)

    ma_base_show = ma_base[
        ["slice", "CAGR", "MaxDD", "Calmar", "Final_Equity", "total_turnover", "trade_days"]
    ].copy() if not ma_base.empty else pd.DataFrame()
    if not ma_base_show.empty:
        for col in ["CAGR", "MaxDD"]:
            ma_base_show[col] = ma_base_show[col].map(_fmt_pct)
        for col in ["Calmar", "Final_Equity", "total_turnover", "trade_days"]:
            ma_base_show[col] = ma_base_show[col].map(_fmt_num)

    if not ma_turnover.empty:
        ma_turnover_show = ma_turnover.copy()
        ma_turnover_show["MeanAnnualizedTurnover"] = ma_turnover_show["MeanAnnualizedTurnover"].map(_fmt_num)
        ma_turnover_show["MeanTradeDays"] = ma_turnover_show["MeanTradeDays"].map(_fmt_num)
        ma_turnover_show["MeanCostDrag"] = ma_turnover_show["MeanCostDrag"].map(_fmt_pct)
    else:
        ma_turnover_show = ma_turnover

    if not static_turnover.empty:
        static_turnover_show = static_turnover.copy()
        static_turnover_show["MeanTradeDays"] = static_turnover_show["MeanTradeDays"].map(_fmt_num)
        static_turnover_show["MeanAnnualizedTurnover"] = static_turnover_show["MeanAnnualizedTurnover"].map(_fmt_num)
    else:
        static_turnover_show = static_turnover

    lines = [
        "# Cost Stress Audit for MA150 Risk-Off QQQ",
        "",
        "## Executive Verdict",
        "",
        verdict,
        "",
        "Current decision: HOLD. Paper-trading status: NOT READY FOR PAPER TRADING.",
        "",
        "## Execution Assumptions",
        "",
        "- Costs are one-way transaction costs applied only to changes in TQQQ and QQQ weights.",
        "- Costs are not applied to buy-and-hold daily returns and do not double-count TQQQ fund expense.",
        "- Static blends are buy-and-hold; no periodic rebalance is modeled, so they only incur initial OOS allocation cost.",
        "- One-day execution delay shifts already-lagged strategy weights by one more trading day; day t close signal can affect returns no earlier than day t+2.",
        "",
        "## MA150 Base Case",
        "",
        _markdown_table(ma_base_show),
        "",
        "## Cost Hurdles",
        "",
        *scenario_lines,
        "",
        "## Execution Delay Stress",
        "",
        *delay_lines,
        "",
        "## Turnover Attribution",
        "",
        turnover_text,
        "",
        "### MA150 Turnover By Scenario",
        "",
        _markdown_table(ma_turnover_show),
        "",
        "### Static Strategy Turnover",
        "",
        _markdown_table(static_turnover_show),
        "",
        "## Interpretation",
        "",
        "- If MA150 loses to simple blends after moderate costs, its research value should be reduced.",
        "- If one-day execution delay materially worsens results, implementation timing risk is a blocker.",
        "- Cost drag is mainly a function of switch frequency and whether the strategy moves between TQQQ and QQQ or cash.",
        "- This audit is not paper-trading approval; it is a hurdle check for continued research.",
        "",
        "## Full Summary",
        "",
        _markdown_table(show),
    ]
    return "\n".join(lines) + "\n"


def write_cost_stress_outputs(results: pd.DataFrame, out_prefix: str) -> tuple[Path, Path]:
    out_dir = Path(out_prefix).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "cost_stress_summary.csv"
    report_path = out_dir / "COST_STRESS_AUDIT.md"
    results.to_csv(csv_path, index=False)
    report_path.write_text(build_cost_stress_report(results), encoding="utf-8")
    return csv_path, report_path


def _average_holding_period_from_turnover(turnover: pd.Series, eps: float = 1e-12) -> float:
    ser = pd.Series(turnover).fillna(0.0).astype(float)
    if ser.empty:
        return float("nan")
    change_positions = np.flatnonzero(ser.to_numpy() > eps)
    boundaries = [0]
    boundaries.extend(int(x) for x in change_positions if int(x) > 0)
    boundaries.append(len(ser))
    boundaries = sorted(set(boundaries))
    lengths = [b - a for a, b in zip(boundaries[:-1], boundaries[1:]) if b > a]
    return float(np.mean(lengths)) if lengths else float(len(ser))


def calculate_turnover_tax_event_proxy(weights: pd.DataFrame) -> dict:
    """Estimate turnover, trade-event, and sell-side tax-event proxies.

    This is intentionally not tax advice. It only measures observable trading
    events in the backtest weights: buy-side changes, sell-side changes, and
    turnover. Actual tax treatment depends on jurisdiction and account type.
    """
    eff = _effective_weights_for_cost(weights, 0)
    eff.index = pd.to_datetime(eff.index)
    risky = eff[["w_tqqq", "w_qqq"]].astype(float)
    prev = risky.shift(1).fillna(0.0)
    delta = risky - prev
    buy_notional = delta.clip(lower=0.0).sum(axis=1)
    sell_notional = (-delta.clip(upper=0.0)).sum(axis=1)
    turnover = buy_notional + sell_notional
    ongoing_turnover = turnover.copy()
    if len(ongoing_turnover) > 0:
        ongoing_turnover.iloc[0] = 0.0

    years = max(len(eff) / PERIODS_PER_YEAR, 1e-12)
    trade_days = int((turnover > 1e-12).sum())
    ongoing_trade_days = int((ongoing_turnover > 1e-12).sum())
    buy_events = int((buy_notional > 1e-12).sum())
    sell_events = int((sell_notional > 1e-12).sum())

    annual_turnover = turnover.groupby(turnover.index.year).sum()
    annual_ongoing_turnover = ongoing_turnover.groupby(ongoing_turnover.index.year).sum()
    annual_trade_days = ongoing_turnover.groupby(ongoing_turnover.index.year).apply(lambda x: int((x > 1e-12).sum()))
    average_holding_period = _average_holding_period_from_turnover(turnover)
    short_holding_risk_proxy = (
        float(min(1.0, 30.0 / average_holding_period))
        if np.isfinite(average_holding_period) and average_holding_period > 0.0
        else float("nan")
    )
    pct_years_no_trades = float((annual_trade_days == 0).mean()) if len(annual_trade_days) else float("nan")
    max_annual_turnover = float(annual_turnover.max()) if len(annual_turnover) else 0.0
    worst_calendar_year = str(int(annual_turnover.idxmax())) if len(annual_turnover) else ""
    worst_calendar_year_ongoing = (
        str(int(annual_ongoing_turnover.idxmax())) if len(annual_ongoing_turnover) else ""
    )
    ongoing_max_annual_turnover = float(annual_ongoing_turnover.max()) if len(annual_ongoing_turnover) else 0.0
    operational_burden_score = float(
        min(
            100.0,
            2.0 * (trade_days / years)
            + 4.0 * (ongoing_trade_days / years)
            + 3.0 * (float(turnover.sum()) / years)
            + 1.5 * max_annual_turnover
            + 15.0 * (short_holding_risk_proxy if np.isfinite(short_holding_risk_proxy) else 0.0),
        )
    )

    return {
        "turnover_series": turnover.rename("Turnover"),
        "buy_notional_series": buy_notional.rename("BuyNotionalProxy"),
        "sell_notional_series": sell_notional.rename("SellNotionalProxy"),
        "annual_turnover_by_year": {str(int(k)): float(v) for k, v in annual_turnover.items()},
        "annual_ongoing_turnover_by_year": {str(int(k)): float(v) for k, v in annual_ongoing_turnover.items()},
        "trades_per_year": float(trade_days / years),
        "switches_per_year": float(ongoing_trade_days / years),
        "average_turnover_per_year": float(turnover.sum() / years),
        "max_annual_turnover": max_annual_turnover,
        "number_sell_events": sell_events,
        "number_buy_events": buy_events,
        "estimated_taxable_events_proxy": sell_events,
        "taxable_sell_notional_proxy": float(sell_notional.sum()),
        "average_holding_period_proxy_days": average_holding_period,
        "short_holding_risk_proxy": short_holding_risk_proxy,
        "percentage_years_with_no_trades": pct_years_no_trades,
        "worst_calendar_year_turnover": max_annual_turnover,
        "worst_calendar_year": worst_calendar_year,
        "ongoing_max_annual_turnover": ongoing_max_annual_turnover,
        "worst_calendar_year_ongoing_turnover": ongoing_max_annual_turnover,
        "worst_calendar_year_ongoing": worst_calendar_year_ongoing,
        "operational_burden_score": operational_burden_score,
        "trade_days": trade_days,
        "ongoing_trade_days": ongoing_trade_days,
        "total_turnover": float(turnover.sum()),
        "ongoing_turnover": float(ongoing_turnover.sum()),
    }


def run_operational_feasibility_audit(
    df: pd.DataFrame,
    strict_slices: Iterable[dict] | None = None,
) -> pd.DataFrame:
    """Audit operational burden and sell-side event proxies for fixed baselines."""
    px = _require_price_frame(df)
    slices = list(strict_slices or default_strict_slices(px))
    rows = []
    for sl in slices:
        train_start = pd.Timestamp(sl["train_start"])
        test_start = pd.Timestamp(sl["test_start"])
        test_end = min(pd.Timestamp(sl["test_end"]), pd.Timestamp(px.index.max()))
        df_slice = px.loc[(px.index >= train_start) & (px.index <= test_end)].copy()
        if df_slice.empty:
            continue
        oos_index = df_slice.loc[(df_slice.index >= test_start) & (df_slice.index <= test_end)].index
        if len(oos_index) < 2:
            continue

        benchmark = _normalize(_equity_from_returns(_price_returns(df_slice).reindex(oos_index).fillna(0.0)["TQQQ"]))
        for label, family, fn in _cost_strategy_specs():
            result = fn(df_slice)
            weights_oos = result["weights"].reindex(oos_index).dropna(how="all")
            equity_oos = _normalize(result["equity"].reindex(oos_index))
            metrics = compute_performance_metrics(
                equity_oos,
                benchmark_equity=benchmark,
                rf_ann=DEFAULT_RF_ANN,
                periods_per_year=PERIODS_PER_YEAR,
            )
            proxy = calculate_turnover_tax_event_proxy(weights_oos)
            row = {
                "slice": sl["slice"],
                "strategy": label,
                "family": family,
                "train_start": sl["train_start"],
                "train_end": sl["train_end"],
                "test_start": sl["test_start"],
                "test_end": test_end.strftime("%Y-%m-%d"),
                "CAGR": metrics["CAGR"],
                "MaxDD": metrics["MaxDD"],
                "Calmar": metrics["Calmar"],
                "Final_Equity": metrics["Final_Equity"],
                "trades_per_year": proxy["trades_per_year"],
                "switches_per_year": proxy["switches_per_year"],
                "average_turnover_per_year": proxy["average_turnover_per_year"],
                "max_annual_turnover": proxy["max_annual_turnover"],
                "number_sell_events": proxy["number_sell_events"],
                "number_buy_events": proxy["number_buy_events"],
                "estimated_taxable_events_proxy": proxy["estimated_taxable_events_proxy"],
                "taxable_sell_notional_proxy": proxy["taxable_sell_notional_proxy"],
                "average_holding_period_proxy_days": proxy["average_holding_period_proxy_days"],
                "short_holding_risk_proxy": proxy["short_holding_risk_proxy"],
                "percentage_years_with_no_trades": proxy["percentage_years_with_no_trades"],
                "worst_calendar_year_turnover": proxy["worst_calendar_year_turnover"],
                "worst_calendar_year": proxy["worst_calendar_year"],
                "ongoing_max_annual_turnover": proxy["ongoing_max_annual_turnover"],
                "worst_calendar_year_ongoing_turnover": proxy["worst_calendar_year_ongoing_turnover"],
                "worst_calendar_year_ongoing": proxy["worst_calendar_year_ongoing"],
                "operational_burden_score": proxy["operational_burden_score"],
                "trade_days": proxy["trade_days"],
                "ongoing_trade_days": proxy["ongoing_trade_days"],
                "total_turnover": proxy["total_turnover"],
                "ongoing_turnover": proxy["ongoing_turnover"],
                "PeriodicRebalance": False,
                "TaxAdvice": False,
            }
            rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out

    for benchmark_strategy, suffix in [
        ("70/30 TQQQ/QQQ", "70_30"),
        ("50/50 TQQQ/QQQ", "50_50"),
        ("TQQQ buy-and-hold", "tqqq_buy_hold"),
    ]:
        bench = out[out["strategy"] == benchmark_strategy][
            ["slice", "operational_burden_score", "trades_per_year", "average_turnover_per_year"]
        ].rename(
            columns={
                "operational_burden_score": f"burden_score_{suffix}",
                "trades_per_year": f"trades_per_year_{suffix}",
                "average_turnover_per_year": f"turnover_per_year_{suffix}",
            }
        )
        out = out.merge(bench, on="slice", how="left")
        out[f"extra_burden_vs_{suffix}"] = out["operational_burden_score"] - out[f"burden_score_{suffix}"]
        out[f"extra_trades_per_year_vs_{suffix}"] = out["trades_per_year"] - out[f"trades_per_year_{suffix}"]
        out[f"extra_turnover_per_year_vs_{suffix}"] = (
            out["average_turnover_per_year"] - out[f"turnover_per_year_{suffix}"]
        )
    return out


def _operational_comparison_line(results: pd.DataFrame, benchmark_strategy: str) -> str:
    ma = results[results["strategy"] == "MA150 risk-off QQQ"].copy()
    other = results[results["strategy"] == benchmark_strategy].copy()
    if ma.empty or other.empty:
        return f"MA150 versus {benchmark_strategy}: unavailable."
    merged = ma.merge(other, on="slice", how="inner", suffixes=("", "_other"))
    if merged.empty:
        return f"MA150 versus {benchmark_strategy}: unavailable."
    return (
        f"MA150 versus {benchmark_strategy}: mean extra trades/year "
        f"{_fmt_num((merged['trades_per_year'] - merged['trades_per_year_other']).mean())}, "
        f"mean extra annual turnover {_fmt_num((merged['average_turnover_per_year'] - merged['average_turnover_per_year_other']).mean())}, "
        f"mean extra operational burden score "
        f"{_fmt_num((merged['operational_burden_score'] - merged['operational_burden_score_other']).mean())}."
    )


def build_operational_feasibility_report(results: pd.DataFrame) -> str:
    ma = results[results["strategy"] == "MA150 risk-off QQQ"].copy()
    if ma.empty:
        verdict = "Operational feasibility audit is inconclusive because no MA150 rows were generated."
        ma_show = pd.DataFrame()
    else:
        verdict = (
            "MA150 is operationally feasible for continued research, but the added trade events versus "
            "static blends keep the decision at HOLD and NOT READY FOR PAPER TRADING."
        )
        ma_show = ma[
            [
                "slice",
                "trades_per_year",
                "switches_per_year",
                "average_turnover_per_year",
                "max_annual_turnover",
                "number_sell_events",
                "number_buy_events",
                "estimated_taxable_events_proxy",
                "taxable_sell_notional_proxy",
                "average_holding_period_proxy_days",
                "percentage_years_with_no_trades",
                "operational_burden_score",
            ]
        ].copy()
        for col in ma_show.columns:
            if col != "slice":
                ma_show[col] = ma_show[col].map(_fmt_num)

    agg = pd.DataFrame()
    if not results.empty:
        agg = results.groupby("strategy", as_index=False).agg(
            MeanTradesPerYear=("trades_per_year", "mean"),
            MeanSwitchesPerYear=("switches_per_year", "mean"),
            MeanAnnualTurnover=("average_turnover_per_year", "mean"),
            MeanSellEvents=("number_sell_events", "mean"),
            MeanTaxableSellNotionalProxy=("taxable_sell_notional_proxy", "mean"),
            MeanHoldingPeriodProxyDays=("average_holding_period_proxy_days", "mean"),
            MeanOperationalBurdenScore=("operational_burden_score", "mean"),
        )
        for col in agg.columns:
            if col != "strategy":
                agg[col] = agg[col].map(_fmt_num)

    show = results[
        [
            "slice",
            "strategy",
            "trades_per_year",
            "switches_per_year",
            "average_turnover_per_year",
            "max_annual_turnover",
            "number_sell_events",
            "number_buy_events",
            "estimated_taxable_events_proxy",
            "taxable_sell_notional_proxy",
            "average_holding_period_proxy_days",
            "percentage_years_with_no_trades",
            "operational_burden_score",
            "PeriodicRebalance",
        ]
    ].copy() if not results.empty else pd.DataFrame()
    if not show.empty:
        for col in [
            "trades_per_year",
            "switches_per_year",
            "average_turnover_per_year",
            "max_annual_turnover",
            "number_sell_events",
            "number_buy_events",
            "estimated_taxable_events_proxy",
            "taxable_sell_notional_proxy",
            "average_holding_period_proxy_days",
            "percentage_years_with_no_trades",
            "operational_burden_score",
        ]:
            show[col] = show[col].map(_fmt_num)

    lines = [
        "# Operational Feasibility Audit for MA150 Risk-Off QQQ",
        "",
        "## Executive Verdict",
        "",
        verdict,
        "",
        "Current decision: HOLD. Paper-trading status: NOT READY FOR PAPER TRADING.",
        "",
        "## Scope And Tax Disclaimer",
        "",
        "- This audit measures turnover, trade events, sell-side event proxies, and operational burden.",
        "- It does not provide formal tax advice.",
        "- Tax treatment depends on jurisdiction/account type and requires professional advice.",
        "- For a Singapore-based investor, practical concerns often include operating discipline, USD products, broker execution, reporting, and estate tax / withholding considerations rather than only US-style short-term capital gains. This is not a legal conclusion.",
        "- Static blends are modeled as buy-and-hold with no periodic rebalance; they only have the initial OOS allocation event.",
        "",
        "## MA150 Summary",
        "",
        _markdown_table(ma_show),
        "",
        "## Comparison Versus Static Blends",
        "",
        _operational_comparison_line(results, "TQQQ buy-and-hold"),
        _operational_comparison_line(results, "70/30 TQQQ/QQQ"),
        _operational_comparison_line(results, "50/50 TQQQ/QQQ"),
        "",
        "## Strategy-Level Operational Burden",
        "",
        _markdown_table(agg),
        "",
        "## Feasibility Interpretation",
        "",
        "- MA150 is materially more complex than buy-and-hold or static blends because it can require sell-side events during regime switches.",
        "- The strategy is still simple enough to execute manually at low frequency, but paper-trading evidence should include retained daily artifacts and an operator checklist.",
        "- Automation is useful for consistency and audit logs, but the rule should remain manually understandable before any live or paper workflow.",
        "- The sell-side taxable-event proxy is not a tax estimate; it only flags that realized-sale events may exist in taxable accounts.",
        "- If operational burden or taxable-event proxy offsets the pain/Calmar improvement, the MA strategy should remain research-only.",
        "",
        "## Full Summary",
        "",
        _markdown_table(show),
    ]
    return "\n".join(lines) + "\n"


def write_operational_feasibility_outputs(results: pd.DataFrame, out_prefix: str) -> tuple[Path, Path]:
    out_dir = Path(out_prefix).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "operational_feasibility_summary.csv"
    report_path = out_dir / "OPERATIONAL_FEASIBILITY_AUDIT.md"
    results.to_csv(csv_path, index=False)
    report_path.write_text(build_operational_feasibility_report(results), encoding="utf-8")
    return csv_path, report_path


def _monitor_signal_state(close: float, ma_value: float) -> str:
    if not np.isfinite(float(ma_value)):
        return "risk_off"
    return "risk_on" if float(close) > float(ma_value) else "risk_off"


def generate_frozen_ma150_monitor(
    df: pd.DataFrame,
    asof_date=None,
    risk_on_asset: str = "TQQQ",
    risk_off_asset: str = "QQQ",
    ma_window: int = 150,
) -> dict:
    """Generate a monitoring-only frozen MA150 signal report.

    The as-of close signal is a target for the next trading session. This
    function does not place orders, read accounts, or generate executable
    instructions.
    """
    px = _require_price_frame(df)
    if int(ma_window) != 150:
        raise ValueError("frozen monitor is locked to ma_window=150.")
    if str(risk_on_asset).upper() != "TQQQ" or str(risk_off_asset).upper() != "QQQ":
        raise ValueError("frozen monitor is locked to risk-on TQQQ and risk-off QQQ.")

    if asof_date is not None:
        cutoff = px.loc[px.index <= pd.Timestamp(asof_date)].copy()
    else:
        cutoff = px.copy()
    if cutoff.empty:
        raise ValueError("no price rows are available at or before asof_date.")

    qqq = cutoff["QQQ"].astype(float)
    ma = qqq.rolling(int(ma_window)).mean()
    asof_ts = pd.Timestamp(cutoff.index[-1])
    latest_close = float(qqq.iloc[-1])
    latest_ma = float(ma.iloc[-1]) if pd.notna(ma.iloc[-1]) else float("nan")
    distance = (latest_close / latest_ma - 1.0) if np.isfinite(latest_ma) and abs(latest_ma) > 1e-12 else float("nan")

    signal_state = _monitor_signal_state(latest_close, latest_ma)
    previous_signal_state = "unknown"
    signal_changed = False
    if len(cutoff) >= 2:
        previous_signal_state = _monitor_signal_state(float(qqq.iloc[-2]), float(ma.iloc[-2]) if pd.notna(ma.iloc[-2]) else float("nan"))
        signal_changed = signal_state != previous_signal_state

    state_series = pd.Series(
        [_monitor_signal_state(float(c), float(m) if pd.notna(m) else float("nan")) for c, m in zip(qqq, ma)],
        index=cutoff.index,
        dtype=object,
    )
    switch_mask = state_series.ne(state_series.shift())
    if len(switch_mask) > 0:
        switch_mask.iloc[0] = False
    switch_dates = state_series.index[switch_mask.fillna(False)]
    if len(switch_dates) > 0:
        last_switch_ts = pd.Timestamp(switch_dates[-1])
        days_since_last_switch = int(cutoff.index.get_loc(asof_ts) - cutoff.index.get_loc(last_switch_ts))
        last_switch_date = last_switch_ts.strftime("%Y-%m-%d")
    else:
        last_switch_date = None
        days_since_last_switch = None

    target_asset = str(risk_on_asset).upper() if signal_state == "risk_on" else str(risk_off_asset).upper()
    notes = [
        "Monitoring only; not a trade instruction.",
        "Day t close signal applies to the next trading session.",
        "Frozen rule: QQQ close > QQQ MA150 means risk-on TQQQ; otherwise risk-off QQQ.",
        "Decision remains HOLD and paper-trading status remains NOT_READY.",
        "No broker connection, account read, order generation, or automatic execution is performed.",
    ]
    return {
        "asof_date": asof_ts.strftime("%Y-%m-%d"),
        "latest_close": latest_close,
        "ma150": latest_ma,
        "distance_to_ma_pct": distance,
        "signal_state": signal_state,
        "target_asset_next_session": target_asset,
        "previous_signal_state": previous_signal_state,
        "signal_changed": bool(signal_changed),
        "last_switch_date": last_switch_date,
        "days_since_last_switch": days_since_last_switch,
        "rule_name": "Frozen MA150 risk-off QQQ",
        "risk_on_asset": str(risk_on_asset).upper(),
        "risk_off_asset": str(risk_off_asset).upper(),
        "ma_window": int(ma_window),
        "decision_status": "HOLD",
        "paper_trading_status": "NOT_READY",
        "notes": notes,
    }


def build_frozen_monitor_spec() -> str:
    lines = [
        "# Frozen MA150 Forward Monitor Specification",
        "",
        "## Frozen Rule",
        "",
        "- MA150 rule is frozen.",
        "- Risk-on asset: TQQQ.",
        "- Risk-off asset: QQQ.",
        "- Signal is based on QQQ close versus QQQ MA150.",
        "- Signal at day t close applies to the next trading session.",
        "- The monitor is not paper-trading ready and does not authorize allocation.",
        "",
        "## Safety Boundary",
        "",
        "- No broker connection.",
        "- No account read.",
        "- No order generation.",
        "- No automatic execution.",
        "- No trade recommendation.",
        "- Output is monitoring-only and must not be treated as an instruction to trade.",
        "",
        "## Manual Checklist Before Any Future Paper Trading",
        "",
        "1. Confirm no-lookahead daily timing remains covered by tests.",
        "2. Confirm strict OOS and raw TQQQ benchmark assumptions remain unchanged.",
        "3. Confirm cost, turnover, and operational feasibility hurdles remain acceptable.",
        "4. Confirm daily artifacts are retained for every monitor run used in research review.",
        "5. Confirm the rule remains frozen; do not tune MA windows from monitor outcomes.",
        "6. Confirm paper-trading entry hurdles in GO_NO_GO_HURDLES.md are met.",
        "",
        "## Conditions Before Paper Trading Is Reconsidered",
        "",
        "- Current status must move from HOLD to an explicitly documented approval state.",
        "- The strategy must pass the frozen go/no-go hurdles without parameter changes.",
        "- Slippage/cost, underwater/pain, and operational-burden evidence must remain acceptable.",
        "- A separate paper-trading plan must define capital cap, kill switch, logs, and manual operator controls.",
        "- This monitor alone is insufficient evidence for paper trading.",
    ]
    return "\n".join(lines) + "\n"


def _json_safe_value(value):
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if np.isfinite(val) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def build_frozen_monitor_report(monitor: dict) -> str:
    lines = [
        "# Frozen MA150 Monitor",
        "",
        "Monitoring only, not trade instruction.",
        "",
        "## Current Signal",
        "",
        f"- As-of date: {monitor['asof_date']}",
        f"- QQQ close: {_fmt_num(monitor['latest_close'])}",
        f"- QQQ MA150: {_fmt_num(monitor['ma150'])}",
        f"- Distance to MA: {_fmt_pct(monitor['distance_to_ma_pct'])}",
        f"- Current signal: {monitor['signal_state']}",
        f"- Next-session target: {monitor['target_asset_next_session']}",
        f"- Signal changed?: {monitor['signal_changed']}",
        f"- Last switch date: {monitor['last_switch_date'] or 'n/a'}",
        f"- Trading sessions since switch: {monitor['days_since_last_switch'] if monitor['days_since_last_switch'] is not None else 'n/a'}",
        f"- Status: {monitor['decision_status']} / {str(monitor['paper_trading_status']).replace('_', ' ')} FOR PAPER TRADING",
        "",
        "## Manual Review Checklist",
        "",
        "- Confirm this run used only prices available at or before the as-of date.",
        "- Confirm no broker, account, or order system was connected.",
        "- Confirm this monitor output is not being used as an order ticket.",
        "- Confirm MA150 remains frozen and no parameter was changed.",
        "- Confirm the HOLD / NOT READY status remains in force.",
        "- Confirm any future paper-trading consideration is handled in a separate approval document.",
        "",
        "## Notes",
        "",
        *[f"- {note}" for note in monitor.get("notes", [])],
    ]
    return "\n".join(lines) + "\n"


def write_frozen_monitor_outputs(monitor: dict, monitor_out: str | Path) -> tuple[Path, Path, Path]:
    report_path = Path(monitor_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path = report_path.parent / "FROZEN_MONITOR_SPEC.md"
    json_path = report_path.with_suffix(".json")
    spec_path.write_text(build_frozen_monitor_spec(), encoding="utf-8")
    report_path.write_text(build_frozen_monitor_report(monitor), encoding="utf-8")
    json_path.write_text(json.dumps(_json_safe_value(monitor), indent=2, sort_keys=True), encoding="utf-8")
    return spec_path, report_path, json_path


MONITOR_HISTORY_COLUMNS = [
    "run_timestamp_local",
    "asof_date",
    "qqq_close",
    "ma150",
    "distance_to_ma_pct",
    "signal_state",
    "target_asset_next_session",
    "previous_signal_state",
    "signal_changed",
    "last_switch_date",
    "days_since_last_switch",
    "decision_status",
    "paper_trading_status",
    "rule_name",
    "monitoring_only",
]


def monitor_history_row(monitor: dict, run_timestamp_local: str | None = None) -> dict:
    return {
        "run_timestamp_local": run_timestamp_local or pd.Timestamp.now().isoformat(timespec="seconds"),
        "asof_date": monitor["asof_date"],
        "qqq_close": monitor["latest_close"],
        "ma150": monitor["ma150"],
        "distance_to_ma_pct": monitor["distance_to_ma_pct"],
        "signal_state": monitor["signal_state"],
        "target_asset_next_session": monitor["target_asset_next_session"],
        "previous_signal_state": monitor["previous_signal_state"],
        "signal_changed": bool(monitor["signal_changed"]),
        "last_switch_date": monitor["last_switch_date"],
        "days_since_last_switch": monitor["days_since_last_switch"],
        "decision_status": monitor["decision_status"],
        "paper_trading_status": monitor["paper_trading_status"],
        "rule_name": monitor["rule_name"],
        "monitoring_only": True,
    }


def append_monitor_history(
    monitor: dict,
    history_csv: str | Path = "reports/minimal_baseline/frozen_monitor_history.csv",
    allow_duplicate: bool = False,
    run_timestamp_local: str | None = None,
) -> tuple[Path, bool]:
    """Append a monitoring-only history row.

    The history intentionally excludes account, broker, order, shares, and
    notional fields. It is not an order log and must not be used as one.
    """
    path = Path(history_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = monitor_history_row(monitor, run_timestamp_local=run_timestamp_local)

    if path.exists():
        existing = pd.read_csv(path)
        if "asof_date" in existing.columns and not allow_duplicate:
            if str(row["asof_date"]) in set(existing["asof_date"].astype(str)):
                return path, False
        out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])

    for col in MONITOR_HISTORY_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[MONITOR_HISTORY_COLUMNS]
    out.to_csv(path, index=False)
    return path, True


def build_monitor_review_playbook() -> str:
    lines = [
        "# Frozen Monitor Review Playbook",
        "",
        "## Scope",
        "",
        "- The monitor is read-only and monitoring only.",
        "- It is not a trade recommendation.",
        "- It is not paper trading.",
        "- It does not connect to brokers, read accounts, generate orders, or suggest position size.",
        "- Signal is generated after the close and is only meaningful for the next trading session.",
        "- Current status remains HOLD / NOT READY FOR PAPER TRADING.",
        "",
        "## Required Review When signal_changed=True",
        "",
        "1. Confirm the data date is correct.",
        "2. Confirm QQQ close is credible against an independent market-data source.",
        "3. Confirm MA150 is reasonable and based only on available close data.",
        "4. Check for missing data, holidays, partial sessions, or stale prices.",
        "5. Confirm the strategy still satisfies the go/no-go hurdles.",
        "6. Confirm the project remains HOLD / NOT READY.",
        "",
        "## Prohibited Actions",
        "",
        "- Do not automatically place orders.",
        "- Do not read account data.",
        "- Do not change the rule.",
        "- Do not enter live or paper trading based on a single signal.",
        "- Do not treat monitor target as a trade instruction.",
        "- Do not add broker, shares, notional, account, or order fields to monitor history.",
        "",
        "## Record Discipline",
        "",
        "- History rows are signal observations, not trades.",
        "- Duplicate as-of dates are skipped by default.",
        "- Use duplicate rows only when explicitly documenting a rerun or data correction.",
        "- Any future paper-trading approval must be documented outside this monitor.",
    ]
    return "\n".join(lines) + "\n"


def write_monitor_review_playbook(out_dir: str | Path = "reports/minimal_baseline") -> Path:
    path = Path(out_dir) / "MONITOR_REVIEW_PLAYBOOK.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_monitor_review_playbook(), encoding="utf-8")
    return path


def write_outputs(summary: pd.DataFrame, out_prefix: str) -> tuple[Path, Path]:
    prefix = Path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(f"{out_prefix}_summary.csv")
    report_path = prefix.parent / "MINIMAL_BASELINE_REPORT.md"
    summary.to_csv(csv_path, index=False)
    report_path.write_text(build_minimal_baseline_report(summary, out_prefix), encoding="utf-8")
    return csv_path, report_path


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Run minimal TQQQ risk-management baselines.")
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="auto")
    ap.add_argument("--qqq_csv", default=None)
    ap.add_argument("--tqqq_csv", default=None)
    ap.add_argument("--slices_csv", default=None)
    ap.add_argument("--out_prefix", default="reports/minimal_baseline/minimal_tqqq")
    ap.add_argument("--ma_behavior_audit", action="store_true")
    ap.add_argument("--underwater_pain_audit", action="store_true")
    ap.add_argument("--save_daily_artifacts", action="store_true")
    ap.add_argument("--artifact_dir", default="reports/minimal_baseline/raw")
    ap.add_argument("--cost_stress_audit", action="store_true")
    ap.add_argument("--cost_bps_list", default="0,5,10,25,50")
    ap.add_argument("--execution_delay_days", type=int, default=0)
    ap.add_argument("--operational_feasibility_audit", action="store_true")
    ap.add_argument("--frozen_monitor", action="store_true")
    ap.add_argument("--monitor_asof", default=None)
    ap.add_argument("--monitor_out", default="reports/minimal_baseline/frozen_monitor_latest.md")
    ap.add_argument("--append_monitor_history", action="store_true")
    ap.add_argument("--monitor_history_csv", default="reports/minimal_baseline/frozen_monitor_history.csv")
    ap.add_argument("--allow_duplicate_monitor_history", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    end = args.end
    if end is None or str(end).lower() == "auto":
        end = (pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = load_price_data(args.start, end, qqq_csv=args.qqq_csv, tqqq_csv=args.tqqq_csv)
    monitor_only = args.frozen_monitor and not any(
        [
            args.ma_behavior_audit,
            args.underwater_pain_audit,
            args.save_daily_artifacts,
            args.cost_stress_audit,
            args.operational_feasibility_audit,
        ]
    )
    if monitor_only:
        monitor = generate_frozen_ma150_monitor(df, asof_date=args.monitor_asof)
        spec_path, monitor_path, json_path = write_frozen_monitor_outputs(monitor, args.monitor_out)
        playbook_path = write_monitor_review_playbook(Path(args.monitor_out).parent)
        print(f"[minimal] wrote {spec_path}")
        print(f"[minimal] wrote {monitor_path}")
        print(f"[minimal] wrote {json_path}")
        print(f"[minimal] wrote {playbook_path}")
        if args.append_monitor_history:
            history_path, appended = append_monitor_history(
                monitor,
                history_csv=args.monitor_history_csv,
                allow_duplicate=args.allow_duplicate_monitor_history,
            )
            action = "appended" if appended else "skipped duplicate"
            print(f"[minimal] {action} monitor history: {history_path}")
        print(f"[minimal] frozen monitor target next session: {monitor['target_asset_next_session']}")
        return 0

    slices = _load_slices_from_csv(args.slices_csv, df)
    summary = run_minimal_baseline_suite(df, slices)
    csv_path, report_path = write_outputs(summary, args.out_prefix)
    print(f"[minimal] wrote {csv_path}")
    print(f"[minimal] wrote {report_path}")
    if not summary.empty:
        best, _ = _best_strategy(summary)
        print(f"[minimal] best composite strategy: {best}")
    if args.ma_behavior_audit:
        ma_results = run_ma_regime_variants(df, slices)
        ma_csv, ma_report = write_ma_behavior_outputs(ma_results, args.out_prefix)
        analysis = analyze_ma_regime_behavior(df, ma_results)
        print(f"[minimal] wrote {ma_csv}")
        print(f"[minimal] wrote {ma_report}")
        print(f"[minimal] best MA variant: {analysis['best_variant']}")
    if args.underwater_pain_audit:
        pain_results = run_underwater_pain_audit(df, slices)
        pain_csv, pain_report = write_underwater_pain_outputs(pain_results, args.out_prefix)
        print(f"[minimal] wrote {pain_csv}")
        print(f"[minimal] wrote {pain_report}")
    if args.save_daily_artifacts:
        artifact_paths = save_daily_artifacts(df, slices, artifact_dir=args.artifact_dir)
        print(f"[minimal] wrote {len(artifact_paths)} daily artifact files to {args.artifact_dir}")
    if args.cost_stress_audit:
        cost_results = run_cost_stress_suite(
            df,
            slices,
            cost_bps_list=args.cost_bps_list,
            execution_delay_days=args.execution_delay_days,
        )
        cost_csv, cost_report = write_cost_stress_outputs(cost_results, args.out_prefix)
        print(f"[minimal] wrote {cost_csv}")
        print(f"[minimal] wrote {cost_report}")
    if args.operational_feasibility_audit:
        operational_results = run_operational_feasibility_audit(df, slices)
        operational_csv, operational_report = write_operational_feasibility_outputs(
            operational_results,
            args.out_prefix,
        )
        print(f"[minimal] wrote {operational_csv}")
        print(f"[minimal] wrote {operational_report}")
    if args.frozen_monitor:
        monitor = generate_frozen_ma150_monitor(df, asof_date=args.monitor_asof)
        spec_path, monitor_path, json_path = write_frozen_monitor_outputs(monitor, args.monitor_out)
        playbook_path = write_monitor_review_playbook(Path(args.monitor_out).parent)
        print(f"[minimal] wrote {spec_path}")
        print(f"[minimal] wrote {monitor_path}")
        print(f"[minimal] wrote {json_path}")
        print(f"[minimal] wrote {playbook_path}")
        if args.append_monitor_history:
            history_path, appended = append_monitor_history(
                monitor,
                history_csv=args.monitor_history_csv,
                allow_duplicate=args.allow_duplicate_monitor_history,
            )
            action = "appended" if appended else "skipped duplicate"
            print(f"[minimal] {action} monitor history: {history_path}")
        print(f"[minimal] frozen monitor target next session: {monitor['target_asset_next_session']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
