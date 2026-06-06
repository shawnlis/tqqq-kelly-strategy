#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
    YF_AVAILABLE = True
except Exception:
    yf = None  # type: ignore
    YF_AVAILABLE = False


def load_execution(path: str, broker: str, start: str | None, end: str | None) -> pd.DataFrame:
    exe = pd.read_csv(path, parse_dates=["date"])
    exe = exe[exe["broker"].str.lower() == broker.lower()]
    exe = exe[exe["entry_type"] != "missed"]
    if start:
        exe = exe[exe["date"] >= pd.to_datetime(start)]
    if end:
        exe = exe[exe["date"] <= pd.to_datetime(end)]
    exe = exe.sort_values("date").reset_index(drop=True)
    return exe


def load_backtest(path: str) -> pd.DataFrame:
    bt = pd.read_csv(path)
    date_col = None
    for cand in ("date", "Date"):
        if cand in bt.columns:
            date_col = cand
            break
    if date_col is None:
        date_col = bt.columns[0]
    equity_col = None
    for col in bt.columns:
        if col.lower().endswith("equity"):
            equity_col = col
            break
    if equity_col is None:
        raise ValueError("backtest equity file must contain a column ending with 'equity'")
    bt = bt[[date_col, equity_col]].rename(columns={date_col: "date", equity_col: "bt_nav"})
    bt["date"] = pd.to_datetime(bt["date"])
    return bt


def attach_backtest(
    exe: pd.DataFrame,
    bt: pd.DataFrame,
    nav_column: str = "nav",
    scale_backtest: bool = False
) -> pd.DataFrame:
    merged = exe.merge(bt, on="date", how="left")
    merged[nav_column] = pd.to_numeric(merged[nav_column], errors="coerce")
    merged["bt_nav"] = pd.to_numeric(merged["bt_nav"], errors="coerce")
    merged["bt_nav_scaled"] = merged["bt_nav"]
    if scale_backtest and not merged.empty:
        first_row = merged.iloc[0]
        nav0 = first_row.get(nav_column)
        bt0 = first_row.get("bt_nav")
        if nav0 is not None and bt0 not in (None, 0) and np.isfinite(nav0) and np.isfinite(bt0):
            scale = float(nav0) / float(bt0)
            merged["bt_nav_scaled"] = merged["bt_nav"] * scale
        else:
            scale_backtest = False  # fall back silently
    merged["live_over_bt"] = np.nan
    merged["abs_diff"] = np.nan
    bt_col = "bt_nav_scaled" if scale_backtest else "bt_nav"
    valid = merged[bt_col].notna() & merged[nav_column].notna() & (merged[bt_col].abs() > 1e-12)
    merged.loc[valid, "live_over_bt"] = merged.loc[valid, nav_column] / merged.loc[valid, bt_col] - 1.0
    merged.loc[valid, "abs_diff"] = merged.loc[valid, nav_column] - merged.loc[valid, bt_col]
    if "effective_leverage_realized" in merged.columns:
        merged["eff_lev_live"] = pd.to_numeric(merged["effective_leverage_realized"], errors="coerce")
    else:
        merged["eff_lev_live"] = np.nan
    if "effective_leverage_target" in merged.columns:
        merged["eff_lev_model"] = pd.to_numeric(merged["effective_leverage_target"], errors="coerce")
    elif "effective_leverage" in merged.columns:
        merged["eff_lev_model"] = pd.to_numeric(merged["effective_leverage"], errors="coerce")
    else:
        merged["eff_lev_model"] = np.nan
    return merged


def download_prices(dates: pd.Series) -> pd.DataFrame | None:
    if not YF_AVAILABLE or dates.empty:
        return None
    start = pd.Timestamp(dates.min()) - timedelta(days=2)
    end = pd.Timestamp(dates.max()) + timedelta(days=2)
    tickers = ["QQQ", "TQQQ", "QQQ5.L"]
    try:
        px = yf.download(
            tickers,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            auto_adjust=False,
            progress=False,
        )
    except Exception:
        return None
    if px.empty:
        return None
    if isinstance(px.columns, pd.MultiIndex):
        if "Close" in px.columns.get_level_values(0):
            close_df = px["Close"].copy()
        else:
            close_df = px.copy()
    else:
        close_df = px.copy()
    close_df = close_df.rename(columns={"QQQ5.L": "QQQ5.L"})
    return close_df


