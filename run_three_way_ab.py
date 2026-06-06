#!/usr/bin/env python3
"""
Run a three-way comparison of DL variants:
    V0 - Legacy (no trend gate, daily_ret reward)
    V1 - Trend-gated QQQ5 (daily_ret reward)
    V2 - Trend gate + fwd5_utility contextual bandit reward
Outputs a sorted summary table with CAGR / Sharpe / MaxDD / Calmar / Final Equity /
Avg effective leverage / mean L_after_crash on top 5% up-days.
"""
import argparse
import datetime as dt
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

import qqq_deep_learning_and_baseline_experimental as strat


# CLI defaults mirror qqq_deep_learning_and_baseline_experimental.py
DEFAULTS = dict(
    base_kelly_frac=0.493816,
    kelly_lookback_days=252 * 3,
    rebal_days=3,
    fut_fin_spread=0.002,
    tqqq_expense=0.009,
    qqq5_expense=0.0095,
    trade_cost_bps=1.0,
    bandit_alpha=0.69744,
    target_vol=0.329758,
    dl_conf=0.62,
    dl_max_daily_qqq5=0.08,
    dl_delever_threshold=-0.004,
    dl_delever_frac=0.05,
    dl_max_qqq5=0.35,
    dl_max_tqqq=0.60,
    dl_trade_max_frac=0.35,
    dl_cooldown=3,
    dl_look_ahead=3,
    slip_bps=1.0,
    impact_k=0.0005,
    risk_gate_max_fut_frac=0.55,
    risk_gate_max_leverage=2.2,
    kelly_max_step=0.10,
    tqqq_slip_bps=1.0,
    qqq5_slip_bps=15.0,
    adv_daily_frac_cap=0.10,
    bandit_reward_window=20,
    bandit_ewma_alpha=0.2,
    bandit_dd_lambda=0.5,
    bandit_dd_trigger=0.05,
    bandit_crash_lambda=0.2,
    bandit_lam_dd=3.0,
)


VARIANTS = {
    "V0": {
        "label": "Legacy (no trend gate, daily_ret reward)",
        "overrides": {
            "mom21_gate": -1.0,
            "mom63_gate": -1.0,
            "bandit_reward_mode": "daily_ret",
        },
    },
    "V1": {
        "label": "Trend-gated QQQ5 (daily_ret reward)",
        "overrides": {
            "mom21_gate": 0.02,
            "mom63_gate": 0.05,
            "bandit_reward_mode": "daily_ret",
        },
    },
    "V2": {
        "label": "Trend gate + fwd5_utility reward",
        "overrides": {
            "mom21_gate": 0.02,
            "mom63_gate": 0.05,
            "bandit_reward_mode": "fwd5_utility",
        },
    },
}


def _parse_args():
    parser = argparse.ArgumentParser(description="Three-way AB harness for DL variants.")
    parser.add_argument("--start", type=str, default="2015-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--rf", type=float, default=None, help="Override risk-free rate (annualized).")
    parser.add_argument("--qqq_csv", type=str, default=None, help="Optional QQQ price CSV.")
    parser.add_argument("--tqqq_csv", type=str, default=None, help="Optional TQQQ price CSV.")
    parser.add_argument("--qqq5_csv", type=str, default=None, help="Optional QQQ5 price CSV.")
    parser.add_argument("--adv_tqqq_csv", type=str, default=None, help="Optional ADV CSV for TQQQ.")
    parser.add_argument("--adv_qqq5_csv", type=str, default=None, help="Optional ADV CSV for QQQ5.")
    parser.add_argument("--out_prefix", type=str, default="three_way_ab")
    parser.add_argument("--keep_metrics", action="store_true", help="Keep per-variant metrics CSVs.")
    return parser.parse_args()


def _load_adv_csv(path: str | None) -> pd.Series | None:
    if not path:
        return None
    try:
        ser = pd.read_csv(path, index_col=0).squeeze()
        ser.index = pd.to_datetime(ser.index)
        ser = pd.to_numeric(ser, errors="coerce").dropna()
        return ser.sort_index()
    except Exception as exc:  # pragma: no cover - logging for manual runs
        print(f"[warn] Failed to load ADV CSV {path}: {exc}")
        return None


def _compute_extra_metrics(csv_path: Path) -> Tuple[float, float]:
    if not csv_path.exists():
        return (float("nan"), float("nan"))
    df = pd.read_csv(csv_path)
    avg_lev = float(df["effective_leverage"].mean()) if "effective_leverage" in df.columns else float("nan")
    top_mean = float("nan")
    if "qqq_ret" in df.columns and "L_after_crash" in df.columns:
        thresh = df["qqq_ret"].quantile(0.95)
        top = df[df["qqq_ret"] >= thresh]
        if len(top) > 0:
            top_mean = float(top["L_after_crash"].mean())
    return avg_lev, top_mean


