#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ib_paper_trader.py  (dual-broker: IBKR + Moomoo SG OpenD)

Paper-trade harness (ETF / MNQ) with v5f committee support + production hardening.
This version can execute on BOTH IBKR and Moomoo SG OpenD in a single run
(or just one broker, if you prefer).

Key features retained & extended:
- Committee (mean/median/voltarget/dsr) + optional clamp, OOS prune
- Idempotency lock per (asof, broker, committee fingerprint)
- Drift/turnover/min trade guards
- XNYS-aware close-window gate (ET; half-days handled)
- Safe price sourcing (IB first, robust yfinance fallback; moomoo snapshot/quote with fallback)
- Env + YAML config (CLI overrides all)
- Telegram notifications (debounced) + JSONL audit log
- Per-broker summaries with consistent schema

Moomoo notes:
- Uses Futu OpenD (module "futu"). Install: pip install futu-api
- US ETFs only (QQQ / TQQQ). MNQ not supported via moomoo adapter.
- Codes use 'QQQ.US' / 'TQQQ.US'.
- Requires OpenD running and logged-in on the same host, with trading unlocked.

Usage quickstart:
  # Dry-run: committee mean of top-3 keepers, ETF sleeve, run on BOTH IB & Moomoo:
  python ib_paper_trader.py --keepers-csv robust_rel_v5f_run4_topk_final.csv \
    --oos-csv robust_rel_v5f_run4_oos.csv --topn 3 --committee mean \
    --exec-mode etf --brokers ib moomoo --dry

  # Live paper: pick row 0 only, MNQ sleeve (IB only), Telegram push
  python ib_paper_trader.py --keepers-csv robust_rel_v5f_run4_topk_final.csv \
    --pick-row 0 --committee off --exec-mode mnq --brokers ib \
    --notify-telegram-bot $BOT --notify-telegram-chat $CHAT