def compute_nav_from_close(exe: pd.DataFrame, prices: pd.DataFrame | None) -> tuple[pd.DataFrame, str]:
    if prices is None or exe.empty:
        exe["nav_close"] = np.nan
        return exe, "price_data_unavailable"
    prices = prices.sort_index()

    def pick_price(ts: pd.Timestamp):
        if ts in prices.index:
            return prices.loc[ts]
        before = prices.loc[:ts]
        if before.empty:
            return None
        return before.iloc[-1]

    def _nav(row):
        d = pd.Timestamp(row["date"])
        px = pick_price(d)
        if px is None:
            return np.nan
        cash = pd.to_numeric(row.get("cash_balance"), errors="coerce")
        qqq = pd.to_numeric(row.get("pos_qqq"), errors="coerce")
        tqqq = pd.to_numeric(row.get("pos_tqqq"), errors="coerce")
        qqq5 = pd.to_numeric(row.get("pos_qqq5"), errors="coerce")
        mnq_nom = pd.to_numeric(row.get("pos_mnq_nominal"), errors="coerce")
        cash = cash if np.isfinite(cash) else 0.0
        qqq = qqq if np.isfinite(qqq) else 0.0
        tqqq = tqqq if np.isfinite(tqqq) else 0.0
        qqq5 = qqq5 if np.isfinite(qqq5) else 0.0
        mnq_nom = mnq_nom if np.isfinite(mnq_nom) else 0.0
        needed = ["QQQ", "TQQQ", "QQQ5.L"]
        if any(sym not in px or not np.isfinite(px[sym]) for sym in needed):
            return np.nan
        return (
            float(cash)
            + float(qqq) * float(px["QQQ"])
            + float(tqqq) * float(px["TQQQ"])
            + float(qqq5) * float(px["QQQ5.L"])
            + float(mnq_nom)
        )

    exe["nav_close"] = exe.apply(_nav, axis=1)
    return exe, "yfinance_close"


def compute_weight_diffs(df: pd.DataFrame, prices: pd.DataFrame | None) -> tuple[pd.DataFrame, str]:
    if prices is None:
        df["dw_qqq"] = np.nan
        df["dw_tqqq"] = np.nan
        df["dw_qqq5"] = np.nan
        return df, "price_data_unavailable"
    prices = prices.sort_index()
    tickers = ["QQQ", "TQQQ", "QQQ5.L"]

    def _weight_diff(row):
        nav = row.get("nav")
        if nav is None or not np.isfinite(nav) or nav == 0:
            return pd.Series({"dw_qqq": np.nan, "dw_tqqq": np.nan, "dw_qqq5": np.nan})
        d = pd.Timestamp(row["date"])
        try:
            px = prices.loc[d]
        except KeyError:
            # asof previous close
            px = prices.loc[:d].iloc[-1] if d >= prices.index.min() else None
        if px is None or any(sym not in px or not np.isfinite(px[sym]) for sym in tickers):
            return pd.Series({"dw_qqq": np.nan, "dw_tqqq": np.nan, "dw_qqq5": np.nan})
        def real_weight(symbol: str, price_symbol: str, sim_key: str) -> float:
            shares = row.get(symbol)
            price = px.get(price_symbol)
            if shares is None or price is None or not np.isfinite(price):
                return np.nan
            return (float(shares) * float(price)) / float(nav)
        w_real = {
            "QQQ": real_weight("pos_qqq", "QQQ", "sim_weight_qqq"),
            "TQQQ": real_weight("pos_tqqq", "TQQQ", "sim_weight_tqqq"),
            "QQQ5": real_weight("pos_qqq5", "QQQ5.L", "sim_weight_qqq5"),
        }
        return pd.Series({
            "dw_qqq": w_real["QQQ"] - row.get("sim_weight_qqq", np.nan) if np.isfinite(w_real["QQQ"]) else np.nan,
            "dw_tqqq": w_real["TQQQ"] - row.get("sim_weight_tqqq", np.nan) if np.isfinite(w_real["TQQQ"]) else np.nan,
            "dw_qqq5": w_real["QQQ5"] - row.get("sim_weight_qqq5", np.nan) if np.isfinite(w_real["QQQ5"]) else np.nan,
        })

    diffs = df.apply(_weight_diff, axis=1)
    df[["dw_qqq", "dw_tqqq", "dw_qqq5"]] = diffs
    return df, "yfinance_close"