def _prepare_market_data(args) -> Tuple[pd.DataFrame, Dict]:
    namespace = argparse.Namespace(
        qqq_csv=args.qqq_csv,
        tqqq_csv=args.tqqq_csv,
        qqq5_csv=args.qqq5_csv,
        start=args.start,
        end=args.end,
    )
    df, synth5 = strat.load_prices(namespace)
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)
    df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
    if df.empty:
        raise SystemExit("No price data in the requested window.")
    rf_series = None
    if args.rf is None:
        rf_series = strat.fetch_rf_series_or_default(args.start, args.end)
        if rf_series is not None and len(rf_series) > 0:
            rf_ann = strat.get_rf_value_for_date(rf_series, rf_series.index[-1], 0.02)
        else:
            rf_ann = 0.02
    else:
        rf_ann = float(args.rf)
    vix_series = strat.fetch_vix_series_or_none(args.start, args.end)
    adv_series = strat.fetch_tqqq_adv_series(args.start, args.end, lookback=20)
    adv_tqqq = _load_adv_csv(args.adv_tqqq_csv)
    adv_qqq5 = _load_adv_csv(args.adv_qqq5_csv)
    meta = dict(
        rf_series=rf_series,
        rf_ann=rf_ann,
        vix_series=vix_series,
        adv_series=adv_series,
        adv_tqqq=adv_tqqq,
        adv_qqq5=adv_qqq5,
        synth5=synth5,
    )
    return df, meta


def _build_common_kwargs(meta: Dict) -> Dict:
    return dict(
        rf_series=meta["rf_series"],
        rf_ann=meta["rf_ann"],
        base_kelly_frac=DEFAULTS["base_kelly_frac"],
        kelly_lookback_days=DEFAULTS["kelly_lookback_days"],
        rebalance_every_days=DEFAULTS["rebal_days"],
        fut_fin_spread=DEFAULTS["fut_fin_spread"],
        tqqq_expense=DEFAULTS["tqqq_expense"],
        qqq5_expense=DEFAULTS["qqq5_expense"],
        trade_cost_bps=DEFAULTS["trade_cost_bps"],
        policy_mode="bandit",
        bandit_alpha=DEFAULTS["bandit_alpha"],
        use_risk_gate=True,
        vix_series=meta["vix_series"],
        target_vol=DEFAULTS["target_vol"],
        dl_conf=DEFAULTS["dl_conf"],
        dl_max_daily_qqq5=DEFAULTS["dl_max_daily_qqq5"],
        dl_delever_threshold=DEFAULTS["dl_delever_threshold"],
        dl_delever_frac=DEFAULTS["dl_delever_frac"],
        dl_max_qqq5=DEFAULTS["dl_max_qqq5"],
        dl_max_tqqq=DEFAULTS["dl_max_tqqq"],
        dl_trade_max_frac=DEFAULTS["dl_trade_max_frac"],
        dl_cooldown=DEFAULTS["dl_cooldown"],
        dl_look_ahead=DEFAULTS["dl_look_ahead"],
        adv_series=meta["adv_series"],
        slip_bps=DEFAULTS["slip_bps"],
        impact_k=DEFAULTS["impact_k"],
        risk_gate_max_fut_frac=DEFAULTS["risk_gate_max_fut_frac"],
        risk_gate_max_leverage=DEFAULTS["risk_gate_max_leverage"],
        kelly_max_step=DEFAULTS["kelly_max_step"],
        tqqq_slip_bps=DEFAULTS["tqqq_slip_bps"],
        qqq5_slip_bps=DEFAULTS["qqq5_slip_bps"],
        adv_series_tqqq=meta["adv_tqqq"],
        adv_series_qqq5=meta["adv_qqq5"],
        adv_daily_frac_cap=DEFAULTS["adv_daily_frac_cap"],
        metrics_csv_path=None,  # replaced per variant
        crash_mode="soft_scale",
        crash_tail_frac=0.05,
        bandit_reward_mode=DEFAULTS["bandit_reward_mode"],
        bandit_reward_window=DEFAULTS["bandit_reward_window"],
        bandit_ewma_alpha=DEFAULTS["bandit_ewma_alpha"],
        bandit_dd_lambda=DEFAULTS["bandit_dd_lambda"],
        bandit_dd_trigger=DEFAULTS["bandit_dd_trigger"],
        bandit_crash_lambda=DEFAULTS["bandit_crash_lambda"],
        bandit_lam_dd=DEFAULTS["bandit_lam_dd"],
        allow_cash=False,
    )


def main():
    args = _parse_args()
    if args.end.lower() in {"auto", "today"}:
        args.end = dt.date.today().isoformat()
    df, meta = _prepare_market_data(args)
    common_kwargs = _build_common_kwargs(meta)
    out_dir = Path(args.out_prefix)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    for key, cfg in VARIANTS.items():
        metrics_path = out_dir / f"{key}_metrics.csv"
        if metrics_path.exists():
            metrics_path.unlink()
        variant_kw = dict(common_kwargs)
        variant_kw.update(cfg["overrides"])
        variant_kw["metrics_csv_path"] = str(metrics_path)
        print(f"\n=== Running {key}: {cfg['label']} ===")
        res = strat.deep_learning_backtest(df, **variant_kw)
        report = res[2]
        avg_lev, top_mean = _compute_extra_metrics(metrics_path)
        summary_rows.append({
            "Variant": key,
            "Label": cfg["label"],
            "CAGR": float(report.get("CAGR", np.nan)),
            "Sharpe": float(report.get("Sharpe_ex_rf0", np.nan)),
            "MaxDD": float(report.get("MaxDD", np.nan)),
            "Calmar": float(report.get("Calmar", np.nan)),
            "Final_Equity": float(report.get("Final_Equity", np.nan)),
            "Avg_Leverage": avg_lev,
            "TopUp_L_after_crash": top_mean,
        })
        if not args.keep_metrics:
            metrics_path.unlink(missing_ok=True)

    summary_df = pd.DataFrame(summary_rows).sort_values(by="CAGR", ascending=False).reset_index(drop=True)
    print("\n=== Three-way comparison ===")
    pd.options.display.float_format = "{:,.4f}".format
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
