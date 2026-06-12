#!/usr/bin/env python
"""Minimal strict-OOS TQQQ / QQQ / cash risk-management baselines.

This module intentionally avoids the complex strategy stack: no DL, no Bandit,
no Kelly, no QQQ5, and no synthetic products. Signals are daily close signals
that are shifted one bar before they affect returns.
"""

from __future__ import annotations

import argparse
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
    if risk_off_asset not in {"cash", "qqq"}:
        raise ValueError("risk_off_asset must be 'cash' or 'qqq'.")
    px = _require_price_frame(df)
    ma = px["QQQ"].rolling(ma_window).mean()
    raw_signal = (px["QQQ"] > ma).astype(float)
    risk_on_for_pnl = raw_signal.shift(1).fillna(0.0)
    w_tqqq = risk_on_for_pnl
    w_qqq = (1.0 - risk_on_for_pnl) if risk_off_asset == "qqq" else 0.0
    w_cash = 1.0 - w_tqqq - (w_qqq if isinstance(w_qqq, pd.Series) else 0.0)
    strategy = f"ma_regime_qqq_ma{ma_window}_riskoff_{risk_off_asset}"
    weights = _weights_frame(px.index, w_tqqq, w_qqq, w_cash, strategy)
    weights["raw_signal"] = raw_signal
    weights["signal_lagged"] = risk_on_for_pnl
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
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    end = args.end
    if end is None or str(end).lower() == "auto":
        end = (pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = load_price_data(args.start, end, qqq_csv=args.qqq_csv, tqqq_csv=args.tqqq_csv)
    slices = _load_slices_from_csv(args.slices_csv, df)
    summary = run_minimal_baseline_suite(df, slices)
    csv_path, report_path = write_outputs(summary, args.out_prefix)
    print(f"[minimal] wrote {csv_path}")
    print(f"[minimal] wrote {report_path}")
    if not summary.empty:
        best, _ = _best_strategy(summary)
        print(f"[minimal] best composite strategy: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