def parse_args():
    ap = argparse.ArgumentParser(description="Compare execution record vs backtest equity.")
    ap.add_argument("--execution_path", type=str, default="logs/ib_execution_log.csv")
    ap.add_argument("--backtest_equity_path", type=str, default="crash_cap_check_dl_equity.csv")
    ap.add_argument("--broker", type=str, default="ib")
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--save-merged", type=str, default=None,
                    help="Optional path to save merged execution/backtest comparison CSV.")
    ap.add_argument("--compute-close-nav", action="store_true",
                    help="Compute NAV at close using positions/cash and yfinance close prices.")
    ap.add_argument("--scale-backtest", action="store_true",
                    help="Scale backtest NAV to match the first overlapping live NAV column used in the comparison.")
    return ap.parse_args()


def main():
    args = parse_args()
    exec_path = Path(args.execution_path)
    bt_path = Path(args.backtest_equity_path)
    if not exec_path.exists():
        raise FileNotFoundError(f"Execution record not found: {exec_path}")
    if not bt_path.exists():
        raise FileNotFoundError(f"Backtest equity file not found: {bt_path}")

    exe = load_execution(str(exec_path), args.broker, args.start, args.end)
    if exe.empty:
        print("No execution rows after filtering; nothing to analyze.")
        return
    bt = load_backtest(str(bt_path))
    prices_full = download_prices(exe["date"])

    if args.compute_close_nav:
        exe, nav_source = compute_nav_from_close(exe, prices_full)
        nav_col = "nav_close"
    else:
        nav_source = "live_nav_column"
        nav_col = "nav"

    merged = attach_backtest(exe, bt, nav_column=nav_col, scale_backtest=args.scale_backtest)

    print(f"Rows analyzed: {len(merged)} from {merged['date'].min().date()} to {merged['date'].max().date()}")
    if args.scale_backtest:
        bt_ref = "bt_nav_scaled"
        scale_note = " (scaled to first overlap)"
    else:
        bt_ref = "bt_nav"
        scale_note = ""

    live_te = pd.to_numeric(merged["live_over_bt"], errors="coerce")
    valid_te = live_te.dropna()
    if not valid_te.empty:
        mean_abs = valid_te.abs().mean()
        max_abs = valid_te.abs().max()
        print(f"Mean |live_over_bt| (using {nav_col} vs {bt_ref}{scale_note}): {mean_abs:.6e}")
        print(f"Max  |live_over_bt|: {max_abs:.6e}")
        print(valid_te.describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]))
    else:
        print("live_over_bt: no valid data")
    if "delta_nav_vs_sim" in merged.columns:
        delta = pd.to_numeric(merged["delta_nav_vs_sim"], errors="coerce").dropna()
        if not delta.empty:
            print("\ndelta_nav_vs_sim stats:")
            print(delta.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95]))

    merged["abs_dev"] = merged["abs_diff"].abs()
    if merged["abs_dev"].notna().any():
        top = merged.sort_values("abs_dev", ascending=False).head(10)
        print(f"\nTop 10 days by |{nav_col} - {bt_ref}|:")
        cols = ["date", nav_col, bt_ref, "abs_diff", "live_over_bt", "eff_lev_live", "eff_lev_model"]
        cols = [c for c in cols if c in top.columns]
        print(top[cols])

    prices = download_prices(merged["date"])
    merged, weight_source = compute_weight_diffs(merged, prices)
    weight_cols = ["dw_qqq", "dw_tqqq", "dw_qqq5"]
    print("Weight diffs (real - sim):")
    print(merged[weight_cols].describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]))
    for col in weight_cols:
        max_abs = merged[col].abs().max()
        if np.isfinite(max_abs):
            print(f"Max |{col}|: {max_abs:.6f}")
    print(f"Weight source: {weight_source}")
    if args.compute_close_nav:
        print(f"NAV close source: {nav_source}")

    if args.save_merged:
        out_path = Path(args.save_merged)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["date", "bt_nav", "bt_nav_scaled", nav_col, "live_over_bt", "abs_diff", "eff_lev_live", "eff_lev_model"]
        merged.to_csv(out_path, index=False, columns=[c for c in cols if c in merged.columns])
        print(f"Merged comparison saved to {out_path}")


if __name__ == "__main__":
    main()