"""

import os, sys, math, time, json, argparse, hashlib, pathlib, csv
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List, Iterable, Mapping
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytz

# --- yfinance (robust fallback) ---
try:
    import yfinance as yf
except Exception:
    yf = None

# --- requests (optional for Telegram) ---
try:
    import requests as _rq
except Exception:
    _rq = None

# --- market calendar (XNYS) ---
try:
    import pandas_market_calendars as mcal
    _XNYS_CAL = mcal.get_calendar('XNYS')
except Exception:
    _XNYS_CAL = None

_EASTERN = pytz.timezone("America/New_York")

# --- IBKR ---
try:
    from ib_insync import IB, Stock, Future, MarketOrder, Contract, util, TagValue
except Exception:
    print("ERROR: ib_insync not installed. Run: pip install ib-insync")
    raise

# --- Moomoo / Futu OpenD (optional) ---
try:
    from futu import (
        OpenQuoteContext, OpenUSTradeContext,
        TrdEnv, TrdSide, OrderType, RET_OK
    )
    _FUTU_AVAILABLE = True
except Exception:
    _FUTU_AVAILABLE = False


# =========================
# .env + YAML config
# =========================
def load_env_file(path: str = ".env") -> Dict[str, str]:
    env: Dict[str, str] = {}
    p = pathlib.Path(path)
    if not p.exists():
        return env
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        env[key] = value
        os.environ.setdefault(key, value)
    return env


def load_yaml_config(path: str) -> Dict:
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        print(f"[config] PyYAML not available; skipping config file {path}")
        return {}
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _fetch_prices_for_date(asof: date, symbols: list[str]) -> dict[str, float | None]:
    """
    Fetch yfinance closes for the given date (with a small window tolerance).
    Falls back to prior close if exact date is missing.
    """
    if yf is None:
        return {s: None for s in symbols}
    start = (asof - timedelta(days=2)).isoformat()
    end = (asof + timedelta(days=2)).isoformat()
    try:
        df = yf.download(symbols, start=start, end=end, auto_adjust=False, progress=False)
    except Exception:
        return {s: None for s in symbols}
    if df is None or df.empty:
        return {s: None for s in symbols}
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df
    out: dict[str, float | None] = {}
    for s in symbols:
        series = close.get(s) if hasattr(close, "get") else None
        if series is None or series.empty:
            out[s] = None
            continue
        try:
            ser_sorted = series.sort_index()
            if pd.Timestamp(asof) in ser_sorted.index:
                out[s] = float(ser_sorted.loc[pd.Timestamp(asof)])
            else:
                prior = ser_sorted[ser_sorted.index <= pd.Timestamp(asof)]
                out[s] = float(prior.iloc[-1]) if not prior.empty else None
        except Exception:
            out[s] = None
    return out


def flatten_live_config(cfg: Dict) -> Dict[str, object]:
    if not cfg:
        return {}
    flat: Dict[str, object] = {}
    simple_keys = [
        "strategy",
        "start",
        "end",
        "keepers_csv",
        "oos_csv",
        "topn",
        "committee",
        "committee_clamp",
        "pick_row",
        "exec_mode",
        "idempotency_dir",
        "jsonl",
        "base_kelly_frac",
        "target_vol",
        "bandit_alpha",
        "rebal_days",
        # You may optionally add 'brokers' here in YAML as a list
    ]
    for key in simple_keys:
        if key in cfg and cfg[key] is not None:
            flat[key] = cfg[key]
    for bool_key in ("oos_prune", "close_window", "use_fut_mnq"):
        if bool_key in cfg and cfg[bool_key] is not None:
            flat[bool_key] = bool(cfg[bool_key])
    for key in ("mm_qqq5_replicate",):
        if key in cfg and cfg[key] is not None:
            flat[key] = bool(cfg[key])
    if "mm_qqq5_leverage" in cfg and cfg["mm_qqq5_leverage"] is not None:
        flat["mm_qqq5_leverage"] = float(cfg["mm_qqq5_leverage"])
    guards = cfg.get("guards") or {}
    if isinstance(guards, dict):
        guard_keys = [
            "min_trade_value",
            "min_shares",
            "max_turnover",
            "min_drift",
            "sum_cap",
            "qqq5_scale",
        ]
        for key in guard_keys:
            if key in guards and guards[key] is not None:
                flat[key] = guards[key]
    # Optional moomoo defaults in YAML (non-secrets)
    mm = cfg.get("moomoo") or {}
    if isinstance(mm, dict):
        for k in ("mm_host", "mm_port", "mm_trd_env", "mm_acc_id"):
            if k in mm and mm[k] is not None:
                flat[k] = mm[k]
    return flat


def env_defaults_from_os() -> Dict[str, object]:
    mapping = {}
    env_map = {
        # IB
        "IB_HOST": ("ib_host", str),
        "IB_PORT": ("ib_port", int),
        "IB_CLIENT_ID": ("ib_client_id", int),
        "IB_ACCOUNT": ("ib_account", str),
        # Telegram
        "TELEGRAM_BOT_TOKEN": ("notify_telegram_bot", str),
        "TELEGRAM_CHAT_ID": ("notify_telegram_chat", str),
        # Moomoo (OpenD)
        "MOOMOO_OPEND_HOST": ("mm_host", str),
        "MOOMOO_OPEND_PORT": ("mm_port", int),
        "MOOMOO_TRD_ENV": ("mm_trd_env", str),        # "SIMULATE" or "REAL"
        "MOOMOO_ACC_ID": ("mm_acc_id", int),
        "MOOMOO_TRADE_PWD": ("mm_trade_pwd", str),
    }
    for env_key, (attr, caster) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None and val != "":
            try:
                mapping[attr] = caster(val)
            except Exception:
                mapping[attr] = val
    return mapping


def apply_env_overrides(args: argparse.Namespace) -> None:
    env_map = {
        # IB
        "IB_HOST": ("ib_host", str),
        "IB_PORT": ("ib_port", int),
        "IB_CLIENT_ID": ("ib_client_id", int),
        "IB_ACCOUNT": ("ib_account", str),
        # Telegram
        "TELEGRAM_BOT_TOKEN": ("notify_telegram_bot", str),
        "TELEGRAM_CHAT_ID": ("notify_telegram_chat", str),
        # Moomoo (OpenD)
        "MOOMOO_OPEND_HOST": ("mm_host", str),
        "MOOMOO_OPEND_PORT": ("mm_port", int),
        "MOOMOO_TRD_ENV": ("mm_trd_env", str),
        "MOOMOO_ACC_ID": ("mm_acc_id", int),
        "MOOMOO_TRADE_PWD": ("mm_trade_pwd", str),
    }
    for env_key, (attr, caster) in env_map.items():
        env_val = os.environ.get(env_key)
        if not env_val:
            continue
        current = getattr(args, attr, None)
        if current not in (None, "", 0):
            continue
        try:
            setattr(args, attr, caster(env_val))
        except Exception:
            setattr(args, attr, env_val)


# =========================
# Date helpers
# =========================
def _parse_date_like(x) -> Optional[date]:
    if x is None: return None
    if isinstance(x, (date, datetime, pd.Timestamp)):
        return pd.Timestamp(x).date()
    s = str(x).strip().lower()
    if s in ("", "none"): return None
    if s in ("today", "now"): return pd.Timestamp.today().date()
    if s in ("yesterday", "yd"): return (pd.Timestamp.today() - pd.Timedelta(days=1)).date()
    if len(s) >= 2 and s[:-1].isdigit() and s[-1] in "dwmy":
        n, u = int(s[:-1]), s[-1]; base = pd.Timestamp.today()
        if u == "d": return (base - pd.Timedelta(days=n)).date()
        if u == "w": return (base - pd.Timedelta(weeks=n)).date()
        if u == "m": return (base - pd.DateOffset(months=n)).date()
        if u == "y": return (base - pd.DateOffset(years=n)).date()
    return pd.to_datetime(s).date()


def normalize_dates_for_yf(start_like, end_like) -> Tuple[str, str]:
    s = _parse_date_like(start_like) or (pd.Timestamp.today() - pd.DateOffset(years=10)).date()
    if isinstance(end_like, str) and end_like.strip().lower() == "auto":
        e = pd.Timestamp.today().date()
    else:
        e = _parse_date_like(end_like) or pd.Timestamp.today().date()
    if s > e: s, e = e, s
    e_excl = (pd.Timestamp(e) + pd.Timedelta(days=1)).date()
    return s.isoformat(), e_excl.isoformat()


# =========================
# yfinance robust close
# =========================
def yf_safe_close(symbol: str, days_back: int = 30) -> float:
    if yf is None:
        raise RuntimeError("yfinance not available")
    days_back = max(int(days_back), 1)
    try:
        df = yf.download(symbol, period=f"{days_back}d", progress=False, auto_adjust=False)
        if df is None or df.empty:
            raise RuntimeError("empty period")
    except Exception:
        end_dt = datetime.now(timezone.utc).date()
        start_dt = end_dt - timedelta(days=days_back + 7)
        df = yf.download(
            symbol,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            raise RuntimeError(f"yfinance returned no data for {symbol}")
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    target = df[col] if col in df.columns else df
    if isinstance(target, pd.DataFrame):
        if symbol in target.columns:
            target = target[symbol]
        else:
            target = target.squeeze()
    s = pd.to_numeric(target, errors="coerce").dropna()
    if s.empty:
        raise RuntimeError(f"no close for {symbol}")
    return float(s.iloc[-1])


# =========================
# Strategy glue
# =========================
def import_strategy_module(module_path_or_name: str):
    import importlib, importlib.util
    if os.path.isfile(module_path_or_name):
        spec = importlib.util.spec_from_file_location("strategy_mod", module_path_or_name)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        return mod
    return importlib.import_module(module_path_or_name)


@dataclass
class StrategySnapshot:
    asof: pd.Timestamp
    w_fut: float
    w_tqqq: float
    w_qqq5: float
    L_base: float


def run_strategy_snapshot(mod, start_iso: str, end_iso: str,
                          base_kelly_frac: float, target_vol: float,
                          bandit_alpha: float, rebal_days: int = 5,
                          other: Dict = None) -> StrategySnapshot:
    other = other or {}
    class Args:
        qqq_csv=None; tqqq_csv=None; qqq5_csv=None
        start=None; end=None
    a=Args(); a.start=start_iso; a.end=end_iso

    df, synth5 = mod.load_prices(a)
    rf_series = mod.fetch_rf_series_or_default(a.start, a.end)
    vix_series = mod.fetch_vix_series_or_none(a.start, a.end)
    adv_series = mod.fetch_tqqq_adv_series(a.start, a.end, lookback=20)

    common_kwargs = dict(
        rf_series=rf_series,
        rf_ann=other.get("rf_ann", 0.02),
        base_kelly_frac=base_kelly_frac,
        kelly_lookback_days=other.get("kelly_lookback_days", 252*3),
        rebalance_every_days=rebal_days,
        fut_fin_spread=other.get("fut_fin_spread", 0.002),
        tqqq_expense=other.get("tqqq_expense", 0.009),
        qqq5_expense=other.get("qqq5_expense", 0.0095),
        trade_cost_bps=other.get("trade_cost_bps", 1.0),
        policy_mode=other.get("policy", "bandit"),
        bandit_alpha=bandit_alpha,
        use_risk_gate=other.get("risk_gate", True),
        vix_series=vix_series,
        target_vol=target_vol,
        dl_conf=other.get("dl_conf", 0.62),
        dl_max_qqq5=other.get("dl_max_qqq5", 0.45),
        dl_max_tqqq=other.get("dl_max_tqqq", 0.60),
        dl_trade_max_frac=other.get("dl_trade_max_frac", 0.20),
        dl_cooldown=other.get("dl_cooldown", 3),
        adv_series=adv_series,
        slip_bps=other.get("slip_bps", 1.5),
        impact_k=other.get("impact_k", 0.0005),
    )
    initial_train_end = other.get("initial_train_end") or "2018-12-31"
    initial_train_end = pd.Timestamp(initial_train_end).strftime("%Y-%m-%d")
    print(f"[strategy] initial_train_end={initial_train_end}")

    backtest_fn = getattr(mod, "deep_learning_backtest", None)
    if backtest_fn is None:
        raise RuntimeError("Strategy module is missing deep_learning_backtest.")
    res = backtest_fn(
        df,
        initial_train_end=initial_train_end,
        **common_kwargs,
    )
    weights_df = res[3]
    latest = weights_df.iloc[-1]
    asof = weights_df.index[-1]
    return StrategySnapshot(
        asof=pd.Timestamp(asof),
        w_fut=float(latest['w_fut']),
        w_tqqq=float(latest['w_tqqq']),
        w_qqq5=float(latest['w_qqq5']),
        L_base=float(latest['L_base']),
    )


# =========================
# Committee utils
# =========================
@dataclass
class ExecTargets:
    tgt_cash_frac: float
    tgt_qqq_frac: float
    tgt_tqqq_frac: float
    tgt_qqq5_frac: float
    tgt_mnq_nominal: float


def compute_effective_leverage(targets: ExecTargets) -> float:
    """
    Approximate beta exposure vs QQQ by summing the sleeves:
    QQQ @ 1x, TQQQ @ 3x, QQQ5 @ 5x, futures sleeve via MNQ nominal.
    """
    eff = float(targets.tgt_qqq_frac) + 3.0 * float(targets.tgt_tqqq_frac) + 5.0 * float(targets.tgt_qqq5_frac)
    eff += float(targets.tgt_mnq_nominal)
    return eff


def compute_realized_effective_leverage_from_positions(
    positions: Mapping[str, float | int] | None,
    prices: Mapping[str, float | None] | None,
    equity: float | None,
) -> float:
    """
    Compute post-trade effective leverage using realized holdings.
    """
    if positions is None or prices is None:
        return float("nan")
    if equity is None:
        return float("nan")
    try:
        eq = float(equity)
    except Exception:
        return float("nan")
    if not math.isfinite(eq) or abs(eq) < 1e-9:
        return float("nan")

    def _leg(sym: str, beta: float) -> float:
        shares = positions.get(sym) if isinstance(positions, Mapping) else None
        px = prices.get(sym) if isinstance(prices, Mapping) else None
        if shares in (None, "") or px in (None, "", 0.0):
            return 0.0
        try:
            return beta * (float(shares) * float(px) / eq)
        except Exception:
            return 0.0

    eff = _leg("QQQ", 1.0) + _leg("TQQQ", 3.0) + _leg("QQQ5", 5.0)
    fut_nom = positions.get("MNQ_nominal") if isinstance(positions, Mapping) else None
    try:
        eff += float(fut_nom) / eq if fut_nom not in (None, "") else 0.0
    except Exception:
        pass
    return eff


def replicate_levered_sleeve_via_fut(capital_frac: float, leverage: float) -> tuple[float, float]:
    """
    Convert a levered sleeve (e.g., QQQ5) into cash-equivalent ETF weights using the
    same logic as routing a futures bucket: dedicate 1x notional to QQQ and express
    the remaining leverage through TQQQ (3x). Returns (extra_qqq_frac, extra_tqqq_frac).
    """
    capital_frac = max(0.0, float(capital_frac))
    leverage = max(0.0, float(leverage))
    if capital_frac == 0.0 or leverage == 0.0:
        return 0.0, 0.0
    if leverage <= 1.0:
        return capital_frac * leverage, 0.0
    extra_qqq = capital_frac  # base sleeve contributes 1x on QQQ
    extra_tqqq = capital_frac * (leverage - 1.0) / 3.0
    return extra_qqq, extra_tqqq


def map_weights_to_exec(snapshot: StrategySnapshot, mode: str,
                        max_sum: float = 0.98,
                        qqq5_to_tqqq_scale: float = 5.0/3.0,
                        use_futures_sleeve: bool = False) -> ExecTargets:
    wf, wt, w5, L = snapshot.w_fut, snapshot.w_tqqq, snapshot.w_qqq5, snapshot.L_base
    tgt_qqq = 0.0; tgt_tqqq = 0.0; tgt_qqq5 = 0.0; mnq_nominal = 0.0
    if use_futures_sleeve:
        tgt_tqqq += max(0.0, wt)
        tgt_qqq5 += max(0.0, w5)
        mnq_nominal = wf * max(L, 0.0)
    elif mode.lower() == "mnq":
        mnq_nominal = wf * max(L, 0.0)
    else:
        if L <= 1.0:
            tgt_qqq += wf * max(L, 0.0)
        else:
            tgt_qqq += wf * 1.0
            tgt_tqqq += max(0.0, wf * (L - 1.0) / 3.0)
        tgt_tqqq += max(0.0, wt)
        tgt_qqq5 += max(0.0, w5)
    total = tgt_qqq + tgt_tqqq + tgt_qqq5
    if total > max_sum:
        scale = max_sum / max(1e-9, total)
        tgt_qqq *= scale
        tgt_tqqq *= scale
        tgt_qqq5 *= scale
        total = tgt_qqq + tgt_tqqq + tgt_qqq5
    tgt_cash = max(0.0, 1.0 - total)
    return ExecTargets(tgt_cash, tgt_qqq, tgt_tqqq, tgt_qqq5, mnq_nominal)


def combine_snapshots(snaps: List[StrategySnapshot], how: str="mean") -> StrategySnapshot:
    arr = pd.DataFrame([{
        "w_fut": s.w_fut, "w_tqqq": s.w_tqqq, "w_qqq5": s.w_qqq5, "L_base": s.L_base, "asof": s.asof
    } for s in snaps])
    agg = (arr[["w_fut","w_tqqq","w_qqq5","L_base"]].median()
           if how=="median" else arr[["w_fut","w_tqqq","w_qqq5","L_base"]].mean())
    asof = arr["asof"].max()
    return StrategySnapshot(asof=asof, w_fut=float(agg["w_fut"]),
                            w_tqqq=float(agg["w_tqqq"]), w_qqq5=float(agg["w_qqq5"]),
                            L_base=float(agg["L_base"]))


def load_keepers(keepers_csv: str, oos_csv: Optional[str], topn: int) -> pd.DataFrame:
    df = pd.read_csv(keepers_csv)
    if oos_csv and os.path.exists(oos_csv):
        oos = pd.read_csv(oos_csv)
        common = [c for c in ("config_id","key") if c in df.columns and c in oos.columns]
        if common:
            df = df.merge(oos, on=common[0], how="left", suffixes=("","_oos"))
        elif len(oos) == len(df):
            oos_cols = [c for c in oos.columns if c not in df.columns]
            df = pd.concat([df.reset_index(drop=True), oos[oos_cols].reset_index(drop=True)], axis=1)

        if "oos_flag" in df.columns:
            df = df[df["oos_flag"].astype(str).str.strip().eq("") | df["oos_flag"].isna()]
        if "OOS_DSR_long" in df.columns:
            df = df[df["OOS_DSR_long"] >= 1.0]
        if "OOS_Calmar_long" in df.columns:
            df = df[df["OOS_Calmar_long"] >= 0.65]

    sort_cols = [c for c in ("rank","pareto_rank","score","CAGR","DSR","Calmar") if c in df.columns]
    if sort_cols:
        df = df.sort_values(by=sort_cols, ascending=[True] + [False]*(len(sort_cols)-1))
    return df.head(topn).copy()


# =========================
# IBKR helpers
# =========================
def connect_ib(host: str, port: int, client_id: int) -> IB:
    try:
        ib = IB()
        ib.connect(host, port, clientId=client_id, readonly=False)
        return ib
    except Exception as exc:
        port_hints = {
            7497: "TWS paper default",
            7496: "TWS live default",
            4002: "IB Gateway paper default",
            4001: "IB Gateway live default",
        }
        port_hint = port_hints.get(int(port), "custom port")
        raise RuntimeError(
            f"IB API connection failed host={host} port={port} ({port_hint}) clientId={client_id}. "
            "Ensure TWS/IBG is running, API is enabled, and the socket port matches."
        ) from exc

def qualify_stock(ib: IB, symbol: str) -> Stock:
    stk = Stock(symbol, 'SMART', 'USD', primaryExchange='NASDAQ'); ib.qualifyContracts(stk); return stk

def qualify_qqq5(ib: IB) -> Stock:
    stk = Stock('QQQ5', 'SMART', 'USD', primaryExchange='LSEETF')
    try:
        stk.conId = 532462984
    except Exception:
        pass
    ib.qualifyContracts(stk)
    return stk

def pick_front_mnq(ib: IB) -> Future:
    fut = Future(symbol='MNQ', exchange='CME', currency='USD')
    details = ib.reqContractDetails(fut)
    if not details:
        fut = Future(symbol='NQ', exchange='CME', currency='USD')
        details = ib.reqContractDetails(fut)
    if not details: raise RuntimeError("No futures details for MNQ/NQ on CME.")
    today = pd.Timestamp.today().date(); best=None
    for d in details:
        ltd = d.contract.lastTradeDateOrContractMonth
        try:
            end_date = pd.to_datetime(ltd if len(ltd)==8 else ltd+"01").date()
        except Exception:
            continue
        if end_date >= today and (best is None or end_date < best[0]): best = (end_date, d.contract)
    if best is None: best = (today, details[0].contract)
    con = best[1]; ib.qualifyContracts(con); return con

def account_equity(ib: IB, account: Optional[str]=None) -> float:
    for _ in range(6):
        ib.sleep(0.5)
        acc_vals = ib.accountValues()
        if account:
            filtered = [x for x in acc_vals if x.tag == 'NetLiquidation' and x.currency == 'USD' and x.account == account]
        else:
            filtered = [x for x in acc_vals if x.tag == 'NetLiquidation' and x.currency == 'USD']
        if filtered:
            return float(filtered[0].value) 
    raise RuntimeError("Unable to read NetLiquidation (USD).")

def cancel_open_orders_for(ib: IB, symbols: Tuple[str, ...]):
    for tr in ib.openTrades():
        con = tr.contract
        sym = getattr(con, 'localSymbol', None) or getattr(con, 'symbol', None) or ""
        if any(sym.startswith(s) for s in symbols):
            try: ib.cancelOrder(tr.order)
            except Exception: pass
    ib.sleep(0.2)

def get_last_price_via_ib_or_yf(ib: IB, contract: Contract, fallback_symbol: str, *, sleep_sec: float = 0.6) -> float:
    """Prefer IB quote; fall back to yfinance close (no 'today' literal)."""
    try:
        tick = ib.reqMktData(contract, "", False, False)
        ib.sleep(sleep_sec)
        px = None
        if tick.last is not None and math.isfinite(tick.last):
            px = float(tick.last)
        elif tick.close is not None and math.isfinite(tick.close):
            px = float(tick.close)
        ib.cancelMktData(contract)
        if px and px > 0:
            return px
    except Exception:
        pass
    sym = '^NDX' if fallback_symbol.upper() in {'NDX', '^NDX'} else fallback_symbol
    return yf_safe_close(sym, days_back=30)

def round_shares(target_value: float, price: float, lot: int=1) -> int:
    if price<=0: return 0
    raw=int(target_value//price); raw=(raw//lot)*lot; return max(0,raw)

def place_delta_order_stock(ib: IB, contract: Stock, delta_shares: int, use_adaptive=True):
    if delta_shares==0: return None
    side='BUY' if delta_shares>0 else 'SELL'; qty=abs(int(delta_shares))
    order = MarketOrder(side, qty)
    if use_adaptive: order.algoStrategy='Adaptive'; order.algoParams=[]
    return ib.placeOrder(contract, order)


def _ib_time_fmt(ts: datetime) -> str:
    """Format timestamp for IB algo params (YYYYMMDD HH:MM:SS in ET)."""
    local = ts.astimezone(_EASTERN)
    return local.strftime("%Y%m%d %H:%M:%S")


def place_twap_order_stock(ib: IB, contract: Stock, delta_shares: int,
                           duration_minutes: float, start_delay_sec: float = 2.0):
    qty = abs(int(delta_shares))
    if qty == 0:
        return None
    duration_minutes = max(float(duration_minutes), 1.0)
    start_delay_sec = max(float(start_delay_sec), 0.0)
    side = 'BUY' if delta_shares > 0 else 'SELL'
    order = MarketOrder(side, qty)
    order.algoStrategy = 'Twap'
    now_utc = datetime.now(timezone.utc)
    start = now_utc + timedelta(seconds=start_delay_sec)
    end = start + timedelta(minutes=duration_minutes)
    order.algoParams = [
        TagValue('startTime', _ib_time_fmt(start)),
        TagValue('endTime', _ib_time_fmt(end)),
        TagValue('allowPastEndTime', '1'),
    ]
    return ib.placeOrder(contract, order)


def place_vwap_order_stock(ib: IB, contract: Stock, delta_shares: int,
                           duration_minutes: float, max_pct_vol: float,
                           start_delay_sec: float = 2.0):
    qty = abs(int(delta_shares))
    if qty == 0:
        return None
    duration_minutes = max(float(duration_minutes), 1.0)
    start_delay_sec = max(float(start_delay_sec), 0.0)
    max_pct_vol = max(0.01, float(max_pct_vol))
    side = 'BUY' if delta_shares > 0 else 'SELL'
    order = MarketOrder(side, qty)
    order.algoStrategy = 'Vwap'
    now_utc = datetime.now(timezone.utc)
    start = now_utc + timedelta(seconds=start_delay_sec)
    end = start + timedelta(minutes=duration_minutes)
    order.algoParams = [
        TagValue('startTime', _ib_time_fmt(start)),
        TagValue('endTime', _ib_time_fmt(end)),
        TagValue('maxPctVol', f"{max_pct_vol:.4f}"),
        TagValue('allowPastEndTime', '1'),
        TagValue('noCleanUp', '0'),
        TagValue('speedUp', '1'),
    ]
    return ib.placeOrder(contract, order)


def place_sliced_market_orders(ib: IB, contract: Stock, delta_shares: int,
                               slice_shares: int, pause_sec: float,
                               use_adaptive: bool = True):
    slice_shares = max(int(slice_shares), 1)
    pause_sec = max(float(pause_sec), 0.0)
    remaining = int(delta_shares)
    sign = 1 if remaining > 0 else -1
    placed = []
    while remaining != 0:
        chunk = sign * min(abs(remaining), slice_shares)
        order = place_delta_order_stock(ib, contract, chunk, use_adaptive=use_adaptive)
        placed.append(order)
        remaining -= chunk
        if remaining != 0 and pause_sec > 0:
            ib.sleep(pause_sec)
    return placed


def build_qqq5_liquidity_plan(delta_shares: int, price: float, args) -> dict:
    mode = (getattr(args, "qqq5_liquidity_exec", "twap") or "twap").lower()
    notional = abs(int(delta_shares)) * max(float(price), 0.0)
    shares = int(abs(delta_shares))
    plan: dict = {
        "mode": mode,
        "shares": shares,
        "notional": notional,
        "trigger_notional": float(getattr(args, "qqq5_liquidity_trigger", 0.0)),
    }
    if mode in ("twap", "vwap"):
        plan["duration_minutes"] = max(float(getattr(args, "qqq5_twap_minutes", 15.0)), 1.0)
        plan["start_delay_sec"] = max(float(getattr(args, "qqq5_liquidity_start_delay", 2.0)), 0.0)
        if mode == "vwap":
            plan["max_pct_vol"] = max(float(getattr(args, "qqq5_vwap_max_pct", 0.15)), 0.01)
    elif mode == "slice":
        price_safe = max(float(price), 1e-4)
        slice_notional = max(float(getattr(args, "qqq5_slice_notional", 50000.0)), 1000.0)
        slice_shares = max(1, int(slice_notional // price_safe))
        plan["slice_notional"] = slice_notional
        plan["slice_shares"] = slice_shares
        plan["pause_sec"] = max(float(getattr(args, "qqq5_slice_pause", 20.0)), 0.0)
        plan["slices"] = int(math.ceil(max(shares, 1) / slice_shares))
    return plan


def execute_qqq5_liquidity_plan(ib: IB, contract: Stock, delta_shares: int, plan: dict):
    mode = plan.get("mode", "twap")
    if mode == "slice":
        slice_shares = plan.get("slice_shares", 1)
        pause_sec = plan.get("pause_sec", 10.0)
        return place_sliced_market_orders(
            ib, contract, delta_shares, slice_shares=slice_shares,
            pause_sec=pause_sec, use_adaptive=True
        )
    if mode == "vwap":
        return [place_vwap_order_stock(
            ib, contract, delta_shares,
            duration_minutes=plan.get("duration_minutes", 15.0),
            max_pct_vol=plan.get("max_pct_vol", 0.15),
            start_delay_sec=plan.get("start_delay_sec", 2.0)
        )]
    # default TWAP
    return [place_twap_order_stock(
        ib, contract, delta_shares,
        duration_minutes=plan.get("duration_minutes", 15.0),
        start_delay_sec=plan.get("start_delay_sec", 2.0)
    )]

def place_or_adjust_mnq(ib: IB, contract: Future, target_nominal_usd: float, price_hint: Optional[float]=None):
    poz = ib.positions(); cur_contracts=0
    for p in poz:
        if p.contract.conId == contract.conId:
            cur_contracts=int(p.position); break
    price = price_hint or get_last_price_via_ib_or_yf(ib, contract, '^NDX')
    multiplier=2.0
    tgt_contracts=int(round(target_nominal_usd/max(1e-9,(price*multiplier))))
    delta=tgt_contracts-cur_contracts
    if delta==0: return None
    side='BUY' if delta>0 else 'SELL'; qty=abs(int(delta))
    order=MarketOrder(side, qty); order.tif='DAY'; order.algoStrategy='Adaptive'
    return ib.placeOrder(contract, order)


# =========================
# Execution record helpers
# =========================
EXECUTION_RECORD_COLUMNS = [
    "date", "broker", "entry_type", "missed_execution", "run_timestamp",
    "nav", "cash_balance", "pos_qqq", "pos_tqqq", "pos_qqq5", "pos_mnq_nominal",
    "order_qqq", "order_tqqq", "order_qqq5", "order_mnq_nominal",
    "orders_detail", "est_transaction_cost",
    "sim_weight_cash", "sim_weight_qqq", "sim_weight_tqqq", "sim_weight_qqq5", "sim_mnq_nominal",
    "sim_cash", "sim_notional_qqq", "sim_notional_tqqq", "sim_notional_qqq5",
    "sim_target_qqq_shares", "sim_target_tqqq_shares", "sim_target_qqq5_shares",
    "sim_nav", "sim_snapshot_base_nav",
    "effective_leverage_target", "effective_leverage_realized", "effective_leverage_diff",
    "delta_nav_vs_sim", "live_drawdown", "notes",
    "guard_min_drift_hit", "guard_max_turnover_hit", "guard_min_trade_value_hit", "guard_min_shares_hit",
]


def _ensure_parent_dir(path: str) -> None:
    if not path:
        return
    parent = Path(path).expanduser().resolve().parent
    parent.mkdir(parents=True, exist_ok=True)


def _load_execution_log(path: str) -> pd.DataFrame:
    if not path or not os.path.exists(path):
        return pd.DataFrame(columns=EXECUTION_RECORD_COLUMNS)
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=EXECUTION_RECORD_COLUMNS)
    return df


def _append_execution_rows(path: str, rows: list[dict]) -> None:
    if not rows or not path:
        return
    _ensure_parent_dir(path)
    file_exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXECUTION_RECORD_COLUMNS)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            safe_row = {col: row.get(col) for col in EXECUTION_RECORD_COLUMNS}
            writer.writerow(safe_row)


def _compute_missing_dates(last_date_str: str | float | int | None, current_date_str: str | None) -> list[date]:
    if not last_date_str or not current_date_str:
        return []
    try:
        last_date = pd.to_datetime(last_date_str).date()
        current_date = pd.to_datetime(current_date_str).date()
    except Exception:
        return []
    if current_date <= last_date:
        return []
    rng = pd.bdate_range(last_date + pd.offsets.BDay(1), current_date - pd.offsets.BDay(1))
    return [d.date() for d in rng]


def _download_symbol_series(symbol: str, start_date: date, end_date: date) -> pd.Series:
    if yf is None:
        return pd.Series(dtype=float)
    start = (start_date - timedelta(days=2)).isoformat()
    end = (end_date + timedelta(days=2)).isoformat()
    try:
        df = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False)
    except Exception:
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    series: pd.Series | None = None
    # yfinance 2.x returns MultiIndex columns (Price, Ticker); handle both cases
    if isinstance(df.columns, pd.MultiIndex):
        candidates = [('Adj Close', symbol), ('Close', symbol)]
        for key in candidates:
            if key in df.columns:
                series = df[key]
                break
        if series is None:
            for lvl in ('Adj Close', 'Close'):
                if lvl in df.columns.get_level_values(0):
                    subset = df.xs(lvl, level=0, axis=1)
                    if isinstance(subset, pd.Series):
                        series = subset
                        break
                    if isinstance(subset, pd.DataFrame):
                        if symbol in subset.columns:
                            series = subset[symbol]
                        elif subset.shape[1] == 1:
                            series = subset.iloc[:, 0]
                        if series is not None:
                            break
    else:
        col = None
        if 'Adj Close' in df.columns:
            col = 'Adj Close'
        elif 'Close' in df.columns:
            col = 'Close'
        if col:
            series = df[col]
    if series is None:
        return pd.Series(dtype=float)
    series = pd.to_numeric(series, errors='coerce').dropna()
    series.index = pd.to_datetime(series.index).normalize()
    return series


def _price_on_or_before(series: pd.Series, dt: date) -> float | None:
    if series is None or series.empty:
        return None
    ts = pd.Timestamp(dt)
    if ts in series.index:
        try:
            val = float(series.loc[ts])
            return val if math.isfinite(val) else None
        except Exception:
            return None
    pos = series.index.searchsorted(ts, side='right') - 1
    if pos >= 0:
        try:
            val = float(series.iloc[pos])
            return val if math.isfinite(val) else None
        except Exception:
            return None
    return None


def _fetch_price_lookup(symbols: list[str], missing_dates: list[date]) -> dict[date, dict[str, float | None]]:
    if not missing_dates:
        return {}
    start = min(missing_dates)
    end = max(missing_dates)
    lookup: dict[str, pd.Series] = {}
    for sym in symbols:
        lookup[sym] = _download_symbol_series(sym, start, end)
    price_map: dict[date, dict[str, float | None]] = {}
    for dt in missing_dates:
        price_map[dt] = {}
        for sym in symbols:
            price_map[dt][sym] = _price_on_or_before(lookup.get(sym, pd.Series(dtype=float)), dt)
    return price_map


def _build_missed_rows(prev_row: pd.Series, missing_dates: list[date],
                       price_lookup: dict[date, dict[str, float | None]], broker: str) -> list[dict]:
    rows: list[dict] = []
    if prev_row is None or not missing_dates:
        return rows
    pos_qqq = float(prev_row.get("pos_qqq", 0.0) or 0.0)
    pos_tqqq = float(prev_row.get("pos_tqqq", 0.0) or 0.0)
    pos_qqq5 = float(prev_row.get("pos_qqq5", 0.0) or 0.0)
    pos_mnq_nominal = float(prev_row.get("pos_mnq_nominal", 0.0) or 0.0)
    cash = float(prev_row.get("cash_balance", 0.0) or 0.0)
    sim_fields = {col: prev_row.get(col) for col in EXECUTION_RECORD_COLUMNS if col.startswith("sim_")}
    for miss_date in missing_dates:
        px = price_lookup.get(miss_date, {})
        nav_value = None
        if px:
            vals = []
            q = px.get('QQQ'); t = px.get('TQQQ'); q5 = px.get('QQQ5')
            if all(val is not None for val in (q, t, q5)):
                nav_value = cash + pos_qqq*q + pos_tqqq*t + pos_qqq5*(q5 or 0.0)
        row = {
            "date": miss_date.isoformat(),
            "broker": broker,
            "entry_type": "missed",
            "missed_execution": 1,
            "run_timestamp": pd.Timestamp.utcnow().isoformat(),
            "nav": nav_value,
            "cash_balance": cash,
            "pos_qqq": pos_qqq,
            "pos_tqqq": pos_tqqq,
            "pos_qqq5": pos_qqq5,
            "pos_mnq_nominal": pos_mnq_nominal,
            "order_qqq": 0,
            "order_tqqq": 0,
            "order_qqq5": 0,
            "order_mnq_nominal": 0.0,
            "orders_detail": "[]",
            "est_transaction_cost": 0.0,
            "notes": "missed_execution_auto",
            "guard_min_drift_hit": 0,
            "guard_max_turnover_hit": 0,
            "guard_min_trade_value_hit": 0,
            "guard_min_shares_hit": 0,
        }
        for key, val in (sim_fields or {}).items():
            row[key] = val
        if nav_value is not None and sim_fields and math.isfinite(sim_fields.get("sim_nav", float('nan'))):
            try:
                row["delta_nav_vs_sim"] = nav_value - float(sim_fields.get("sim_nav", 0.0))
            except Exception:
                row["delta_nav_vs_sim"] = None
        rows.append(row)
    return rows


def _estimate_transaction_costs(delta_map: dict[str, int], price_map: dict[str, float | None], args) -> float:
    rate = (float(getattr(args, "trade_cost_bps", 0.0)) + float(getattr(args, "slip_bps", 0.0))) / 10000.0
    if rate <= 0:
        return 0.0
    cost = 0.0
    for sym, delta in delta_map.items():
        price = price_map.get(sym)
        if price is None or price <= 0 or delta == 0:
            continue
        cost += abs(int(delta)) * price * rate
    return float(cost)


def _build_orders_detail(delta_map: dict[str, int], price_map: dict[str, float | None],
                         qqq5_liq_plan: dict | None, mnq_nominal: float) -> list[dict]:
    details = []
    for sym in ('QQQ', 'TQQQ', 'QQQ5'):
        delta = int(delta_map.get(sym, 0))
        if delta == 0:
            continue
        entry = {
            "symbol": sym,
            "delta_shares": delta,
            "price_ref": price_map.get(sym),
            "notional_est": (abs(delta) * price_map.get(sym)) if price_map.get(sym) else None,
        }
        if sym == 'QQQ5' and qqq5_liq_plan:
            entry["liq_mode"] = qqq5_liq_plan.get("mode")
        details.append(entry)
    if mnq_nominal and abs(mnq_nominal) > 0:
        details.append({"symbol": "MNQ", "delta_nominal": mnq_nominal})
    return details


def _targets_to_dict(exec_targets: ExecTargets) -> dict:
    return {
        "cash": float(exec_targets.tgt_cash_frac),
        "qqq": float(exec_targets.tgt_qqq_frac),
        "tqqq": float(exec_targets.tgt_tqqq_frac),
        "qqq5": float(getattr(exec_targets, "tgt_qqq5_frac", 0.0)),
        "mnq_nominal_of_equity": float(exec_targets.tgt_mnq_nominal),
    }


def _build_sim_snapshot(eq: float, targets_dict: dict, price_map: dict[str, float | None]) -> dict:
    weights = {
        "cash": float(targets_dict.get("cash", 0.0)),
        "qqq": float(targets_dict.get("qqq", 0.0)),
        "tqqq": float(targets_dict.get("tqqq", 0.0)),
        "qqq5": float(targets_dict.get("qqq5", 0.0)),
    }
    mnq_nominal = float(targets_dict.get("mnq_nominal_of_equity", 0.0)) * float(eq)
    notionals = {k: eq * v for k, v in weights.items() if k != "cash"}
    sim_cash = eq * weights["cash"]
    sim_nav = sim_cash + notionals.get("qqq", 0.0) + notionals.get("tqqq", 0.0) + notionals.get("qqq5", 0.0)
    sim_shares = {}
    for key, sym in (("qqq", "QQQ"), ("tqqq", "TQQQ"), ("qqq5", "QQQ5")):
        price = price_map.get(sym)
        notional = notionals.get(key, 0.0)
        sim_shares[sym] = (notional / price) if price and price > 0 else None
    return {
        "weights": weights,
        "notionals": notionals,
        "shares": sim_shares,
        "cash": sim_cash,
        "nav": sim_nav,
        "mnq_nominal": mnq_nominal,
        "base_nav": eq,
        "sim_weights_overrides": weights,
        "sim_notionals_overrides": notionals,
        "sim_target_shares_overrides": sim_shares,
    }


def _build_actual_execution_row(summary: dict) -> dict:
    prices = summary.get("prices", {})
    post_positions = summary.get("post_positions", {})
    delta_shares = summary.get("delta_shares", {})
    orders_detail = json.dumps(summary.get("orders_detail", []))
    sim_snapshot = summary.get("sim_snapshot", {}) or {}
    weights = sim_snapshot.get("weights", {})
    notionals = sim_snapshot.get("notionals", {})
    shares = sim_snapshot.get("shares", {})
    row = {
        "date": summary.get("asof"),
        "broker": summary.get("broker"),
        "entry_type": "dry" if summary.get("dry") else "executed",
        "missed_execution": 0,
        "run_timestamp": pd.Timestamp.utcnow().isoformat(),
        "nav": summary.get("equity_usd"),
        "cash_balance": summary.get("cash_balance"),
        "pos_qqq": post_positions.get("QQQ"),
        "pos_tqqq": post_positions.get("TQQQ"),
        "pos_qqq5": post_positions.get("QQQ5"),
        "pos_mnq_nominal": post_positions.get("MNQ_nominal"),
        "order_qqq": delta_shares.get("QQQ"),
        "order_tqqq": delta_shares.get("TQQQ"),
        "order_qqq5": delta_shares.get("QQQ5"),
        "order_mnq_nominal": summary.get("order_mnq_nominal"),
        "orders_detail": orders_detail,
        "est_transaction_cost": summary.get("transaction_cost_est"),
        "sim_weight_cash": weights.get("cash"),
        "sim_weight_qqq": weights.get("qqq"),
        "sim_weight_tqqq": weights.get("tqqq"),
        "sim_weight_qqq5": weights.get("qqq5"),
        "sim_mnq_nominal": sim_snapshot.get("mnq_nominal"),
        "sim_cash": sim_snapshot.get("cash"),
        "sim_notional_qqq": notionals.get("qqq"),
        "sim_notional_tqqq": notionals.get("tqqq"),
        "sim_notional_qqq5": notionals.get("qqq5"),
        "sim_target_qqq_shares": shares.get("QQQ"),
        "sim_target_tqqq_shares": shares.get("TQQQ"),
        "sim_target_qqq5_shares": shares.get("QQQ5"),
        "sim_nav": sim_snapshot.get("nav"),
        "sim_snapshot_base_nav": sim_snapshot.get("base_nav"),
        "effective_leverage_target": summary.get("effective_leverage"),
        "effective_leverage_realized": summary.get("effective_leverage_realized"),
        "effective_leverage_diff": summary.get("effective_leverage_diff"),
        "live_drawdown": summary.get("live_drawdown"),
    }
    nav = row.get("nav")
    sim_nav = row.get("sim_nav")
    if isinstance(nav, (int, float)) and isinstance(sim_nav, (int, float)):
        row["delta_nav_vs_sim"] = float(nav) - float(sim_nav)
    notes = []
    if summary.get("dry"):
        notes.append("dry_run")
    note_extra = summary.get("entry_notes")
    if isinstance(note_extra, str) and note_extra:
        notes.append(note_extra)
    elif isinstance(note_extra, list):
        notes.extend([n for n in note_extra if n])
    row["notes"] = ";".join(notes) if notes else None
    row["guard_min_drift_hit"] = int(summary.get("guard_min_drift_hit", 0) or 0)
    row["guard_max_turnover_hit"] = int(summary.get("guard_max_turnover_hit", 0) or 0)
    row["guard_min_trade_value_hit"] = int(summary.get("guard_min_trade_value_hit", 0) or 0)
    row["guard_min_shares_hit"] = int(summary.get("guard_min_shares_hit", 0) or 0)
    return row


def _reprice_nav(row: dict, price_map: Mapping[str, float | None], include_mnq: bool) -> float | None:
    """
    Recompute NAV using provided prices (e.g., official closes) to align with backtest.
    """
    try:
        cash = float(row.get("cash_balance", 0.0) or 0.0)
    except Exception:
        cash = 0.0
    nav = cash
    for sym_key, price_key in (("pos_qqq", "QQQ"), ("pos_tqqq", "TQQQ"), ("pos_qqq5", "QQQ5")):
        sh = row.get(sym_key)
        px = price_map.get(price_key) if isinstance(price_map, Mapping) else None
        if sh is None or px is None or not math.isfinite(px):
            continue
        try:
            nav += float(sh) * float(px)
        except Exception:
            continue
    if include_mnq:
        mnq = row.get("pos_mnq_nominal")
        if isinstance(mnq, (int, float)) and math.isfinite(mnq):
            nav += float(mnq)
    return nav if math.isfinite(nav) else None


def _record_execution_day(args, summary: dict) -> None:
    path = getattr(args, "execution_record", None)
    if not path:
        return
    try:
        df = _load_execution_log(path)
        broker = summary.get("broker")
        prev_row = None
        if not df.empty and broker is not None:
            subset = df[df["broker"] == broker]
            if not subset.empty:
                prev_row = subset.iloc[-1]
        missing_rows: list[dict] = []
        if prev_row is not None:
            missing_dates = _compute_missing_dates(prev_row.get("date"), summary.get("asof"))
            if missing_dates:
                price_lookup = _fetch_price_lookup(['QQQ', 'TQQQ', 'QQQ5'], missing_dates)
                missing_rows = _build_missed_rows(prev_row, missing_dates, price_lookup, broker)
        actual_row = _build_actual_execution_row(summary)
        # Optional NAV repricing to align with backtest
        if getattr(args, "nav_price_source", "live") != "live":
            dt = pd.to_datetime(actual_row.get("date"), errors="coerce")
            if pd.notna(dt):
                px_override = _fetch_prices_for_date(dt.date(), ["QQQ", "TQQQ", "QQQ5", "QQQ5.L"])
                # Normalize any QQQ5.L into QQQ5 key
                if px_override.get("QQQ5") is None and px_override.get("QQQ5.L") is not None:
                    px_override["QQQ5"] = px_override.get("QQQ5.L")
                nav_reprice = _reprice_nav(actual_row, px_override, include_mnq=getattr(args, "nav_include_mnq", False))
                if nav_reprice is not None:
                    actual_row["nav_close"] = nav_reprice
                    actual_row["nav_price_source"] = getattr(args, "nav_price_source", "live")
                    actual_row["nav"] = nav_reprice
        rows = missing_rows + [actual_row]
        _append_execution_rows(path, rows)
    except Exception as exc:
        print(f"[warn:record] Failed to update execution record: {exc}")


def _load_broker_nav_series(path: str, broker: str | None) -> pd.Series | None:
    if not path or broker is None:
        return None
    df = _load_execution_log(path)
    if df.empty or "date" not in df.columns or "nav" not in df.columns or "broker" not in df.columns:
        return None
    try:
        dates = pd.to_datetime(df["date"], errors="coerce")
    except Exception:
        return None
    navs = pd.to_numeric(df["nav"], errors="coerce")
    mask = (df["broker"] == broker) & dates.notna() & navs.notna()
    if not mask.any():
        return None
    series = pd.Series(navs[mask].astype(float).values, index=dates[mask]).sort_index()
    return series if not series.empty else None


def _nearest_value(series: pd.Series, target: pd.Timestamp) -> tuple[pd.Timestamp | None, float | None]:
    if series is None or series.empty:
        return None, None
    series = series.sort_index()
    prior = series[series.index <= target]
    if not prior.empty:
        idx = prior.index[-1]
        val = prior.iloc[-1]
        return idx, float(val)
    after = series[series.index >= target]
    if not after.empty:
        idx = after.index[0]
        val = after.iloc[0]
        return idx, float(val)
    return None, None


def _calc_period_metrics(series: pd.Series, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> dict | None:
    if series is None or series.empty:
        return None
    series = series.sort_index()
    if end_dt <= series.index.min() or (end_dt - start_dt).days < 5:
        return None
    start_idx, start_val = _nearest_value(series, start_dt)
    end_idx, end_val = _nearest_value(series, end_dt)
    if start_idx is None or end_idx is None or start_val is None or end_val is None:
        return None
    if start_val <= 0 or end_val <= 0 or end_idx <= start_idx:
        return None
    years = max((end_idx - start_idx).days / 365.25, 1.0 / 365.25)
    total_return = float(end_val / start_val - 1.0)
    cagr = float((end_val / start_val) ** (1 / years) - 1.0) if years > 0 else None
    subset = series[(series.index >= start_idx) & (series.index <= end_idx)]
    rets = subset.pct_change().dropna()
    sharpe = None
    if len(rets) >= 5:
        std = rets.std(ddof=0)
        if std > 0:
            sharpe = float(rets.mean() / std * np.sqrt(252.0))
    return {
        "start": start_idx,
        "end": end_idx,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
    }


def _compute_performance_snapshot(path: str | None, broker: str | None,
                                  asof_str: str | None, current_nav: float | None) -> dict:
    if broker is None or current_nav is None or not isinstance(current_nav, (int, float)):
        return {}
    try:
        asof_ts = pd.to_datetime(asof_str)
    except Exception:
        return {}
    series = _load_broker_nav_series(path, broker)
    if series is None:
        series = pd.Series(dtype=float)
    series = series.copy()
    series.loc[asof_ts] = float(current_nav)
    series = series.sort_index()
    snapshot: dict[str, float] = {}
    start_of_year = pd.Timestamp(year=asof_ts.year, month=1, day=1, tz=asof_ts.tz)
    ytd_metrics = _calc_period_metrics(series, start_of_year, asof_ts)
    if ytd_metrics is not None:
        snapshot["ytd_return"] = ytd_metrics["total_return"]
    for yrs in (1, 3, 5, 10):
        start_dt = asof_ts - pd.DateOffset(years=yrs)
        metrics = _calc_period_metrics(series, start_dt, asof_ts)
        if metrics is None:
            continue
        snapshot[f"cagr_{yrs}y"] = metrics.get("cagr")
        snapshot[f"sharpe_{yrs}y"] = metrics.get("sharpe")
    return snapshot


# =========================
# Moomoo OpenD helpers (US ETFs via Futu OpenAPI)
# =========================
class MoomooCtx:
    def __init__(self, quote_ctx: 'OpenQuoteContext', trd_ctx: 'OpenUSTradeContext'):
        self.quote_ctx = quote_ctx
        self.trd_ctx = trd_ctx

    def close(self):
        try:
            self.quote_ctx.close()
        except Exception:
            pass
        try:
            self.trd_ctx.close()
        except Exception:
            pass


def _mm_env_str_to_enum(s: str):
    s = (s or "SIMULATE").upper()
    return TrdEnv.SIMULATE if s.startswith("SIM") else TrdEnv.REAL


def connect_moomoo(host: str, port: int, trd_env_str: str, acc_id: int | None, trade_pwd: str | None) -> MoomooCtx:
    if not _FUTU_AVAILABLE:
        raise RuntimeError("futu-api not installed. Run: pip install futu-api")
    quote = OpenQuoteContext(host, port)
    trd_env = _mm_env_str_to_enum(trd_env_str)
    trade = OpenUSTradeContext(host, port, trd_env=trd_env, acc_id=acc_id)
    if trade_pwd:
        ret, _ = trade.unlock_trade(trade_pwd)
        if ret != RET_OK:
            quote.close(); trade.close()
            raise RuntimeError("Failed to unlock Moomoo trading (check MOOMOO_TRADE_PWD).")
    return MoomooCtx(quote, trade)


def mm_account_equity(ctx: MoomooCtx) -> float:
    ret, df = ctx.trd_ctx.accinfo_query()
    if ret != RET_OK or df is None or df.empty:
        raise RuntimeError("Moomoo accinfo_query failed.")
    # Prefer 'total_assets' if present; fall back to 'power'
    for col in ('total_assets', 'power'):
        if col in df.columns:
            try:
                v = float(df[col].iloc[0])
                if math.isfinite(v):
                    return v
            except Exception:
                pass
    raise RuntimeError("Could not determine account equity from Moomoo accinfo.")


def mm_get_positions(ctx: MoomooCtx) -> dict[str, int]:
    ret, df = ctx.trd_ctx.position_list_query()
    pos: dict[str, int] = {}
    if ret != RET_OK or df is None or df.empty:
        return pos
    for _, row in df.iterrows():
        code = str(row.get('code', '')).upper()
        qty = int(row.get('qty', 0))
        if not code or qty == 0:
            continue
        pos[code] = pos.get(code, 0) + qty
    return pos


def mm_cancel_open_orders(ctx: MoomooCtx, codes: tuple[str, ...] = ('QQQ.US','TQQQ.US')):
    ret, df = ctx.trd_ctx.order_list_query()
    if ret != RET_OK or df is None or df.empty:
        return
    for _, row in df.iterrows():
        code = str(row.get('code',''))
        order_id = row.get('order_id')
        status = str(row.get('order_status','')).upper()  # e.g., SUBMITTED, FILLED_PART, PENDING_CANCEL
        if code in codes and any(tag in status for tag in ('SUBMIT', 'PENDING', 'PART')):
            try:
                ctx.trd_ctx.order_cancel(str(order_id))
            except Exception:
                pass


def mm_last_price(ctx: MoomooCtx, code: str) -> float:
    # Try snapshot; fallback to quote
    ret, df = ctx.quote_ctx.get_market_snapshot([code])
    if ret == RET_OK and df is not None and not df.empty:
        try:
            v = float(df['last_price'].iloc[0])
            if math.isfinite(v) and v > 0:
                return v
        except Exception:
            pass
    ret, df = ctx.quote_ctx.get_stock_quote([code])
    if ret == RET_OK and df is not None and not df.empty:
        try:
            v = float(df['last_price'].iloc[0])
            if math.isfinite(v) and v > 0:
                return v
        except Exception:
            pass
    # yfinance fallback by mapping code -> ticker
    ticker = 'QQQ' if code.upper().startswith('QQQ') else 'TQQQ'
    return yf_safe_close(ticker, days_back=30)


def mm_round_shares(target_value: float, price: float, lot: int = 1) -> int:
    if price <= 0:
        return 0
    raw = int(target_value // price)
    raw = (raw // lot) * lot
    return max(0, raw)


def mm_place_delta_market(ctx: MoomooCtx, code: str, delta_shares: int):
    if delta_shares == 0:
        return None
    side = TrdSide.BUY if delta_shares > 0 else TrdSide.SELL
    qty = abs(int(delta_shares))
    # MARKET order; price ignored
    ret, data = ctx.trd_ctx.place_order(
        price=0.0, qty=qty, code=code, trd_side=side, order_type=OrderType.MARKET
    )
    if ret != RET_OK:
        raise RuntimeError(f"Moomoo place_order failed for {code} ({side})")
    return data


# =========================
# Notifications & logging
# =========================
def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    if not _rq or not bot_token or not chat_id:
        return
    try:
        _rq.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def load_dd_alert_state(path: str) -> dict:
    state = {"hwm_nav": None, "min_dd": 0.0, "last_alert_date": None}
    if not path:
        return state
    p = Path(path)
    if not p.exists():
        return state
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state.update({k: loaded.get(k) for k in state.keys()})
    except Exception:
        return state
    return state


def save_dd_alert_state(path: str, state: dict | None) -> None:
    if not path or not state:
        return
    _ensure_parent_dir(path)
    payload = {
        "hwm_nav": state.get("hwm_nav"),
        "min_dd": state.get("min_dd"),
        "last_alert_date": state.get("last_alert_date"),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def update_dd_alert_state(state: dict | None,
                          nav_live: float | None,
                          asof_str: str,
                          threshold: float,
                          bot_token: str | None,
                          chat_id: str | None) -> float | None:
    if state is None:
        return None
    try:
        nav = float(nav_live)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(nav) or nav <= 0:
        return None
    try:
        today = pd.to_datetime(asof_str).date()
    except Exception:
        today = date.today()
    hwm = state.get("hwm_nav")
    if not isinstance(hwm, (int, float)) or not math.isfinite(hwm) or hwm <= 0:
        hwm = nav
    hwm = max(hwm, nav)
    drawdown = nav / max(hwm, 1e-9) - 1.0
    min_dd = state.get("min_dd")
    if not isinstance(min_dd, (int, float)):
        min_dd = 0.0
    min_dd = min(min_dd, drawdown)
    last_alert_date = state.get("last_alert_date")
    thr = float(threshold or 0.0)
    if thr > 0 and drawdown <= -thr:
        last_dt = None
        if isinstance(last_alert_date, str) and last_alert_date:
            try:
                last_dt = pd.to_datetime(last_alert_date).date()
            except Exception:
                last_dt = None
        if last_dt is None or last_dt < today:
            send_telegram_message(
                bot_token or "",
                chat_id or "",
                f"[DD alert] Live NAV={nav:.2f}, HWM={hwm:.2f}, DD={drawdown*100:.1f}%",
            )
            last_alert_date = today.isoformat()
    state["hwm_nav"] = hwm
    state["min_dd"] = min_dd
    state["last_alert_date"] = last_alert_date
    return drawdown


def is_in_close_window(
    now_utc: pd.Timestamp,
    regular_start_et: str = "15:30",
    regular_end_et: str = "18:00",
    early_close_tail_minutes: int = 90,
) -> tuple[bool, str]:
    now_utc = pd.Timestamp(now_utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.tz_localize("UTC")
    else:
        now_utc = now_utc.tz_convert("UTC")
    now_et = now_utc.tz_convert(_EASTERN)
    if _XNYS_CAL is None:
        start = pd.Timestamp(f"{now_et.date()} {regular_start_et}", tz=_EASTERN)
        end = pd.Timestamp(f"{now_et.date()} {regular_end_et}", tz=_EASTERN)
        return (start <= now_et <= end), f"fallback start={start} end={end} now={now_et}"
    sched = _XNYS_CAL.schedule(start_date=now_et.date(), end_date=now_et.date())
    if sched.empty:
        return False, f"holiday {now_et.date()}"
    open_ts = sched.iloc[0]["market_open"].tz_convert(_EASTERN)
    close_ts = sched.iloc[0]["market_close"].tz_convert(_EASTERN)
    reg_start = pd.Timestamp(f"{now_et.date()} {regular_start_et}", tz=_EASTERN)
    reg_end = pd.Timestamp(f"{now_et.date()} {regular_end_et}", tz=_EASTERN)
    if close_ts <= reg_end:
        start = max(reg_start, close_ts - pd.Timedelta(minutes=early_close_tail_minutes))
        end = close_ts
        mode = "early_close"
    else:
        start, end, mode = reg_start, reg_end, "regular"
    if end < start:
        end = start
    return (start <= now_et <= end), f"mode={mode} start={start} end={end} now={now_et}"


def compute_turnover_value(px_qqq: float, px_tqqq: float, d_qqq: int, d_tqqq: int,
                           px_qqq5: float | None = None, d_qqq5: int = 0) -> float:
    total = abs(d_qqq) * float(px_qqq) + abs(d_tqqq) * float(px_tqqq)
    if px_qqq5 is not None:
        total += abs(d_qqq5) * float(px_qqq5)
    return total


def committee_fingerprint(config_rows: Iterable[Tuple[float, float, float, int]]) -> str:
    tuples = [tuple(row) for row in config_rows]
    raw = json.dumps(tuples, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def idempotency_should_skip(asof: str, broker: str, fingerprint: str, state_dir: str | None) -> tuple[bool, Optional[pathlib.Path]]:
    if not state_dir or not str(state_dir).strip():
        return False, None
    dir_path = pathlib.Path(state_dir).expanduser()
    dir_path.mkdir(parents=True, exist_ok=True)
    state_file = dir_path / f"{asof}_{broker}_{fingerprint}.json"
    return state_file.exists(), state_file


def idempotency_mark(lock_path: Optional[pathlib.Path]) -> None:
    if lock_path is None:
        return
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"locked": True, "ts": time.time()}))
    except Exception:
        pass


def append_jsonl(path: str, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def should_debounce_notify(state_dir: str | None, key: str, cooldown_sec: int = 600) -> bool:
    if not state_dir:
        return False
    state_path = Path(state_dir) / "notify_cooldown.json"
    db = {}
    if state_path.exists():
        try:
            db = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            db = {}
    now = int(time.time())
    last = int(db.get(key, 0))
    if now - last < cooldown_sec:
        return True
    db[key] = now
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(db), encoding="utf-8")
    except Exception:
        pass
    return False


def _norm(vec: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    s = float(np.nansum(vec))
    if not np.isfinite(s) or s <= eps:
        return np.ones_like(vec) / max(len(vec), 1)
    return vec / s


def compute_committee_weights(rows: list[dict], mode: str = "mean", clamp: float | None = None) -> np.ndarray:
    sleeves = ["w_fut", "w_tqqq", "w_qqq5"]
    if not rows:
        return np.array([1.0, 0.0, 0.0])
    M = np.array([[float(r.get(k, 0.0)) for k in sleeves] for r in rows], dtype=float)
    mode = (mode or "mean").lower()
    if mode == "off" or len(rows) == 1:
        w = M[0]
    elif mode == "mean":
        w = np.nanmean(M, axis=0)
    elif mode == "median":
        w = np.nanmedian(M, axis=0)
    elif mode == "voltarget":
        vols = np.array([float(r.get("Vol", np.nan)) for r in rows], dtype=float)
        inv = np.reciprocal(vols, where=np.isfinite(vols))
        inv = _norm(inv)
        w = inv @ M
    elif mode == "dsr":
        dsr = np.array([float(r.get("DSR", np.nan)) for r in rows], dtype=float)
        w = _norm(dsr) @ M
    else:
        raise ValueError(f"Unknown committee mode: {mode}")
    if clamp and clamp > 0 and len(rows) > 1:
        med = np.nanmedian(M, axis=0)
        lo = med * (1.0 - clamp)
        hi = med * (1.0 + clamp)
        w = np.clip(w, lo, hi)
    return _norm(w)


def oos_prune(rows: list[dict], drop_if_flagged: bool = True,
              degrade_keys: tuple[str, ...] = ("OOS_DSR_long", "OOS_Calmar_long"),
              thresholds: tuple[float, ...] = (0.75, 0.5)) -> list[dict]:
    kept = []
    for r in rows:
        if drop_if_flagged and str(r.get("oos_flag", "")).strip().lower() in {"1","true","yes","y"}:
            continue
        ok = True
        for key, thresh in zip(degrade_keys, thresholds):
            v = r.get(key)
            if v is None:
                continue
            try:
                if float(v) < float(thresh):
                    ok = False
                    break
            except Exception:
                continue
        if ok:
            kept.append(r)
    return kept


def format_telegram_message(summary: dict) -> str:
    lines: List[str] = []
    header = f"ib_paper_trader :: {summary.get('asof')} [{summary.get('broker','?').upper()}]"
    if summary.get("dry"):
        header += " [DRY]"
    lines.append(header)

    committee = summary.get("committee", {})
    lines.append(
        f"Mode={summary.get('exec_mode')} Committee={committee.get('mode')} "
        f"members={committee.get('members')}"
    )

    weights = summary.get("weights", {})
    lines.append(
        "Weights: fut={:.3f} tqqq={:.3f} qqq5={:.3f} L={:.2f}".format(
            float(weights.get("w_fut", float('nan'))),
            float(weights.get("w_tqqq", float('nan'))),
            float(weights.get("w_qqq5", float('nan'))),
            float(weights.get("L_base", float('nan'))),
        )
    )

    targets = summary.get("targets", {})
    lines.append(
        "Targets: cash={:.3f} qqq={:.3f} tqqq={:.3f}".format(
            float(targets.get("cash", float('nan'))),
            float(targets.get("qqq", float('nan'))),
            float(targets.get("tqqq", float('nan'))),
        )
    )

    nav = summary.get("equity_usd")
    sim_snapshot = summary.get("sim_snapshot", {})
    sim_nav = sim_snapshot.get("nav") if isinstance(sim_snapshot, dict) else None
    perf_snapshot = summary.get("perf_snapshot") or {}
    lines.append("—— Portfolio ——")
    if isinstance(nav, (int, float)):
        nav_line = f"• NAV: ${nav:,.0f}"
        ytd_ret = perf_snapshot.get("ytd_return")
        if isinstance(ytd_ret, (int, float)):
            nav_line += f" | YTD {ytd_ret:,.2%}"
        lines.append(nav_line)
    perf_lines = []
    for yrs in (1, 3, 5, 10):
        cagr = perf_snapshot.get(f"cagr_{yrs}y")
        if cagr is None:
            continue
        entry = f"{yrs}y {cagr:,.2%}"
        sharpe = perf_snapshot.get(f"sharpe_{yrs}y")
        if isinstance(sharpe, (int, float)):
            entry += f" (S {sharpe:.2f})"
        perf_lines.append(entry)
    if perf_lines:
        lines.append("• Perf: " + " | ".join(perf_lines))
    if isinstance(nav, (int, float)) and isinstance(sim_nav, (int, float)):
        lines.append(f"• Sim NAV: ${sim_nav:,.0f} (Δ {nav - sim_nav:,.0f})")

    lines.append("—— Execution ——")
    turnover_value = summary.get("turnover_value")
    turnover_frac = summary.get("turnover_fraction")
    if turnover_value is not None and turnover_frac is not None:
        lines.append(f"• Turnover: ${turnover_value:,.0f} ({turnover_frac:.2%})")

    orders = summary.get("orders", {})
    if orders:
        dq = int(orders.get("QQQ_delta_shares", 0))
        dt = int(orders.get("TQQQ_delta_shares", 0))
        dq5 = int(orders.get("QQQ5_delta_shares", 0))
        order_line = f"• Orders: ΔQQQ={dq:+} ΔTQQQ={dt:+}"
        if dq5:
            order_line += f" ΔQQQ5={dq5:+}"
        lines.append(order_line)

    if summary.get("skip_reason"):
        lines.append(f"Skip: {summary['skip_reason']}")

    return "\n".join(lines)


def format_console_summary(summary: dict) -> str:
    def pct(val):
        return f"{val:.2%}" if isinstance(val, (int, float)) else "n/a"

    def money(val):
        return f"${val:,.0f}" if isinstance(val, (int, float)) else "n/a"

    def beta_fmt(val):
        return f"{float(val):.2f}x" if isinstance(val, (int, float)) and math.isfinite(float(val)) else "n/a"

    lines: list[str] = []
    broker = summary.get("broker", "").upper()
    lines.append(f"=== Rebalance Summary [{broker or '?'}] ===")
    status = summary.get("status", "submitted").upper()
    header = (
        f"As-of {summary.get('asof')} | Status {status}"
        f" | Mode {summary.get('exec_mode')} | Committee {summary.get('committee', {}).get('mode')}"
    )
    lines.append(header)

    nav = summary.get("equity_usd")
    turnover_value = summary.get("turnover_value")
    turnover_frac = summary.get("turnover_fraction")
    nav_line = f"NAV {money(nav)}"
    if turnover_value is not None and turnover_frac is not None:
        nav_line += f" | Turnover {money(turnover_value)} ({pct(turnover_frac)})"
    lines.append(nav_line)

    weights = summary.get("weights", {})
    lines.append(
        "Weights  fut={:.3f}  tqqq={:.3f}  qqq5={:.3f}  L={:.2f}".format(
            float(weights.get("w_fut", float('nan'))),
            float(weights.get("w_tqqq", float('nan'))),
            float(weights.get("w_qqq5", float('nan'))),
            float(weights.get("L_base", float('nan'))),
        )
    )
    targets = summary.get("targets", {})
    lines.append(
        "Targets  cash={:.3f}  qqq={:.3f}  tqqq={:.3f}  qqq5={:.3f}".format(
            float(targets.get("cash", float('nan'))),
            float(targets.get("qqq", float('nan'))),
            float(targets.get("tqqq", float('nan'))),
            float(targets.get("qqq5", float('nan'))),
        )
    )
    eff_target = summary.get("effective_leverage")
    eff_realized = summary.get("effective_leverage_realized")
    eff_diff = summary.get("effective_leverage_diff")
    if eff_target is not None or eff_realized is not None:
        eff_line = f"Effective β  target={beta_fmt(eff_target)}"
        if eff_realized is not None:
            eff_line += f"  actual={beta_fmt(eff_realized)}"
        if isinstance(eff_diff, (int, float)) and math.isfinite(float(eff_diff)):
            eff_line += f"  (Δ {float(eff_diff):+.2f}x)"
        lines.append(eff_line)
    exposures = summary.get("exposure_totals")
    if isinstance(exposures, dict) and exposures:
        lines.append(
            "Exposure USD  ETF {cur}->{tgt}  FUT {fcur}->{ftgt}  NetΔ {net}".format(
                cur=money(exposures.get("etf_current")),
                tgt=money(exposures.get("etf_target")),
                fcur=money(exposures.get("fut_current")),
                ftgt=money(exposures.get("fut_target")),
                net=money(exposures.get("combined_delta")),
            )
        )

    post_positions = summary.get("post_positions") or summary.get("current_shares") or {}
    pos_line = "Positions"
    for sym in ("QQQ", "TQQQ", "QQQ5"):
        val = post_positions.get(sym)
        if val is not None:
            pos_line += f" {sym}={int(val)}"
    if post_positions.get("MNQ_nominal"):
        pos_line += f" MNQ_nom≈{money(post_positions['MNQ_nominal'])}"
    lines.append(pos_line)

    orders = summary.get("orders", {})
    if orders:
        dq = int(orders.get("QQQ_delta_shares", 0))
        dt = int(orders.get("TQQQ_delta_shares", 0))
        dq5 = int(orders.get("QQQ5_delta_shares", 0))
        ord_line = f"Orders  ΔQQQ={dq:+}  ΔTQQQ={dt:+}"
        if dq5:
            ord_line += f"  ΔQQQ5={dq5:+}"
        lines.append(ord_line)

    perf_snapshot = summary.get("perf_snapshot") or {}
    perf_line = []
    ytd_ret = perf_snapshot.get("ytd_return")
    if isinstance(ytd_ret, (int, float)):
        perf_line.append(f"YTD {pct(ytd_ret)}")
    for yrs in (1, 3, 5, 10):
        cagr = perf_snapshot.get(f"cagr_{yrs}y")
        if cagr is None:
            continue
        entry = f"{yrs}y {pct(cagr)}"
        sharpe = perf_snapshot.get(f"sharpe_{yrs}y")
        if isinstance(sharpe, (int, float)):
            entry += f" (S {sharpe:.2f})"
        perf_line.append(entry)
    if perf_line:
        lines.append("Performance  " + " | ".join(perf_line))

    sim_snapshot = summary.get("sim_snapshot", {})
    if sim_snapshot:
        sim_nav = sim_snapshot.get("nav")
        if isinstance(nav, (int, float)) and isinstance(sim_nav, (int, float)):
            lines.append(
                f"Simulation  NAV {money(sim_nav)}  ΔLive {money(nav - sim_nav)}"
            )

    if summary.get("qqq5_liquidity_plan"):
        plan = summary["qqq5_liquidity_plan"]
        lines.append(
            f"QQQ5 Liquidity  mode={plan.get('mode')} "
            f"notional≈{money(plan.get('notional'))}"
        )

    if summary.get("skip_reason"):
        lines.append(f"Skip reason: {summary['skip_reason']}")
    if summary.get("stray_liquidations"):
        lines.append(f"Stray liquidations: {summary['stray_liquidations']}")

    return "\n".join(lines)


# =========================
# Execution helpers (per-broker)
# =========================
def build_summary_dict(
    *, broker: str, asof_str: str, dry: bool, exec_mode: str,
    committee_info: dict, fp: str, picked: list, committee_rows: list,
    fused: StrategySnapshot, targets: ExecTargets,
    eq: float, px_qqq: float, px_tqqq: float,
    cur: dict, tgt_sh_qqq: int, tgt_sh_tqqq: int,
    d_qqq: int, d_tqqq: int, delta_value: float,
    px_qqq5: float | None = None, tgt_sh_qqq5: int | None = None, d_qqq5: int | None = None,
) -> dict:
    turnover_fraction = (delta_value / eq) if eq else None
    def _sanitize_row(row: dict) -> dict:
        cleaned = {}
        for key, val in row.items():
            if isinstance(val, float) and not math.isfinite(val):
                cleaned[key] = None
            else:
                cleaned[key] = val
        return cleaned
    sanitized_inputs = [_sanitize_row(row) for row in committee_rows]
    return {
        "asof": asof_str,
        "broker": broker.lower(),
        "status": "dry" if dry else "submitted",
        "dry": bool(dry),
        "exec_mode": exec_mode.lower(),
        "committee_fp": fp,
        "committee": committee_info,
        "picked_configs": picked,
        "committee_inputs": sanitized_inputs,
        "weights": {
            "w_fut": fused.w_fut,
            "w_tqqq": fused.w_tqqq,
            "w_qqq5": fused.w_qqq5,
            "L_base": fused.L_base,
        },
        "targets": {
            "cash": targets.tgt_cash_frac,
            "qqq": targets.tgt_qqq_frac,
            "tqqq": targets.tgt_tqqq_frac,
            "qqq5": getattr(targets, "tgt_qqq5_frac", 0.0),
            "mnq_nominal_of_equity": targets.tgt_mnq_nominal,
        },
        "prices": {"QQQ": px_qqq, "TQQQ": px_tqqq, "QQQ5": px_qqq5},
        "equity_usd": eq,
        "current_shares": {
            "QQQ": int(cur.get('QQQ',0)),
            "TQQQ": int(cur.get('TQQQ',0)),
            "QQQ5": int(cur.get('QQQ5',0))
        },
        "target_shares": {
            "QQQ": int(tgt_sh_qqq),
            "TQQQ": int(tgt_sh_tqqq),
            "QQQ5": int(tgt_sh_qqq5 or 0)
        },
        "delta_shares": {
            "QQQ": int(d_qqq),
            "TQQQ": int(d_tqqq),
            "QQQ5": int(d_qqq5 or 0)
        },
        "orders": {
            "QQQ_delta_shares": int(d_qqq),
            "TQQQ_delta_shares": int(d_tqqq),
            "QQQ5_delta_shares": int(d_qqq5 or 0)
        },
        "delta_value": float(delta_value),
        "turnover_value": float(delta_value),
        "turnover_fraction": turnover_fraction,
        "effective_leverage": compute_effective_leverage(targets),
    }


def execute_on_ib(args, fused: StrategySnapshot, targets: ExecTargets,
                  fp: str, committee_info: dict, picked: list, committee_rows: list, asof_str: str) -> dict:
    broker = "ib"
    base_targets_dict = _targets_to_dict(targets)
    # idempotency (per broker)
    skip, lock_path = idempotency_should_skip(asof_str, broker, fp, args.idempotency_dir)
    if skip:
        out = {"asof": asof_str, "broker": broker, "dry": bool(args.dry), "skip_reason": "idempotent_lock"}
        print(f"[idempotent:{broker}] Already executed for asof={asof_str}, fp={fp}. Lock: {lock_path}")
        return out

    try:
        ib = connect_ib(args.ib_host, args.ib_port, args.ib_client_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[error:{broker}] {msg}")
        out = {
            "asof": asof_str,
            "broker": broker,
            "status": "connect_failed",
            "dry": bool(args.dry),
            "exec_mode": args.exec_mode.lower(),
            "committee_fp": fp,
            "committee": committee_info,
            "picked_configs": picked,
            "weights": {
                "w_fut": fused.w_fut,
                "w_tqqq": fused.w_tqqq,
                "w_qqq5": fused.w_qqq5,
                "L_base": fused.L_base,
            },
            "targets": base_targets_dict,
            "skip_reason": msg,
        }
        append_jsonl(args.jsonl, out)
        _record_execution_day(args, out)
        if args.notify_telegram_bot and args.notify_telegram_chat:
            notify_key = f"{asof_str}-{broker}-{fp}-{out['status']}"
            if not should_debounce_notify(args.idempotency_dir, notify_key):
                send_telegram_message(
                    args.notify_telegram_bot,
                    args.notify_telegram_chat,
                    format_telegram_message(out),
                )
        return out
    util.logToConsole(level=30)
    try:
        try:
            ib.reqMarketDataType(int(args.market_data_type))
        except Exception as exc:
            print(f"[warn:{broker}] Could not set market data type {args.market_data_type}: {exc}")
        eq = account_equity(ib, args.ib_account)
        print(f"[{broker}] NetLiquidation(USD) = {eq:,.2f}")

        qqq = qualify_stock(ib, 'QQQ'); tqqq = qualify_stock(ib, 'TQQQ'); qqq5_contract = qualify_qqq5(ib)
        mnq = None
        use_mnq_leg = bool(args.exec_mode.lower() == 'mnq' or args.use_fut_mnq)
        if use_mnq_leg:
            try:
                mnq = pick_front_mnq(ib); print(f"[{broker}] Selected MNQ: {mnq.localSymbol}")
            except Exception as exc:
                print(f"[warn:{broker}] Unable to qualify MNQ contract: {exc}")
                use_mnq_leg = False

        cancel_open_orders_for(ib, ('QQQ','TQQQ','QQQ5','MNQ','NQ'))

        # Prices
        px_qqq = get_last_price_via_ib_or_yf(ib, qqq, 'QQQ')
        px_tqqq = get_last_price_via_ib_or_yf(ib, tqqq, 'TQQQ')
        px_qqq5 = get_last_price_via_ib_or_yf(ib, qqq5_contract, 'QQQ5.L')
        print(f"[{broker}] Last prices: QQQ={px_qqq:.2f}  TQQQ={px_tqqq:.2f}  QQQ5={px_qqq5:.2f}")
        price_map = {"QQQ": px_qqq, "TQQQ": px_tqqq, "QQQ5": px_qqq5}
        target_mnq_nominal = eq * targets.tgt_mnq_nominal

        # Targets -> shares
        tgt_val_qqq = eq * targets.tgt_qqq_frac
        tgt_val_tqqq = eq * targets.tgt_tqqq_frac
        tgt_val_qqq5 = eq * targets.tgt_qqq5_frac
        tgt_sh_qqq = int(tgt_val_qqq // px_qqq)
        tgt_sh_tqqq = int(tgt_val_tqqq // px_tqqq)
        tgt_sh_qqq5 = int(tgt_val_qqq5 // px_qqq5) if px_qqq5 > 0 else 0

        # Current
        poz = ib.positions()
        allowed_equity = {"QQQ", "TQQQ", "QQQ5"}
        allowed_futures = {"MNQ", "NQ"}
        cur = {sym: 0 for sym in allowed_equity}
        stray_equities: list[tuple[Contract, int, str]] = []
        futures_market_value = 0.0
        for p in poz:
            qty = int(p.position)
            if qty == 0:
                continue
            contract = p.contract
            sym = (getattr(contract, 'symbol', '') or '').upper()
            local = getattr(contract, 'localSymbol', '') or ''
            base_local = local.split(' ')[0].upper() if local else ''
            canonical = None
            if sym in allowed_equity:
                canonical = sym
            elif base_local in allowed_equity:
                canonical = base_local
            elif sym in allowed_futures or base_local in allowed_futures:
                mv = getattr(p, "marketValue", None)
                try:
                    mv = float(mv)
                except Exception:
                    mv = None
                if mv is None or not math.isfinite(mv):
                    price_est = getattr(p, "marketPrice", None)
                    if price_est is None or not math.isfinite(price_est):
                        price_est = getattr(p, "avgCost", 0.0)
                    multiplier = getattr(contract, "multiplier", 1.0) or 1.0
                    try:
                        mv = float(price_est) * float(multiplier) * qty
                    except Exception:
                        mv = 0.0
                futures_market_value += float(mv or 0.0)
                continue
            else:
                label = sym or base_local or local or str(contract.conId)
                stray_equities.append((contract, qty, label))
                continue
            cur[canonical] += qty

        def _notional(shares: float, price: float | None) -> float:
            if not price or not math.isfinite(price):
                return 0.0
            return float(shares) * float(price)

        cur_notional = {
            "QQQ": _notional(cur['QQQ'], px_qqq),
            "TQQQ": _notional(cur['TQQQ'], px_tqqq),
            "QQQ5": _notional(cur['QQQ5'], px_qqq5),
        }
        current_etf_total = sum(cur_notional.values())
        target_etf_total = tgt_val_qqq + tgt_val_tqqq + tgt_val_qqq5
        total_target_notional = target_etf_total + target_mnq_nominal
        total_current_notional = current_etf_total + futures_market_value
        combined_net_delta = total_target_notional - total_current_notional
        exposure_totals = {
            "etf_current": current_etf_total,
            "etf_target": target_etf_total,
            "fut_current": futures_market_value,
            "fut_target": target_mnq_nominal,
            "combined_current": total_current_notional,
            "combined_target": total_target_notional,
            "combined_delta": combined_net_delta,
        }

        planned_fut_nominal = target_mnq_nominal if use_mnq_leg else futures_market_value
        futures_equiv_current = (futures_market_value / px_qqq) if px_qqq > 0 else 0.0
        futures_equiv_planned = (planned_fut_nominal / px_qqq) if px_qqq > 0 else 0.0
        adj_tgt_sh_qqq = float(tgt_sh_qqq)
        netting_notes: list[str] = []
        if not use_mnq_leg:
            adj_tgt_sh_qqq -= futures_equiv_planned
            if abs(futures_market_value) > 1e-6:
                netting_notes.append(
                    f"netting existing futures ${futures_market_value:,.0f} against ETF targets"
                )
        d_qqq = int(round(adj_tgt_sh_qqq)) - cur['QQQ']
        d_tqqq = tgt_sh_tqqq - cur['TQQQ']
        d_qqq5 = tgt_sh_qqq5 - cur['QQQ5']
        val_delta = compute_turnover_value(px_qqq, px_tqqq, d_qqq, d_tqqq, px_qqq5, d_qqq5)
        print(f"[{broker}] Current: QQQ={cur['QQQ']} TQQQ={cur['TQQQ']} QQQ5={cur['QQQ5']}")
        print(f"[{broker}] Target : QQQ={tgt_sh_qqq} TQQQ={tgt_sh_tqqq} QQQ5={tgt_sh_qqq5}")
        print(f"[{broker}] Delta  : QQQ={d_qqq:+} TQQQ={d_tqqq:+} QQQ5={d_qqq5:+}  (Δ ${val_delta:,.2f})")
        if abs(futures_market_value) > 1e-6:
            print(f"[{broker}] Futures nominal ~ ${futures_market_value:,.0f} (~{futures_equiv_current:+.0f} QQQ sh eq)")
        if use_mnq_leg and abs(planned_fut_nominal - futures_market_value) > 1e-6:
            print(f"[{broker}] Futures target nominal ~ ${planned_fut_nominal:,.0f}")
        print(
            f"[{broker}] Combined exposure (ETF+FUT) USD: current={total_current_notional:,.0f} "
            f"target={total_target_notional:,.0f} netDelta={combined_net_delta:,.0f}"
        )

        # Guards
        guard_min_drift_hit = 0
        guard_max_turnover_hit = 0
        guard_min_trade_value_hit = 0
        guard_min_shares_hit = 0
        if val_delta > args.max_turnover * eq:
            guard_max_turnover_hit = 1
            scale = (args.max_turnover * eq) / max(1e-9, val_delta)
            d_qqq = int(round(d_qqq * scale))
            d_tqqq = int(round(d_tqqq * scale))
            d_qqq5 = int(round(d_qqq5 * scale))
            val_delta = compute_turnover_value(px_qqq, px_tqqq, d_qqq, d_tqqq, px_qqq5, d_qqq5)
            print(f"[guard:{broker}] turnover limited; scaled deltas: QQQ={d_qqq:+}, TQQQ={d_tqqq:+}, QQQ5={d_qqq5:+}")

        if (val_delta / max(1e-9, eq)) < args.min_drift:
            guard_min_drift_hit = 1
            print(f"[guard:{broker}] below min drift ({args.min_drift:.3%}); skipping equity orders.")
            d_qqq = 0; d_tqqq = 0

        # Min trade guards
        if d_qqq != 0:
            trade_val = abs(d_qqq) * px_qqq
            if trade_val < args.min_trade_value:
                guard_min_trade_value_hit = 1
                d_qqq = 0
            elif abs(d_qqq) < args.min_shares:
                guard_min_shares_hit = 1
                d_qqq = 0
        if d_tqqq != 0:
            trade_val = abs(d_tqqq) * px_tqqq
            if trade_val < args.min_trade_value:
                guard_min_trade_value_hit = 1
                d_tqqq = 0
            elif abs(d_tqqq) < args.min_shares:
                guard_min_shares_hit = 1
                d_tqqq = 0
        if d_qqq5 != 0:
            price = px_qqq5 if px_qqq5 and px_qqq5 > 0 else None
            if not price:
                guard_min_trade_value_hit = 1
                d_qqq5 = 0
            else:
                trade_val = abs(d_qqq5) * price
                if trade_val < args.min_trade_value:
                    guard_min_trade_value_hit = 1
                    d_qqq5 = 0
                elif abs(d_qqq5) < args.min_shares:
                    guard_min_shares_hit = 1
                    d_qqq5 = 0

        qqq5_liq_plan = None
        qqq5_trigger = max(0.0, float(getattr(args, "qqq5_liquidity_trigger", 0.0)))
        qqq5_mode = (args.qqq5_liquidity_exec or "adaptive").lower()
        qqq5_notional = abs(d_qqq5) * px_qqq5
        if (
            d_qqq5 != 0 and px_qqq5 > 0
            and qqq5_mode != 'adaptive'
            and qqq5_trigger > 0.0
            and qqq5_notional >= qqq5_trigger
        ):
            qqq5_liq_plan = build_qqq5_liquidity_plan(d_qqq5, px_qqq5, args)
            qqq5_liq_plan["triggered"] = True
            qqq5_liq_plan["notional"] = qqq5_notional
            desc = qqq5_liq_plan.get("mode", "twap")
            if desc == "slice":
                desc += f" ({qqq5_liq_plan.get('slices','?')} slices ≈ {qqq5_liq_plan.get('slice_shares','?')} sh ea)"
            elif desc in ("twap", "vwap"):
                desc += f" ({qqq5_liq_plan.get('duration_minutes', 15.0):.1f} min)"
            print(
                f"[{broker}] QQQ5 notional ${qqq5_notional:,.0f} exceeds liquidity trigger "
                f"${qqq5_trigger:,.0f}; using {desc} execution."
            )

        delta_map = {"QQQ": d_qqq, "TQQQ": d_tqqq, "QQQ5": d_qqq5}
        txn_cost_est = 0.0 if args.dry else _estimate_transaction_costs(delta_map, price_map, args)
        executed_mnq_nominal = target_mnq_nominal if (use_mnq_leg and not args.dry) else 0.0
        orders_detail = _build_orders_detail(delta_map, price_map, qqq5_liq_plan, executed_mnq_nominal)
        post_positions = {sym: int(cur.get(sym, 0)) for sym in ('QQQ', 'TQQQ', 'QQQ5')}
        if not args.dry:
            for sym, delta in delta_map.items():
                post_positions[sym] = post_positions.get(sym, 0) + int(delta)
        post_positions["MNQ_nominal"] = executed_mnq_nominal
        cash_balance = float(eq)
        for sym, price in price_map.items():
            if price and sym in post_positions:
                cash_balance -= post_positions[sym] * price
        entry_notes: list[str] = []
        if qqq5_liq_plan:
            entry_notes.append(f"qqq5_liq={qqq5_liq_plan.get('mode')}")
        if netting_notes:
            entry_notes.extend(netting_notes)

        # MNQ leg
        if use_mnq_leg:
            if args.dry:
                print(f"[{broker}] [DRY] MNQ target nominal ~ ${target_mnq_nominal:,.0f}")
            else:
                try:
                    _ = place_or_adjust_mnq(ib, mnq, target_mnq_nominal)
                except Exception as exc:
                    print(f"[warn:{broker}] Failed to adjust MNQ: {exc}")
            print(f"[{broker}] MNQ target nominal ~ ${target_mnq_nominal:,.0f}")

        stray_logs: list[dict] = []
        for contract, qty, label in stray_equities:
            if qty == 0:
                continue
            if getattr(contract, 'secType', '').upper() != 'STK':
                print(f"[{broker}] [warn] Unmanaged non-equity position {label} ({qty} shares); please close manually.")
                continue
            delta = -qty
            desc = getattr(contract, 'localSymbol', '') or label
            stray_logs.append({"symbol": desc, "delta_shares": delta})
            if args.dry:
                print(f"[{broker}] [DRY] Liquidate stray position {desc}: delta {delta:+}")
            else:
                try:
                    place_delta_order_stock(ib, contract, delta, use_adaptive=False)
                    print(f"[{broker}] Liquidating stray position {desc}: delta {delta:+}")
                except Exception as exc:
                    print(f"[warn:{broker}] Failed to liquidate {desc}: {exc}")

        # Orders
        if d_qqq!=0 or d_tqqq!=0 or d_qqq5!=0:
            if args.dry:
                print(f"[{broker}] [DRY] Orders skipped.")
                if qqq5_liq_plan:
                    print(f"[{broker}] [DRY] QQQ5 liquidity plan: {qqq5_liq_plan}")
            else:
                if d_qqq!=0:
                    place_delta_order_stock(ib, qqq, d_qqq, use_adaptive=True)
                if d_tqqq!=0:
                    place_delta_order_stock(ib, tqqq, d_tqqq, use_adaptive=True)
                if d_qqq5!=0:
                    if qqq5_liq_plan:
                        try:
                            execute_qqq5_liquidity_plan(ib, qqq5_contract, d_qqq5, qqq5_liq_plan)
                            print(f"[{broker}] QQQ5 liquidity execution submitted (mode={qqq5_liq_plan.get('mode')}).")
                        except Exception as exc:
                            print(f"[warn:{broker}] QQQ5 liquidity execution failed ({exc}); using single order.")
                            place_delta_order_stock(ib, qqq5_contract, d_qqq5, use_adaptive=True)
                    else:
                        place_delta_order_stock(ib, qqq5_contract, d_qqq5, use_adaptive=True)
                ib.sleep(1.5)

        # Summary
        summary = build_summary_dict(
            broker=broker, asof_str=asof_str, dry=args.dry, exec_mode=args.exec_mode,
            committee_info=committee_info, fp=fp, picked=picked, committee_rows=committee_rows,
            fused=fused, targets=targets, eq=eq, px_qqq=px_qqq, px_tqqq=px_tqqq,
            cur=cur, tgt_sh_qqq=tgt_sh_qqq, tgt_sh_tqqq=tgt_sh_tqqq,
            d_qqq=d_qqq, d_tqqq=d_tqqq, delta_value=val_delta,
            px_qqq5=px_qqq5, tgt_sh_qqq5=tgt_sh_qqq5, d_qqq5=d_qqq5,
        )
        summary["guard_min_drift_hit"] = guard_min_drift_hit
        summary["guard_max_turnover_hit"] = guard_max_turnover_hit
        summary["guard_min_trade_value_hit"] = guard_min_trade_value_hit
        summary["guard_min_shares_hit"] = guard_min_shares_hit
        sim_snapshot = _build_sim_snapshot(summary.get("equity_usd", eq), base_targets_dict, price_map)
        summary["post_positions"] = post_positions
        summary["cash_balance"] = cash_balance
        summary["orders_detail"] = orders_detail
        summary["transaction_cost_est"] = txn_cost_est
        summary["order_mnq_nominal"] = executed_mnq_nominal
        summary["sim_snapshot"] = sim_snapshot
        summary["entry_notes"] = entry_notes
        summary["futures_market_value"] = futures_market_value
        summary["planned_futures_nominal"] = planned_fut_nominal
        summary["exposure_totals"] = exposure_totals
        summary["futures_equiv_shares"] = {
            "current": futures_equiv_current,
            "planned": futures_equiv_planned,
        }
        realized_beta = compute_realized_effective_leverage_from_positions(
            summary.get("post_positions"),
            summary.get("prices"),
            summary.get("equity_usd"),
        )
        summary["effective_leverage_realized"] = realized_beta
        eff_target = summary.get("effective_leverage")
        if (
            isinstance(eff_target, (int, float)) and math.isfinite(eff_target)
            and isinstance(realized_beta, (int, float)) and math.isfinite(realized_beta)
        ):
            diff = realized_beta - eff_target
            summary["effective_leverage_diff"] = diff
            if abs(diff) > 0.05:
                entry_notes.append(f"beta_diff={diff:+.2f}")
                print(
                    f"[{broker}] [warn] Effective leverage diff {diff:+.2f}x "
                    f"(target {eff_target:.2f}x vs actual {realized_beta:.2f}x)"
                )
        if qqq5_liq_plan:
            summary["qqq5_liquidity_plan"] = qqq5_liq_plan
        if stray_logs:
            summary["stray_liquidations"] = stray_logs
        perf_snapshot = _compute_performance_snapshot(
            getattr(args, "execution_record", None),
            broker,
            asof_str,
            summary.get("equity_usd"),
        )
        summary["perf_snapshot"] = perf_snapshot
        summary["ytd_return"] = perf_snapshot.get("ytd_return")

        print(format_console_summary(summary))

        append_jsonl(args.jsonl, summary)
        _record_execution_day(args, summary)
        if args.notify_telegram_bot and args.notify_telegram_chat:
            notify_key = f"{asof_str}-{broker}-{fp}-{summary['status']}"
            if not should_debounce_notify(args.idempotency_dir, notify_key):
                send_telegram_message(
                    args.notify_telegram_bot,
                    args.notify_telegram_chat,
                    format_telegram_message(summary),
                )

        if not args.dry:
            idempotency_mark(lock_path)
            print(f"[idempotent:{broker}] lock written: {lock_path}")

        return summary

    finally:
        ib.disconnect()


def execute_on_moomoo(args, fused: StrategySnapshot, targets: ExecTargets,
                      fp: str, committee_info: dict, picked: list, committee_rows: list, asof_str: str) -> dict:
    broker = "moomoo"
    base_targets_dict = _targets_to_dict(targets)

    # MNQ not supported on Moomoo
    if args.exec_mode.lower() == 'mnq':
        msg = "MNQ/futures not supported via Moomoo OpenD; skipping this broker."
        print(f"[{broker}] {msg}")
        return {"asof": asof_str, "broker": broker, "dry": bool(args.dry), "skip_reason": msg}

    # idempotency (per broker)
    skip, lock_path = idempotency_should_skip(asof_str, broker, fp, args.idempotency_dir)
    if skip:
        out = {"asof": asof_str, "broker": broker, "dry": bool(args.dry), "skip_reason": "idempotent_lock"}
        print(f"[idempotent:{broker}] Already executed for asof={asof_str}, fp={fp}. Lock: {lock_path}")
        return out

    mm = connect_moomoo(args.mm_host, int(args.mm_port), args.mm_trd_env, args.mm_acc_id, args.mm_trade_pwd)
    try:
        eq = mm_account_equity(mm)
        print(f"[{broker}] total assets (USD) = {eq:,.2f}")

        mm_cancel_open_orders(mm, ('QQQ.US','TQQQ.US'))

        # Prices (with yf fallback inside)
        px_qqq = mm_last_price(mm, 'QQQ.US')
        px_tqqq = mm_last_price(mm, 'TQQQ.US')
        print(f"[{broker}] Last prices: QQQ.US={px_qqq:.2f}  TQQQ.US={px_tqqq:.2f}")
        price_map = {"QQQ": px_qqq, "TQQQ": px_tqqq, "QQQ5": None}
        target_mnq_nominal = eq * targets.tgt_mnq_nominal

        # Effective targets -> shares (optionally replicate QQQ5 via FUT mix)
        local_targets = ExecTargets(
            tgt_cash_frac=targets.tgt_cash_frac,
            tgt_qqq_frac=targets.tgt_qqq_frac,
            tgt_tqqq_frac=targets.tgt_tqqq_frac,
            tgt_qqq5_frac=targets.tgt_qqq5_frac,
            tgt_mnq_nominal=targets.tgt_mnq_nominal,
        )
        replication_note = None
        orig_qqq5 = local_targets.tgt_qqq5_frac
        if args.mm_qqq5_replicate and orig_qqq5 > 1e-6:
            add_qqq, add_tqqq = replicate_levered_sleeve_via_fut(orig_qqq5, args.mm_qqq5_leverage)
            local_targets = ExecTargets(
                tgt_cash_frac=local_targets.tgt_cash_frac,
                tgt_qqq_frac=local_targets.tgt_qqq_frac + add_qqq,
                tgt_tqqq_frac=local_targets.tgt_tqqq_frac + add_tqqq,
                tgt_qqq5_frac=0.0,
                tgt_mnq_nominal=local_targets.tgt_mnq_nominal,
            )
            replication_note = {
                "source_frac": orig_qqq5,
                "added_qqq_frac": add_qqq,
                "added_tqqq_frac": add_tqqq,
                "assumed_leverage": args.mm_qqq5_leverage,
            }
            print(f"[{broker}] Replicating QQQ5={orig_qqq5:.3f} via FUT mix -> +QQQ={add_qqq:.3f}, +TQQQ={add_tqqq:.3f} (lev {args.mm_qqq5_leverage:.1f}x)")
        elif orig_qqq5 > 1e-6:
            print(f"[{broker}] QQQ5 target {orig_qqq5:.3f} is ignored on moomoo (instrument unavailable).")

        tgt_val_qqq = eq * local_targets.tgt_qqq_frac
        tgt_val_tqqq = eq * local_targets.tgt_tqqq_frac
        tgt_sh_qqq = mm_round_shares(tgt_val_qqq, px_qqq, lot=1)
        tgt_sh_tqqq = mm_round_shares(tgt_val_tqqq, px_tqqq, lot=1)

        # Current
        pos = mm_get_positions(mm)
        cur = {'QQQ': int(pos.get('QQQ.US', 0)), 'TQQQ': int(pos.get('TQQQ.US', 0)), 'QQQ5': 0}
        stray_mm = [(code, qty) for code, qty in pos.items()
                    if code not in ('QQQ.US', 'TQQQ.US') and qty != 0]

        d_qqq = tgt_sh_qqq - cur['QQQ']
        d_tqqq = tgt_sh_tqqq - cur['TQQQ']
        val_delta = compute_turnover_value(px_qqq, px_tqqq, d_qqq, d_tqqq)
        print(f"[{broker}] Current: QQQ={cur['QQQ']} TQQQ={cur['TQQQ']}")
        print(f"[{broker}] Target : QQQ={tgt_sh_qqq} TQQQ={tgt_sh_tqqq}")
        print(f"[{broker}] Delta  : QQQ={d_qqq:+} TQQQ={d_tqqq:+}  (Δ ${val_delta:,.2f})")

        # Guards (same logic)
        guard_min_drift_hit = 0
        guard_max_turnover_hit = 0
        guard_min_trade_value_hit = 0
        guard_min_shares_hit = 0
        if val_delta > args.max_turnover * eq:
            guard_max_turnover_hit = 1
            scale = (args.max_turnover * eq) / max(1e-9, val_delta)
            d_qqq = int(round(d_qqq * scale))
            d_tqqq = int(round(d_tqqq * scale))
            val_delta = compute_turnover_value(px_qqq, px_tqqq, d_qqq, d_tqqq)
            print(f"[guard:{broker}] turnover limited; scaled deltas: QQQ={d_qqq:+}, TQQQ={d_tqqq:+}")

        if (val_delta / max(1e-9, eq)) < args.min_drift:
            guard_min_drift_hit = 1
            print(f"[guard:{broker}] below min drift ({args.min_drift:.3%}); skipping equity orders.")
            d_qqq = 0; d_tqqq = 0

        if d_qqq != 0:
            trade_val = abs(d_qqq) * px_qqq
            if trade_val < args.min_trade_value:
                guard_min_trade_value_hit = 1
                d_qqq = 0
            elif abs(d_qqq) < args.min_shares:
                guard_min_shares_hit = 1
                d_qqq = 0
        if d_tqqq != 0:
            trade_val = abs(d_tqqq) * px_tqqq
            if trade_val < args.min_trade_value:
                guard_min_trade_value_hit = 1
                d_tqqq = 0
            elif abs(d_tqqq) < args.min_shares:
                guard_min_shares_hit = 1
                d_tqqq = 0

        delta_map = {"QQQ": d_qqq, "TQQQ": d_tqqq, "QQQ5": 0}
        txn_cost_est = 0.0 if args.dry else _estimate_transaction_costs(delta_map, price_map, args)
        orders_detail = _build_orders_detail(delta_map, price_map, None, 0.0)
        post_positions = {'QQQ': int(cur['QQQ']), 'TQQQ': int(cur['TQQQ']), 'QQQ5': 0}
        if not args.dry:
            post_positions['QQQ'] += d_qqq
            post_positions['TQQQ'] += d_tqqq
        post_positions['MNQ_nominal'] = 0.0
        cash_balance = float(eq)
        for sym in ('QQQ', 'TQQQ'):
            cash_balance -= post_positions[sym] * price_map[sym]
        entry_notes: list[str] = []
        if replication_note:
            entry_notes.append("mm_qqq5_replicated")

        mm_stray_logs: list[dict] = []
        for code, qty in stray_mm:
            delta = -qty
            mm_stray_logs.append({"code": code, "delta_shares": delta})
            if args.dry:
                print(f"[{broker}] [DRY] Liquidate stray position {code}: delta {delta:+}")
            else:
                try:
                    mm_place_delta_market(mm, code, delta)
                    print(f"[{broker}] Liquidating stray position {code}: delta {delta:+}")
                except Exception as exc:
                    print(f"[warn:{broker}] Failed to liquidate {code}: {exc}")

        # Orders
        if d_qqq!=0 or d_tqqq!=0:
            if args.dry:
                print(f"[{broker}] [DRY] Orders skipped.")
            else:
                if d_qqq != 0: mm_place_delta_market(mm, 'QQQ.US', d_qqq)
                if d_tqqq != 0: mm_place_delta_market(mm, 'TQQQ.US', d_tqqq)
        else:
            print(f"[{broker}] No orders to place after guards.")

        # Summary (QQQ5 not tradable on moomoo; optionally replicated via FUT mix)
        px_qqq5 = None
        tgt_sh_qqq5 = 0
        d_qqq5 = 0

        summary = build_summary_dict(
            broker=broker, asof_str=asof_str, dry=args.dry, exec_mode=args.exec_mode,
            committee_info=committee_info, fp=fp, picked=picked, committee_rows=committee_rows,
            fused=fused, targets=local_targets, eq=eq, px_qqq=px_qqq, px_tqqq=px_tqqq,
            cur=cur, tgt_sh_qqq=tgt_sh_qqq, tgt_sh_tqqq=tgt_sh_tqqq,
            d_qqq=d_qqq, d_tqqq=d_tqqq, delta_value=val_delta,
            px_qqq5=px_qqq5, tgt_sh_qqq5=tgt_sh_qqq5, d_qqq5=d_qqq5,
        )
        summary["guard_min_drift_hit"] = guard_min_drift_hit
        summary["guard_max_turnover_hit"] = guard_max_turnover_hit
        summary["guard_min_trade_value_hit"] = guard_min_trade_value_hit
        summary["guard_min_shares_hit"] = guard_min_shares_hit
        sim_snapshot = _build_sim_snapshot(summary.get("equity_usd", eq), base_targets_dict, price_map)
        summary["post_positions"] = post_positions
        summary["cash_balance"] = cash_balance
        summary["orders_detail"] = orders_detail
        summary["transaction_cost_est"] = txn_cost_est
        summary["order_mnq_nominal"] = 0.0
        summary["sim_snapshot"] = sim_snapshot
        summary["entry_notes"] = entry_notes
        realized_beta = compute_realized_effective_leverage_from_positions(
            summary.get("post_positions"),
            summary.get("prices"),
            summary.get("equity_usd"),
        )
        summary["effective_leverage_realized"] = realized_beta
        eff_target = summary.get("effective_leverage")
        if (
            isinstance(eff_target, (int, float)) and math.isfinite(eff_target)
            and isinstance(realized_beta, (int, float)) and math.isfinite(realized_beta)
        ):
            diff = realized_beta - eff_target
            summary["effective_leverage_diff"] = diff
            if abs(diff) > 0.05:
                entry_notes.append(f"beta_diff={diff:+.2f}")
                print(
                    f"[{broker}] [warn] Effective leverage diff {diff:+.2f}x "
                    f"(target {eff_target:.2f}x vs actual {realized_beta:.2f}x)"
                )
        if replication_note:
            summary["qqq5_replication"] = replication_note
        if mm_stray_logs:
            summary["stray_liquidations"] = mm_stray_logs
        perf_snapshot = _compute_performance_snapshot(
            getattr(args, "execution_record", None),
            broker,
            asof_str,
            summary.get("equity_usd"),
        )
        summary["perf_snapshot"] = perf_snapshot
        summary["ytd_return"] = perf_snapshot.get("ytd_return")
        drawdown_live = update_dd_alert_state(
            getattr(args, "dd_state", None),
            summary.get("equity_usd"),
            asof_str,
            getattr(args, "dd_alert_threshold", 0.0),
            getattr(args, "notify_telegram_bot", None),
            getattr(args, "notify_telegram_chat", None),
        )
        if drawdown_live is not None:
            summary["live_drawdown"] = drawdown_live

        print(format_console_summary(summary))

        append_jsonl(args.jsonl, summary)
        _record_execution_day(args, summary)
        if args.notify_telegram_bot and args.notify_telegram_chat:
            notify_key = f"{asof_str}-{broker}-{fp}-{summary['status']}"
            if not should_debounce_notify(args.idempotency_dir, notify_key):
                send_telegram_message(
                    args.notify_telegram_bot,
                    args.notify_telegram_chat,
                    format_telegram_message(summary),
                )

        if not args.dry:
            idempotency_mark(lock_path)
            print(f"[idempotent:{broker}] lock written: {lock_path}")

        return summary

    finally:
        mm.close()


# =========================
# Main
# =========================
def main():
    load_env_file()
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument('--config', type=str, default='live_trader.yaml')
    base_args, remaining = base_parser.parse_known_args()

    config_data = load_yaml_config(base_args.config) if base_args.config else {}
    config_defaults = flatten_live_config(config_data)
    env_defaults = env_defaults_from_os()
    combined_defaults: Dict[str, object] = {}
    combined_defaults.update(config_defaults)
    combined_defaults.update(env_defaults)

    ap = argparse.ArgumentParser(parents=[base_parser])
    # Strategy & dates
    ap.add_argument('--strategy', type=str, default='qqq_deep_learning_and_baseline.py')
    ap.add_argument('--start', type=str, default='2015-01-01')
    ap.add_argument('--end', type=str, default='today')
    # Keeper / committee
    ap.add_argument('--keepers-csv', type=str, default='robust_rel_v5f_run4_topk_final.csv')
    ap.add_argument('--oos-csv', type=str, default='robust_rel_v5f_run4_oos.csv')
    ap.add_argument('--topn', type=int, default=3, help='How many keepers to lift (committee).')
    ap.add_argument('--committee', type=str, default='mean',
                    choices=['off', 'mean', 'median', 'voltarget', 'dsr'],
                    help='Committee fuse method. Use "off" to run single config.')
    ap.add_argument('--committee-clamp', type=float, default=0.10,
                    help='Clamp committee sleeves around median by this fraction (0 disables).')
    ap.add_argument('--oos-prune', action='store_true',
                    help='Drop configs flagged by OOS degradation guard before committee fuse.')
    ap.add_argument('--pick-row', type=int, default=None, help='Force pick a single row from keepers CSV.')
    # Execution sleeve
    ap.add_argument('--exec-mode', type=str, default='mnq', choices=['etf','mnq'])
    # Which brokers to run (one or both)
    ap.add_argument('--brokers', nargs='+', choices=['ib','moomoo'], default=['ib'],
                    help='Choose one or more brokers to execute: ib, moomoo')
    # IB
    ap.add_argument('--ib-host', type=str, default='127.0.0.1')
    ap.add_argument('--ib-port', type=int, default=7497)
    ap.add_argument('--ib-client-id', type=int, default=11)
    ap.add_argument('--ib-account', type=str, default=None)
    ap.add_argument('--market-data-type', type=int, default=1,
                    help='IB market data type: 1=real-time, 2=delayed-frozen, 3=delayed, 4=hist-only')
    # Moomoo / OpenD (US ETFs)
    ap.add_argument('--mm-host', type=str, default='127.0.0.1')
    ap.add_argument('--mm-port', type=int, default=11111)
    ap.add_argument('--mm-trd-env', type=str, default='SIMULATE', choices=['SIMULATE', 'REAL'])
    ap.add_argument('--mm-acc-id', type=int, default=None, help='Moomoo trade account id')
    ap.add_argument('--mm-trade-pwd', type=str, default=None, help='Moomoo trading unlock password')
    ap.add_argument('--mm-qqq5-leverage', type=float, default=5.0,
                    help='Assumed leverage for QQQ5 replication via FUT mix on moomoo.')
    ap.add_argument('--mm-qqq5-replicate', dest='mm_qqq5_replicate', action='store_true',
                    help='Replicate the QQQ5 sleeve on moomoo using a FUT-style QQQ/TQQQ mix.')
    ap.add_argument('--mm-qqq5-ignore', dest='mm_qqq5_replicate', action='store_false',
                    help='Disable QQQ5 replication on moomoo (legacy behavior).')
    # QQQ5 liquidity-aware execution (IB)
    ap.add_argument('--qqq5-liquidity-trigger', type=float, default=150000.0,
                    help='USD notional threshold beyond which QQQ5 orders use liquidity-aware execution (<=0 disables).')
    ap.add_argument('--qqq5-liquidity-exec', type=str, default='twap',
                    choices=['adaptive', 'twap', 'vwap', 'slice'],
                    help='Execution style once the QQQ5 liquidity trigger fires.')
    ap.add_argument('--qqq5-twap-minutes', type=float, default=15.0,
                    help='Duration (minutes) for TWAP/VWAP liquidity execution.')
    ap.add_argument('--qqq5-liquidity-start-delay', type=float, default=2.0,
                    help='Seconds to delay the start of TWAP/VWAP orders after submission.')
    ap.add_argument('--qqq5-slice-notional', type=float, default=50000.0,
                    help='Target USD notional per child slice when using slice mode.')
    ap.add_argument('--qqq5-slice-pause', type=float, default=20.0,
                    help='Seconds to pause between slices in slice mode.')
    ap.add_argument('--qqq5-vwap-max-pct', type=float, default=0.15,
                    help='Max participation (percent of est volume, e.g., 0.15 = 15%%) when using VWAP mode.')
    ap.add_argument('--execution-record', '--exec-log', dest='execution_record', type=str,
                    default='logs/ib_execution_log.csv',
                    help='CSV file to append daily execution vs. simulation tracking (alias --exec-log).')
    ap.add_argument('--dd-alert-threshold', type=float, default=0.25,
                    help='Trigger Telegram alert if live NAV drawdown exceeds this fraction (e.g., 0.25 = 25%%).')
    ap.add_argument('--dd-alert-state-json', type=str, default='logs/live_dd_state.json',
                    help='Path to persist live drawdown state (HWM / min DD / last alert date).')
    # Guards
    ap.add_argument('--min-trade-value', type=float, default=500.0)
    ap.add_argument('--min-shares', type=int, default=1)
    ap.add_argument('--max-turnover', type=float, default=0.40)
    ap.add_argument('--min-drift', type=float, default=0.010, help='Skip trading if turnover/equity < this (e.g., 0.010 = 1%%).')
    ap.add_argument('--sum-cap', type=float, default=0.98)
    ap.add_argument('--qqq5-scale', type=float, default=5.0/3.0)
    ap.add_argument('--use-fut-mnq', action='store_true',
                    help='Express the futures sleeve via MNQ in addition to ETF legs (IB only)')
    # Idempotency & audit
    ap.add_argument('--idempotency-dir', type=str, default='.state')
    ap.add_argument('--jsonl', type=str, default='logs/rebalance_log.jsonl')
    # Optional market-time gate (coarse, ET-based)
    ap.add_argument('--close-window', action='store_true', help='Only run in close window (approx ET 15:30~18:00).')
    # NAV pricing / accounting
    ap.add_argument('--nav-price-source', type=str, default='yfinance_close', choices=['live', 'yfinance_close'],
                    help='Override NAV in execution record using this price source (for backtest alignment).')
    ap.add_argument('--nav-include-mnq', action='store_true',
                    help='Include MNQ nominal when recomputing NAV (makes NAV/lever consistent if futures sleeve used).')
    # Defaults for manual single-run
    ap.add_argument('--base-kelly-frac', type=float, default=0.45)
    ap.add_argument('--target-vol', type=float, default=0.32)
    ap.add_argument('--bandit-alpha', type=float, default=0.75)
    ap.add_argument('--rebal-days', type=int, default=5)
    # Notify
    ap.add_argument('--notify-telegram-bot', type=str, default=None)
    ap.add_argument('--notify-telegram-chat', type=str, default=None)
    # Dry
    ap.add_argument('--dry', action='store_true')

    ap.set_defaults(mm_qqq5_replicate=True)
    if combined_defaults:
        ap.set_defaults(**combined_defaults)
    ap.set_defaults(config=base_args.config)
    ap.set_defaults(nav_include_mnq=True)
    args = ap.parse_args(remaining)
    apply_env_overrides(args)
    args.dd_state = load_dd_alert_state(getattr(args, "dd_alert_state_json", ""))

    # Optional close-window gate
    if args.close_window:
        now_utc = pd.Timestamp.now(tz="UTC")
        allowed, detail = is_in_close_window(now_utc)
        if not allowed:
            print(f"[gate] Outside close window (ET). Detail: {detail}")
            return

    # Strategy snapshot fused once (shared by brokers)
    start_iso, end_iso = normalize_dates_for_yf(args.start, args.end)
    mod = import_strategy_module(args.strategy)

    # --- configs (keepers or ad-hoc) ---
    if args.keepers_csv and os.path.exists(args.keepers_csv):
        keepers = pd.read_csv(args.keepers_csv)
        if args.pick_row is not None:
            if args.pick_row < 0 or args.pick_row >= len(keepers):
                raise ValueError(f"--pick-row out of range (0..{len(keepers) - 1})")
            cfg_df = keepers.iloc[[args.pick_row]].copy()
        else:
            cfg_df = keepers.head(1) if args.committee.lower() == 'off' else \
                     load_keepers(args.keepers_csv, args.oos_csv, args.topn)
    else:
        cfg_df = pd.DataFrame([{
            "base_kelly_frac": args.base_kelly_frac,
            "target_vol": args.target_vol,
            "bandit_alpha": args.bandit_alpha,
            "rebal_days": args.rebal_days,
        }])

    records = cfg_df.to_dict("records")
    if args.oos_prune and args.pick_row is None:
        records = oos_prune(records)
        if not records:
            print("[gate] All keeper configs filtered by OOS guards.")
            return
        cfg_df = pd.DataFrame.from_records(records)

    if cfg_df.empty:
        print("[gate] No keeper rows available.")
        return

    def pick_value(rec: dict, keys: Iterable[str], default=None):
        for key in keys:
            if key in rec and pd.notna(rec[key]):
                return rec[key]
        return default

    picked: List[Tuple[float, float, float, int]] = []
    committee_rows: List[dict] = []
    snaps: List[StrategySnapshot] = []

    for rec in records:
        base_k = float(pick_value(rec, ("base_kelly_frac", "base_kelly", "kelly", "kelly_frac"), args.base_kelly_frac))
        tv = float(pick_value(rec, ("target_vol", "target_vol_ann", "tv"), args.target_vol))
        ba = float(pick_value(rec, ("bandit_alpha", "alpha", "ba"), args.bandit_alpha))
        rd = int(float(pick_value(rec, ("rebal_days", "rebalance_every_days", "rebal"), args.rebal_days)))
        picked.append((base_k, tv, ba, rd))

        snap = run_strategy_snapshot(
            mod,
            start_iso,
            end_iso,
            base_kelly_frac=base_k,
            target_vol=tv,
            bandit_alpha=ba,
            rebal_days=rd,
            other=dict(
                policy='bandit',
                trade_cost_bps=1.5,
                fut_fin_spread=0.002,
                tqqq_expense=0.009,
                qqq5_expense=0.0095,
                risk_gate=True,
                slip_bps=1.5,
                impact_k=0.0005,
            ),
        )
        snaps.append(snap)
        committee_rows.append({
            "w_fut": snap.w_fut,
            "w_tqqq": snap.w_tqqq,
            "w_qqq5": snap.w_qqq5,
            "Vol": float(pick_value(rec, ("Vol", "vol", "AnnVol", "ann_vol"), float("nan"))),
            "DSR": float(pick_value(rec, ("DSR", "dsr", "Sharpe", "Sharpe_ex_rf0"), float("nan"))),
        })
        print(f"  snapshot asof={snap.asof.date()} -> fut={snap.w_fut:.3f} tqqq={snap.w_tqqq:.3f} qqq5={snap.w_qqq5:.3f} L={snap.L_base:.2f}")

    print(f"Picked {len(picked)} config(s): {picked}")
    if not snaps:
        print("[gate] Snapshot generation failed.")
        return

    committee_mode = args.committee.lower()
    clamp_value = None if args.committee_clamp <= 0 else args.committee_clamp
    if len(snaps) == 1 or committee_mode == 'off':
        fused = snaps[0]
        committee_info = {
            "mode": "single",
            "members": len(snaps),
            "weights": [float(fused.w_fut), float(fused.w_tqqq), float(fused.w_qqq5)],
        }
    else:
        weights_vec = compute_committee_weights(committee_rows, mode=committee_mode, clamp=clamp_value)
        fused = StrategySnapshot(
            asof=max(s.asof for s in snaps),
            w_fut=float(weights_vec[0]),
            w_tqqq=float(weights_vec[1]),
            w_qqq5=float(weights_vec[2]),
            L_base=float(np.mean([s.L_base for s in snaps])),
        )
        committee_info = {
            "mode": committee_mode,
            "members": len(snaps),
            "clamp": clamp_value,
            "weights": [float(x) for x in weights_vec],
        }

    asof_str = str(fused.asof.date())
    fp = committee_fingerprint(picked)
    print(f"[{committee_info['mode']}] fused snapshot asof={fused.asof.date()} | fut={fused.w_fut:.3f} tqqq={fused.w_tqqq:.3f} qqq5={fused.w_qqq5:.3f} L={fused.L_base:.2f}")

    # Map sleeves to execution targets (shared by brokers; each uses its own equity/positions)
    targets_ib = map_weights_to_exec(
        fused, args.exec_mode, max_sum=args.sum_cap, qqq5_to_tqqq_scale=args.qqq5_scale,
        use_futures_sleeve=bool(args.use_fut_mnq)
    )
    targets_etf = map_weights_to_exec(
        fused, args.exec_mode, max_sum=args.sum_cap, qqq5_to_tqqq_scale=args.qqq5_scale,
        use_futures_sleeve=False
    )
    preview_targets = targets_ib if args.use_fut_mnq else targets_etf
    preview_beta = compute_effective_leverage(preview_targets)
    print(f"Exec targets ({args.exec_mode.upper()}): CASH={preview_targets.tgt_cash_frac:.3f} "
          f"QQQ={preview_targets.tgt_qqq_frac:.3f} TQQQ={preview_targets.tgt_tqqq_frac:.3f} "
          f"QQQ5={preview_targets.tgt_qqq5_frac:.3f} MNQ_notional={preview_targets.tgt_mnq_nominal:.3f}*Equity "
          f"| Effective beta ≈ {preview_beta:.2f}x")

    # Run on each requested broker
    summaries: list[dict] = []
    if args.use_fut_mnq and 'moomoo' in args.brokers:
        print("[warn] --use-fut-mnq ignored for Moomoo; that broker will run ETF-only.")
    for broker in args.brokers:
        if broker == 'ib':
            summaries.append(execute_on_ib(args, fused, targets_ib, fp, committee_info, picked, committee_rows, asof_str))
        elif broker == 'moomoo':
            summaries.append(execute_on_moomoo(args, fused, targets_etf, fp, committee_info, picked, committee_rows, asof_str))

    # Print compact combined footer
    print("\n=== Combined run summary (per broker) ===")
    for s in summaries:
        tag = s.get("broker","?").upper()
        st = s.get("status","-")
        skip = s.get("skip_reason","")
        print(f"  {tag}: status={st}{(' skip='+skip) if skip else ''}")
    save_dd_alert_state(getattr(args, "dd_alert_state_json", ""), getattr(args, "dd_state", None))

if __name__=="__main__":
    main()
