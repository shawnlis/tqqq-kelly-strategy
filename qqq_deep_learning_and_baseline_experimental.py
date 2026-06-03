#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qqq_deep_learning_and_baseline.py  (v4 with Bandit policy + Risk Gate)
在 v3 的基础上加入两项改进：
1) 轻量“上下文 bandit”策略脑（policy_decide）：动态调节 Kelly 分数、跌幅触发阈值（±2%微调）与反弹步长（8/10/12%）；
2) “事件/波动风控门”（risk gate）：当 VIX 偏高或实现波动簇集时，压降 kelly_frac、外移触发阈值、放宽反弹步长。
并保留：
- 基线（非 DL）与 DL 两种回测；DL 不可用时回落到规则；
- 无前视滚动 Kelly + 周期再平衡；
- 交易成本/融资拖累建模；
- QQQ5 合成（带 airbag 日内保护），若能抓到实盘价则自动混合。
用法示例：
  python qqq_deep_learning_and_baseline.py --mode both --plot
  python qqq_deep_learning_and_baseline.py --mode baseline --policy bandit --start 2015-01-01 --end 2025-10-05
  python qqq_deep_learning_and_baseline.py --mode dl --policy none
"""
import os, math, argparse, sys, warnings, random, subprocess, copy
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
# ------------------ Optional deep-learning imports (conditional) ------------------
DL_AVAILABLE = False
PRIMARY_COMPUTE_DEVICE = "cpu"
TORCH_DEVICE = None
try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    import torch.nn.functional as F
    TORCH_DEVICE = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    if torch.cuda.is_available():
        gpu_names = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        print(f"Available GPUs: {gpu_names}")
        PRIMARY_COMPUTE_DEVICE = "cuda"
    else:
        print("PyTorch running on CPU.")
        PRIMARY_COMPUTE_DEVICE = "cpu"
    TORCH_AVAILABLE = True
except Exception:
    TORCH_AVAILABLE = False
    torch = None  # type: ignore
    print("Warning: PyTorch not available. DL strategy will fallback to rules.")

try:
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except Exception:
    StandardScaler = None  # type: ignore
    SKLEARN_AVAILABLE = False
    print("Warning: scikit-learn not available. DL strategy will fallback to rules.")

DL_AVAILABLE = bool(TORCH_AVAILABLE and SKLEARN_AVAILABLE)
if not DL_AVAILABLE:
    PRIMARY_COMPUTE_DEVICE = "cpu"
warnings.filterwarnings("ignore", category=UserWarning)
DAILY_METRICS_CSV = "logs/strategy_daily_metrics.csv"
def log_daily_metrics(metrics_dict, csv_path: str = DAILY_METRICS_CSV):
    """
    Append one row of daily metrics to a CSV file.
    Creates directories / file if missing.
    metrics_dict keys should be JSON-serializable scalars.
    """
    import pandas as pd

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    row_df = pd.DataFrame([metrics_dict])
    if csv_path.exists():
        try:
            existing = pd.read_csv(csv_path, low_memory=False)
            df = pd.concat([existing, row_df], ignore_index=True)
        except Exception:
            df = row_df
    else:
        df = row_df
    df.to_csv(csv_path, index=False)
def _last_full_month_range(now=None):
    import pandas as pd
    now = pd.Timestamp.now(tz=None) if now is None else pd.Timestamp(now)
    first_of_this_month = pd.Timestamp(year=now.year, month=now.month, day=1)
    end = first_of_this_month - pd.Timedelta(days=1)
    start = pd.Timestamp(year=end.year, month=end.month, day=1)
    return start, end
# ------------------ Determinism ------------------
def set_global_seed(seed=42):
    np.random.seed(seed)
    random.seed(seed)
    if TORCH_AVAILABLE:
        try:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
        except Exception:
            pass

set_global_seed(42)
# ------------------ Platform helpers ------------------
def open_file_with_default_viewer(path):
    try:
        if os.name == 'nt':
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception:
        pass
# ------------------ Product/fee assumptions for synthetic QQQ5 ------------------
ALT_QQQ5_TICKERS = ("QQQ5.L", "QQQ5-USD.L", "QQQ5.MI", "QQQ5.SW")  # 尝试抓取真实 5x ETP
QQQ5_MANAGEMENT_FEE_BPS = 75.0
QQQ5_FINANCING_SPREAD_BPS = 200.0
QQQ5_FLAT_FEE_BPS = 100.0
QQQ5_AIRBAG_TRIGGER = 0.20  # 单日标的跌幅超过 20% 启动日内“气囊”
# ------------------ Utils ------------------
def ann_stats_from_daily(ret_daily):
    mu_arith = ret_daily.mean()*252.0
    sigma = ret_daily.std()*np.sqrt(252.0)
    return mu_arith, sigma
def kelly_star(mu, rf, sigma):
    if sigma <= 1e-8: return 0.0
    return max(0.0, (mu - rf)/(sigma**2))
def get_rf_value_for_date(rf_series, date, default_value):
    if rf_series is None:
        return default_value
    idx = getattr(rf_series, "index", None)
    if idx is None or date not in idx:
        return default_value
    rf_value = rf_series.loc[date]
    if isinstance(rf_value, pd.DataFrame):
        rf_value = rf_value.squeeze()
    if isinstance(rf_value, pd.Series):
        rf_value = rf_value.iloc[0]
    if not np.isscalar(rf_value):
        rf_value = np.asarray(rf_value).flatten()[0]
    return float(rf_value)
def get_close_series_from_yf(ticker, start=None, end=None, auto_adjust=True):
    """Robustly download a single ticker and return a Series of Close prices."""
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, auto_adjust=auto_adjust, progress=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    # Standard single-level:
    if 'Close' in df.columns and not isinstance(df['Close'], pd.DataFrame):
        s = df['Close'].copy(); s.name = ticker; return s
    # Close as DataFrame:
    if 'Close' in df.columns and isinstance(df['Close'], pd.DataFrame):
        if ticker in df['Close'].columns:
            s = df['Close'][ticker].copy()
        else:
            s = df['Close'].squeeze().copy()
        s.name = ticker
        return s
    # MultiIndex fallback:
    if isinstance(df.columns, pd.MultiIndex):
        try:
            s = df[('Close', ticker)].copy(); s.name = ticker; return s
        except Exception:
            pass
        close_cols = [c for c in df.columns if isinstance(c, tuple) and c[0] == 'Close']
        if close_cols:
            s = df[close_cols[0]].copy()
            if isinstance(s, pd.DataFrame): s = s.squeeze()
            s.name = ticker
            return s
    # Last resort
    if isinstance(df, pd.Series):
        s = df.copy(); s.name = ticker; return s
    try:
        s = df['Close'].squeeze().copy(); s.name = ticker; return s
    except Exception:
        raise RuntimeError(f"Unable to extract Close series for {ticker}")
def fetch_vix_series_or_none(start_date, end_date):
    """尝试抓取 ^VIX 日收盘；失败则返回 None。"""
    try:
        vix = get_close_series_from_yf('^VIX', start=start_date, end=end_date, auto_adjust=False)
        vix = pd.to_numeric(vix, errors='coerce').dropna()
        return vix
    except Exception:
        return None
def load_prices(args):
    # QQQ
    if args.qqq_csv and os.path.exists(args.qqq_csv):
        q = pd.read_csv(args.qqq_csv, parse_dates=['Date']).set_index('Date')
        qqq = (q['Adj Close'] if 'Adj Close' in q.columns else q['Close']).astype(float)
        qqq.name = 'QQQ'
    else:
        qqq = get_close_series_from_yf('QQQ', start=args.start, end=args.end, auto_adjust=True)
    # TQQQ
    if args.tqqq_csv and os.path.exists(args.tqqq_csv):
        t = pd.read_csv(args.tqqq_csv, parse_dates=['Date']).set_index('Date')
        tqqq = (t['Adj Close'] if 'Adj Close' in t.columns else t['Close']).astype(float)
        tqqq.name = 'TQQQ'
    else:
        tqqq = get_close_series_from_yf('TQQQ', start=args.start, end=args.end, auto_adjust=True)
    # QQQ5 (real or synthesized from QQQ as daily 5x)
    synth5 = False
    if args.qqq5_csv and os.path.exists(args.qqq5_csv):
        v = pd.read_csv(args.qqq5_csv, parse_dates=['Date']).set_index('Date')
        qqq5 = (v['Adj Close'] if 'Adj Close' in v.columns else v['Close']).astype(float)
        qqq5.name = 'QQQ5'
    else:
        qqq5, real_used = build_qqq5_hybrid_from_base(
            qqq_close=qqq,
            start=args.start,
            end=args.end
        )
        synth5 = not real_used
    df = pd.concat([qqq, tqqq, qqq5], axis=1).dropna()
    return df, synth5
def fetch_rf_series_or_default(start_date, end_date):
    """Return BusinessDay-indexed rf series (^IRX close/100) with ffill; None on failure."""
    try:
        import yfinance as yf
        rng = pd.date_range(start=start_date, end=end_date, freq='B')
        irx = yf.download('^IRX', start=start_date, end=end_date, progress=False, auto_adjust=False)['Close'].dropna()/100.0
        rf = irx.reindex(rng).ffill()
        if len(rf.dropna()) == 0:
            return None
        return rf
    except Exception:
        return None
def load_sofr_series(start_date, end_date):
    """Download SOFR from FRED when available."""
    try:
        import pandas_datareader.data as pdr
    except Exception:
        return None
    try:
        start = pd.to_datetime(start_date) if start_date else None
        end = pd.to_datetime(end_date) if end_date else None
        series = pdr.DataReader('SOFR', 'fred', start, end)
    except Exception:
        return None
    if series.empty:
        return None
    sof = pd.to_numeric(series.iloc[:, 0], errors='coerce').dropna()
    if sof.empty:
        return None
    sof.name = 'SOFR'
    return sof
def fetch_tqqq_adv_series(start, end, lookback=20):
    try:
        import yfinance as yf
        df = yf.download('TQQQ', start=start, end=end, auto_adjust=False, progress=False)
        if df is None or df.empty:
            return None
        if 'Adj Close' in df.columns:
            px = pd.to_numeric(df['Adj Close'], errors='coerce')
        else:
            px = pd.to_numeric(df['Close'], errors='coerce')
        vol = pd.to_numeric(df.get('Volume'), errors='coerce')
        dollar = (px * vol).dropna()
        adv = dollar.rolling(lookback).mean().dropna()
        if adv.empty:
            return None
        adv.name = 'TQQQ_ADV'
        return adv
    except Exception:
        return None
def _simulate_airbag_day(r_under, leverage, fee, trigger):
    if trigger is None or r_under > -trigger:
        return leverage * r_under - fee
    r1 = -trigger
    ret1 = leverage * r1 - fee
    if (1.0 + r1) <= 0.0:
        return -1.0
    r2 = (1.0 + r_under) / (1.0 + r1) - 1.0
    ret2 = leverage * r2 - fee
    return (1.0 + ret1) * (1.0 + ret2) - 1.0
def synth_leveraged_from_base(base_close, leverage, fee_bps=QQQ5_FLAT_FEE_BPS, financing_rate=None, management_fee_bps=QQQ5_MANAGEMENT_FEE_BPS, financing_spread_bps=QQQ5_FINANCING_SPREAD_BPS, airbag_trigger=QQQ5_AIRBAG_TRIGGER):
    if base_close is None or len(base_close) == 0:
        return pd.Series(dtype=float)
    base = pd.to_numeric(base_close, errors='coerce').dropna()
    if base.empty:
        return pd.Series(dtype=float)
    ret_under = base.pct_change().fillna(0.0)
    idx = ret_under.index
    if financing_rate is not None:
        sof = pd.to_numeric(financing_rate, errors='coerce').reindex(idx).ffill().bfill()
        mgmt_daily = (management_fee_bps / 1e4) / 252.0
        spread_daily = (financing_spread_bps / 1e4) / 252.0
        fin_daily = (sof / 100.0) / 252.0 + spread_daily
        daily_fee = mgmt_daily + max(float(leverage) - 1.0, 0.0) * fin_daily
    else:
        daily_fee = pd.Series((fee_bps / 1e4) / 252.0, index=idx)
    daily_fee = pd.to_numeric(daily_fee, errors='coerce').reindex(idx).ffill().bfill().fillna(0.0)
    lev = float(leverage)
    trigger = float(airbag_trigger) if airbag_trigger is not None else None
    lever_ret = []
    for r, fee in zip(ret_under.values, daily_fee.values):
        if np.isnan(r) or np.isnan(fee):
            lever_ret.append(0.0); continue
        if trigger is not None and r <= -trigger:
            lever_ret.append(_simulate_airbag_day(float(r), lev, float(fee), trigger))
        else:
            lever_ret.append(lev * float(r) - float(fee))
    lever_ret = pd.Series(lever_ret, index=idx)
    nav = (1.0 + lever_ret).cumprod()
    base0 = float(base.iloc[0]) if len(base) else 1.0
    nav = nav * base0
    nav.name = 'QQQ5'
    return nav
def _first_available_qqq5_series(start, end):
    for ticker in ALT_QQQ5_TICKERS:
        try:
            series = get_close_series_from_yf(ticker, start=start, end=end, auto_adjust=True)
        except Exception:
            continue
        series = pd.to_numeric(series, errors='coerce')
        if series.dropna().empty:
            continue
        return series, ticker
    return None, None
def build_qqq5_hybrid_from_base(qqq_close, start=None, end=None):
    qqq_series = pd.to_numeric(qqq_close, errors='coerce').dropna() if qqq_close is not None else pd.Series(dtype=float)
    if qqq_series.empty:
        return pd.Series(dtype=float), False
    sofr = load_sofr_series(start, end)
    synth = synth_leveraged_from_base(qqq_series, leverage=5.0, fee_bps=QQQ5_FLAT_FEE_BPS, financing_rate=sofr, management_fee_bps=QQQ5_MANAGEMENT_FEE_BPS, financing_spread_bps=QQQ5_FINANCING_SPREAD_BPS, airbag_trigger=QQQ5_AIRBAG_TRIGGER)
    real, ticker = _first_available_qqq5_series(start, end)
    if real is None:
        print('Warning: QQQ5 live prices unavailable; using synthetic series only.')
        return synth, False
    real = pd.to_numeric(real, errors='coerce')
    if real.dropna().empty:
        print('Warning: QQQ5 live prices contained no usable data; using synthetic series only.')
        return synth, False
    idx = synth.index.union(real.index).sort_values()
    synth_scaled = synth.reindex(idx).ffill()
    real = real.reindex(idx)
    switch = real.first_valid_index()
    if switch is None:
        print('Warning: QQQ5 live prices missing valid entries; using synthetic series only.')
        return synth, False
    synth_value = synth_scaled.loc[switch]
    real_value = real.loc[switch]
    scale = 1.0
    if pd.notna(real_value) and pd.notna(synth_value) and float(synth_value) != 0.0:
        scale = float(real_value) / float(synth_value)
        synth_scaled = synth_scaled * scale
    hybrid = synth_scaled.copy()
    hybrid.loc[switch:] = real.loc[switch:].fillna(synth_scaled.loc[switch:])
    hybrid.name = 'QQQ5'
    try:
        switch_date = switch.date()
    except AttributeError:
        switch_date = switch
    print(f"QQQ5 hybrid uses live data starting {switch_date} from {ticker}.")
    return hybrid, True
# ------------------ Feature Engineering (for DL) ------------------
def create_technical_features(df, window=20):
    px = df.copy()
    features = pd.DataFrame(index=px.index)
    # prices
    features['qqq_price']  = px['QQQ']
    features['tqqq_price'] = px['TQQQ']
    features['qqq5_price'] = px['QQQ5']
    # returns
    features['qqq_ret']  = px['QQQ'].pct_change()
    features['tqqq_ret'] = px['TQQQ'].pct_change()
    features['qqq5_ret'] = px['QQQ5'].pct_change()
    # MA ratios
    for n in (5,10,20):
        features[f'qqq_ma{n}']  = px['QQQ'].rolling(n).mean()  / px['QQQ']
        features[f'tqqq_ma{n}'] = px['TQQQ'].rolling(n).mean() / px['TQQQ']
    # Volatility
    features['qqq_vol']  = px['QQQ'].pct_change().rolling(window).std()
    features['tqqq_vol'] = px['TQQQ'].pct_change().rolling(window).std()
    features['vol_ratio'] = features['tqqq_vol'] / features['qqq_vol'].replace(0, 1e-10)
    # RSI
    for name in ('QQQ','TQQQ'):
        delta = px[name].diff()
        gain = (delta.where(delta>0,0)).rolling(14).mean()
        loss = (-delta.where(delta<0,0)).rolling(14).mean()
        rs = gain / loss.replace(0,1e-10)
        features[f'{name.lower()}_rsi'] = 100 - (100/(1+rs))
    # Bollinger (QQQ)
    q_ma = px['QQQ'].rolling(window).mean()
    q_std = px['QQQ'].rolling(window).std()
    features['qqq_bb_upper'] = (px['QQQ'] - (q_ma + 2*q_std)) / (4*q_std + 1e-10)
    features['qqq_bb_lower'] = (px['QQQ'] - (q_ma - 2*q_std)) / (4*q_std + 1e-10)
    # Drawdown
    q_cummax = px['QQQ'].expanding().max()
    features['qqq_drawdown'] = (px['QQQ'] - q_cummax) / q_cummax
    # Correlation
    features['qqq_tqqq_corr'] = px['QQQ'].rolling(window).corr(px['TQQQ']).fillna(0)
    return features.ffill().fillna(0)
def dynamic_target_vol(short_vol, base_target):
    if base_target is None or base_target <= 0:
        return None
    if short_vol is None or not np.isfinite(short_vol) or short_vol <= 0:
        return float(base_target)
    low, high = 0.15, 0.30
    if short_vol <= low:
        scale = 1.6
    elif short_vol >= high:
        scale = 0.6
    else:
        scale = 1.6 - (short_vol - low) * (1.0 / (high - low))
    return float(base_target * max(0.25, scale))
def compute_regime_score(mom_short, mom_medium, vix_value):
    score = 0.0
    if np.isfinite(mom_short):
        score += 0.5 * np.tanh(np.clip(mom_short, -0.2, 0.4) * 6.0)
    if np.isfinite(mom_medium):
        score += 0.4 * np.tanh(np.clip(mom_medium, -0.2, 0.6) * 4.0)
    if vix_value is not None and np.isfinite(vix_value):
        score += 0.3 * np.clip((30.0 - vix_value) / 20.0, 0.0, 1.0)
    return float(np.clip(score, 0.0, 1.0))
def regime_leverage_cap(regime_score: float) -> float:
    if regime_score < 0.25:
        return 3.0
    elif regime_score < 0.55:
        return 4.0
    return 6.0
if TORCH_AVAILABLE:
    class DLClassifier(nn.Module):
        def __init__(self, input_dim: int):
            super().__init__()
            self.backbone = nn.Sequential(
                nn.Linear(input_dim, 128),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(64, 32),
                nn.ReLU(),
            )
            self.main_head = nn.Linear(32, 3)
            self.crash_head = nn.Linear(32, 1)

        def forward(self, x: "torch.Tensor"):
            h = self.backbone(x)
            logits_main = self.main_head(h)
            crash_logit = self.crash_head(h).squeeze(-1)
            return logits_main, crash_logit
else:
    class DLClassifier:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PyTorch is not available; DLClassifier cannot be constructed.")


def create_deep_learning_model(input_dim: int):
    model = DLClassifier(input_dim)
    if TORCH_AVAILABLE and TORCH_DEVICE is not None and torch is not None:
        model = model.to(TORCH_DEVICE)
    return model
def train_deep_learning_model(df, features, look_ahead=5, prev_thresholds=None, ema_alpha=0.2,
                              crash_tail_frac=0.05):
    """
    Crash-aware 版本的深度学习训练函数：
    - 仍然使用未来 look_ahead 天的 TQQQ 风险调整收益 score 进行三分类（差 / 中性 / 好）。
    - 额外计算“未来窗口内的最小跌幅”（min future drawdown），作为 crash_severity∈[0,1]。
    - 在 CrossEntropyLoss 的基础上，引入 sample-level 权重：
        crash_weight = 1 + 4 * crash_severity
      也就是未来窗口最大跌 0% → 权重 1.0，跌到 -50% → 权重 ~3.0。
    - 同时对最坏类别（class 0）再额外提高 class_weight，让模型更重视“提前识别大跌区间”。
    返回值和调用方式保持不变。
    """
    crash_look = 25  # trading days for crash severity / label
    if not DL_AVAILABLE or StandardScaler is None or not TORCH_AVAILABLE or torch is None:
        return None

    t_ret = df['TQQQ'].pct_change()
    fut_ret = df['TQQQ'].pct_change(look_ahead).shift(-look_ahead)
    fut_vol = t_ret.rolling(look_ahead).std().shift(-look_ahead) * np.sqrt(252.0)
    score = fut_ret / (fut_vol.replace(0, 1e-8) + 1e-8)
    score = score.replace([np.inf, -np.inf], np.nan)
    if look_ahead > 0 and len(score) > look_ahead:
        score.iloc[-look_ahead:] = np.nan

    prices = pd.to_numeric(df['TQQQ'], errors='coerce')
    min_dd = pd.Series(np.nan, index=prices.index)
    look_for_dd = crash_look
    if look_for_dd > 0 and len(prices) > look_for_dd:
        arr = prices.to_numpy(dtype=float)
        n = len(arr)
        for i in range(n - look_for_dd):
            base = arr[i]
            if not np.isfinite(base) or base <= 0.0:
                continue
            future_slice = arr[i+1:i+1+look_for_dd]
            if future_slice.size == 0:
                continue
            rel = future_slice / base - 1.0
            min_dd_val = np.nanmin(rel)
            if np.isfinite(min_dd_val):
                min_dd.iloc[i] = float(min_dd_val)
    # --- Vol-adjusted crash severity and binary label ---
    daily_ret = df['TQQQ'].pct_change()
    realized_vol = daily_ret.rolling(60).std()
    realized_vol = pd.to_numeric(realized_vol, errors='coerce')
    realized_vol = realized_vol.reindex(min_dd.index)

    raw_dd_mag = (-min_dd).clip(lower=0.0)
    eps = 1e-6
    vol_adj_score = raw_dd_mag / (realized_vol + eps)
    vol_adj_score = vol_adj_score.replace([np.inf, -np.inf], np.nan)

    sev_clean = vol_adj_score.dropna()
    if len(sev_clean) > 0:
        ref = float(np.quantile(sev_clean, 0.99))
        if not np.isfinite(ref) or ref <= 0.0:
            ref = 1.0
        crash_severity = (vol_adj_score / ref).clip(lower=0.0, upper=1.0)
    else:
        crash_severity = pd.Series(0.0, index=min_dd.index)
    crash_severity = crash_severity.fillna(0.0)

    crash_binary = pd.Series(0, index=min_dd.index, dtype=int)
    sev_clean = crash_severity.dropna()
    tail = float(crash_tail_frac)
    if not np.isfinite(tail):
        tail = 0.05
    tail = max(0.01, min(tail, 0.20))
    if len(sev_clean) > 0:
        q = 1.0 - tail
        crash_thr = float(np.quantile(sev_clean, q))
        if np.isfinite(crash_thr):
            crash_binary[crash_severity >= crash_thr] = 1
    crash_binary = crash_binary.fillna(0).astype(int)
    crash_q = None
    sev_clean = crash_severity.dropna()
    if len(sev_clean) > 0:
        crash_q = float(np.quantile(sev_clean, 1.0 - tail))

    s_clean = score.dropna()
    if len(s_clean) < 200:
        return None

    q_low_inst = float(np.percentile(s_clean, 35))
    q_high_inst = float(np.percentile(s_clean, 80))
    if not np.isfinite(q_low_inst) or not np.isfinite(q_high_inst) or q_high_inst <= q_low_inst:
        q_low_inst, q_high_inst = np.percentile(s_clean, [40, 65])

    if prev_thresholds is not None and np.all(pd.notna(prev_thresholds)):
        q_low = float((1.0 - ema_alpha) * prev_thresholds[0] + ema_alpha * q_low_inst)
        q_high = float((1.0 - ema_alpha) * prev_thresholds[1] + ema_alpha * q_high_inst)
    else:
        q_low, q_high = float(q_low_inst), float(q_high_inst)

    if not np.isfinite(q_low) or not np.isfinite(q_high) or q_high <= q_low:
        q_low, q_high = float(q_low_inst), float(q_high_inst)

    targets = pd.cut(
        score,
        bins=[-np.inf, q_low, q_high, np.inf],
        labels=[0, 1, 2],
        include_lowest=True
    )
    targets = pd.to_numeric(targets, errors='coerce').fillna(-1).astype(int)
    if crash_q is not None:
        hard_crash_mask = crash_severity >= crash_q
        if hard_crash_mask.any():
            idx_mask = targets.index.intersection(crash_severity.index[hard_crash_mask])
            if len(idx_mask) > 0:
                targets.loc[idx_mask] = 0
    valid_mask = targets >= 0
    if not valid_mask.any():
        return None

    valid_idx = targets[valid_mask].index
    X_df = features.loc[valid_idx]
    y_series = targets.loc[valid_idx]

    keep = ~np.isnan(X_df.values).any(axis=1)
    X_df = X_df.loc[keep]
    y_series = y_series.loc[keep]
    target_ret = fut_ret.loc[valid_idx][keep]
    target_score = score.loc[valid_idx][keep]
    crash_severity = crash_severity.loc[valid_idx][keep]
    crash_binary_series = crash_binary.loc[valid_idx][keep].astype(int)

    if len(X_df) < 200:
        return None

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_df.values).astype(np.float32, copy=False)
    y_array = y_series.to_numpy(dtype=np.int64, copy=False)
    y_crash_array = crash_binary_series.to_numpy(dtype=np.float32, copy=False)
    crash_weight = (1.0 + 4.0 * crash_severity.to_numpy(dtype=np.float32, copy=False))

    vc = y_series.value_counts()
    total = float(len(y_series))
    class_weight = {
        int(k): float(total / (3.0 * vc.get(k, 1.0)))
        for k in [0, 1, 2]
    }
    if 0 in class_weight:
        class_weight[0] *= 1.5

    n_samples = len(X_scaled)
    val_size = int(max(1, n_samples * 0.2))
    if n_samples < 50 or val_size >= n_samples:
        val_size = 0
    train_end = n_samples - val_size
    if train_end <= 0:
        return None

    device = TORCH_DEVICE or torch.device("cpu")
    model = create_deep_learning_model(X_scaled.shape[1])

    weight_tensor = torch.tensor(
        [class_weight.get(0, 1.0),
         class_weight.get(1, 1.0),
         class_weight.get(2, 1.0)],
        dtype=torch.float32,
        device=device
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    lambda_crash = 0.7
    X_train = torch.from_numpy(X_scaled[:train_end]).to(device)
    y_train = torch.from_numpy(y_array[:train_end]).to(device)
    y_crash_train = torch.from_numpy(y_crash_array[:train_end]).to(device)
    w_train = torch.from_numpy(crash_weight[:train_end]).to(device)
    train_dataset = TensorDataset(X_train, y_train, y_crash_train, w_train)
    train_loader = DataLoader(train_dataset, batch_size=64, shuffle=False)

    if val_size > 0:
        X_val = torch.from_numpy(X_scaled[train_end:]).to(device)
        y_val = torch.from_numpy(y_array[train_end:]).to(device)
        y_crash_val = torch.from_numpy(y_crash_array[train_end:]).to(device)
        w_val = torch.from_numpy(crash_weight[train_end:]).to(device)
    else:
        X_val = y_val = y_crash_val = w_val = None

    best_state = copy.deepcopy(model.state_dict())
    best_val_loss = float('inf')
    patience = 4
    epochs_no_improve = 0
    max_epochs = 35

    for epoch in range(max_epochs):
        model.train()
        train_loss = 0.0
        for batch_X, batch_y, batch_y_crash, batch_w in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits_main, crash_logits = model(batch_X)
            loss_cls = F.cross_entropy(
                logits_main,
                batch_y,
                weight=weight_tensor,
                reduction="none"
            )
            y_crash_float = batch_y_crash.float()
            loss_crash = F.binary_cross_entropy_with_logits(
                crash_logits,
                y_crash_float,
                reduction="none"
            )
            loss_raw = loss_cls + lambda_crash * loss_crash
            loss = (loss_raw * batch_w).mean()
            loss.backward()
            optimizer.step()
            train_loss += float(loss.item()) * len(batch_X)
        train_loss /= max(1, len(train_dataset))

        val_loss = train_loss
        if X_val is not None and y_val is not None and y_crash_val is not None:
            model.eval()
            with torch.no_grad():
                logits_main_val, crash_logits_val = model(X_val)
                y_crash_val_float = y_crash_val.float()
                if w_val is not None:
                    loss_cls_val = F.cross_entropy(
                        logits_main_val,
                        y_val,
                        weight=weight_tensor,
                        reduction="none"
                    )
                    loss_crash_val = F.binary_cross_entropy_with_logits(
                        crash_logits_val,
                        y_crash_val_float,
                        reduction="none"
                    )
                    loss_raw_val = loss_cls_val + lambda_crash * loss_crash_val
                    loss_val = (loss_raw_val * w_val).mean()
                else:
                    loss_cls_val = F.cross_entropy(
                        logits_main_val,
                        y_val,
                        weight=weight_tensor,
                        reduction="mean"
                    )
                    loss_crash_val = F.binary_cross_entropy_with_logits(
                        crash_logits_val,
                        y_crash_val_float,
                        reduction="mean"
                    )
                    loss_val = loss_cls_val + lambda_crash * loss_crash_val
                val_loss = float(loss_val.item())

        if val_loss + 1e-6 < best_val_loss:
            best_val_loss = val_loss
            best_state = copy.deepcopy(model.state_dict())
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve > patience:
                break

    model.load_state_dict(best_state)
    model.eval()

    def predict_fn(np_inputs: np.ndarray) -> np.ndarray:
        model.eval()
        with torch.no_grad():
            tensor = torch.as_tensor(np_inputs, dtype=torch.float32, device=device)
            logits_main, crash_logits = model(tensor)
            probs_main = torch.softmax(logits_main, dim=-1)
            crash_prob = torch.sigmoid(crash_logits).unsqueeze(-1)
            combined = torch.cat([probs_main, crash_prob], dim=-1)
            return combined.cpu().numpy()

    predict_fn(np.zeros((1, X_scaled.shape[1]), dtype=np.float32))

    class_returns = []
    class_scores = []
    class_counts = []
    for cls in (0, 1, 2):
        mask = (y_series == cls)
        class_counts.append(int(mask.sum()))
        if mask.any():
            mean_ret = float(np.nan_to_num(target_ret[mask].mean(), nan=0.0))
            mean_score = float(np.nan_to_num(target_score[mask].mean(), nan=0.0))
        else:
            mean_ret = 0.0
            mean_score = 0.0
        class_returns.append(mean_ret)
        class_scores.append(mean_score)

    class_stats = {
        "thresholds": (q_low, q_high),
        "class_returns": class_returns,
        "class_scores": class_scores,
        "class_counts": class_counts
    }
    return model, scaler, predict_fn, class_stats
# ------------------ Risk Gate & Contextual Bandit ------------------
def realized_vol_series(s, lookback=20):
    r = s.pct_change()
    return r.rolling(lookback).std() * np.sqrt(252.0)
def compute_context_vector(i, df, qqq_ath, roll_mu, roll_sigma, vix_series=None):
    """生成一个简单的上下文特征向量（归一化处理）。"""
    q = df['QQQ']
    t = df['TQQQ']
    r_q = q.pct_change().fillna(0.0)
    # 基特征
    mom_1m = (q.iloc[i]/q.iloc[max(0, i-21)]) - 1.0 if i>=21 else 0.0
    mom_3m = (q.iloc[i]/q.iloc[max(0, i-63)]) - 1.0 if i>=63 else 0.0
    mom_6m = (q.iloc[i]/q.iloc[max(0, i-126)]) - 1.0 if i>=126 else 0.0
    dd     = (q.iloc[i]/qqq_ath) - 1.0 if qqq_ath>0 else 0.0
    vol20  = r_q.iloc[max(0, i-20):i].std()*np.sqrt(252.0) if i>=20 else 0.0
    vol252 = r_q.iloc[max(0, i-252):i].std()*np.sqrt(252.0) if i>=252 else 0.0
    vol_ratio = (vol20/(vol252+1e-8)) if vol252>0 else 0.0
    mu_i   = float(roll_mu.iloc[i]) if not np.isnan(roll_mu.iloc[i]) else 0.0
    sg_i   = float(roll_sigma.iloc[i]) if not np.isnan(roll_sigma.iloc[i]) else 0.0
    vix = 0.0
    if vix_series is not None:
        d = df.index[i]
        try:
            v_raw = vix_series.loc[d]
        except KeyError:
            aligned = pd.to_numeric(vix_series, errors='coerce')
            try:
                aligned = aligned.sort_index()
                v_raw = aligned.asof(d)
            except Exception:
                v_raw = np.nan
        if isinstance(v_raw, (pd.Series, pd.DataFrame)):
            v_raw = v_raw.squeeze()
        try:
            vix = float(v_raw)
        except (TypeError, ValueError):
            vix = 0.0
        if not np.isfinite(vix):
            vix = 0.0
    # 归一化/裁剪
    vec = np.array([mom_1m, mom_3m, mom_6m, dd, vol20, vol_ratio, mu_i, sg_i, vix], dtype=float)
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    # 简单缩放（避免尺度过大）
    scale = np.array([0.5,0.3,0.2, 1.0, 0.1, 1.0, 0.1, 1.0, 0.02])
    return (vec * scale).astype(float)
class LinUCBBandit:
    """每个决策维度一个 LinUCB，多臂：对给定上下文选择最优臂。"""
    def __init__(self, arms, d, alpha=0.6, ridge=1.0):
        self.arms = list(arms)
        self.k = len(self.arms)
        self.d = d
        self.alpha = float(alpha)
        self.A = [np.eye(d)*ridge for _ in range(self.k)]
        self.b = [np.zeros((d,1)) for _ in range(self.k)]
        self.last_choice = None
        self.last_x = None
        self.last_p = None
    def choose(self, x):
        x = x.reshape(-1,1)
        p = []
        for j in range(self.k):
            Ainv = np.linalg.inv(self.A[j])
            theta = Ainv @ self.b[j]
            mean = float((theta.T @ x).item())
            unc  = float(np.sqrt((x.T @ Ainv @ x).item()))
            p.append(mean + self.alpha * unc)
        self.last_p = np.asarray(p, dtype=float)
        a = int(np.argmax(p))
        self.last_choice = a
        self.last_x = x
        return self.arms[a]
    def update(self, reward):
        if self.last_choice is None or self.last_x is None: 
            return
        j = self.last_choice; x = self.last_x
        self.A[j] += x @ x.T
        self.b[j] += float(reward) * x
class PolicyBrain:
    """
    策略脑：组合三个独立 bandit
      - kelly_frac ∈ {0.4, 0.6, 0.8, 1.0}
      - trigger_offset ∈ {-0.02, 0.0, +0.02} （阈值外移/内移）
      - rebound_step ∈ {0.08, 0.10, 0.12}
    并内置风控门（risk gate）在高波动/高 VIX periods 压降进攻性。
    """
    def __init__(self, d, alpha=0.6, enable=True, use_risk_gate=True):
        self.enable = enable
        self.use_risk_gate = use_risk_gate
        self.bandit_kelly   = LinUCBBandit(arms=[0.4,0.6,0.8,1.0], d=d, alpha=alpha)
        self.bandit_offset  = LinUCBBandit(arms=[-0.02,0.0,0.02], d=d, alpha=alpha)
        self.bandit_rebound = LinUCBBandit(arms=[0.08,0.10,0.12], d=d, alpha=alpha)
        self.pending_reward = 0.0  # 累计一天的奖励
        self.diag_rows = []        # bandit diagnostics
    def decide(self, context_vec, base_triggers=None):
        if not self.enable:
            return {
                "kelly_frac": 0.7,         # 原始默认
                "triggers": base_triggers if base_triggers is not None else [-0.20,-0.30,-0.35],
                "rebound_step": 0.10,
                "notes": "policy=none"
            }
        kf = self.bandit_kelly.choose(context_vec)
        off = self.bandit_offset.choose(context_vec)
        rb  = self.bandit_rebound.choose(context_vec)
        # 基础触发
        base = base_triggers if base_triggers is not None else [-0.20, -0.30, -0.35]
        triggers = [x + off for x in base]
        notes = f"bandit(kf={kf:.2f}, off={off:+.2f}, rb={rb:.2f})"
        return {
            "kelly_frac": float(kf),
            "triggers": [float(x) for x in triggers],
            "rebound_step": float(rb),
            "notes": notes
        }
    def apply_risk_gate(self, decision, risk_flag):
        """当风险门触发时，压降 kelly_frac、外移触发阈值、放宽反弹步长。"""
        if not self.use_risk_gate or not risk_flag:
            return decision
        dec = decision.copy()
        dec["kelly_frac"] = min(dec["kelly_frac"], 0.60)
        dec["triggers"] = [x + 0.02 for x in dec["triggers"]]  # 全部外移 2%
        dec["rebound_step"] = max(dec["rebound_step"], 0.12)
        dec["notes"] += " + risk_gate"
        return dec
    def record_reward(self, reward):
        """用当天实际组合收益作为回报信号，更新三个 bandit。"""
        self.bandit_kelly.update(reward)
        self.bandit_offset.update(reward)
        self.bandit_rebound.update(reward)
def _collect_bandit_diag(brain, date):
    row = [date, "policy"]
    reg = 0.0
    entries = []
    for bandit in (brain.bandit_kelly, brain.bandit_offset, brain.bandit_rebound):
        last_p = getattr(bandit, "last_p", None)
        last_choice = getattr(bandit, "last_choice", None)
        if last_p is not None and last_choice is not None and len(last_p) == len(bandit.arms):
            p_vals = last_p.astype(float).tolist()
            best = float(np.max(last_p))
            chosen = float(last_p[last_choice])
            reg += best - chosen
            choice = float(bandit.arms[last_choice]) if isinstance(bandit.arms[last_choice], (int, float)) else bandit.arms[last_choice]
        else:
            p_vals = [np.nan]*len(bandit.arms)
            choice = np.nan
        entries.append((choice, p_vals))
    for choice, p_vals in entries:
        row.append(choice)
        row.extend(p_vals)
    row.append(float(reg))
    return row
def risk_gate_signal(i, df, vix_series=None):
    """
    简易风控门：下列任一满足则触发
      - VIX > 25
      - 20日实现波动 > 1年实现波动的 1.5 倍
      - 当日 QQQ 跌幅 < -3%
    """
    q = df['QQQ']
    r = q.pct_change().fillna(0.0)
    vix_flag = False
    if vix_series is not None:
        try:
            d = df.index[i]
            vix_aln = pd.to_numeric(vix_series, errors='coerce').sort_index()
            v = vix_aln.asof(d)
            if pd.notna(v):
                vix_flag = (float(v) >= 25.0)
        except Exception:
            vix_flag = False
    vol20  = r.iloc[max(0, i-20):i].std()*np.sqrt(252.0) if i>=20 else 0.0
    vol252 = r.iloc[max(0, i-252):i].std()*np.sqrt(252.0) if i>=252 else 0.0
    vol_flag = (vol252>0 and (vol20/vol252)>=1.5)
    gap_flag = (r.iloc[i] <= -0.03)
    return bool(vix_flag or vol_flag or gap_flag)


def compute_effective_leverage_from_weights(weights_df):
    """
    Estimate effective beta exposure using the latest weights snapshot.
    Futures sleeve contributes L_base * w_fut, with TQQQ and QQQ5 carrying 3x/5x beta.
    Returns (asof_timestamp, effective_leverage).
    """
    if weights_df is None or len(weights_df) == 0:
        return None, float('nan')
    try:
        latest = weights_df.iloc[-1]
    except Exception:
        return None, float('nan')
    try:
        asof = weights_df.index[-1]
    except Exception:
        asof = None
    w_fut = float(latest.get('w_fut', 0.0))
    w_tqqq = float(latest.get('w_tqqq', 0.0))
    w_qqq5 = float(latest.get('w_qqq5', 0.0))
    L_base = float(latest.get('L_base', 0.0))
    eff = w_fut * L_base + 3.0 * w_tqqq + 5.0 * w_qqq5
    return asof, eff

# ------------------ Baseline (non-DL) backtest with PolicyBrain ------------------
def baseline_backtest(df,
                      rf_series=None,
                      rf_ann=0.02,
                      base_kelly_frac=0.7,
                      kelly_lookback_days=252*3,
                      rebalance_every_days=5,
                      fut_fin_spread=0.002,
                      tqqq_expense=0.009,
                      qqq5_expense=0.0095,
                      trade_cost_bps=1.0,
                      policy_mode='bandit',
                      bandit_alpha=0.6,
                      use_risk_gate=True,
                      vix_series=None,
                      target_vol=0.35,
                      dl_conf=0.62,
                      dl_max_daily_qqq5=0.08,
                      dl_delever_threshold=-0.004,
                      dl_delever_frac=0.05,
                      dl_max_qqq5=0.45,
                      dl_max_tqqq=0.60,
                      dl_trade_max_frac=0.20,
                      dl_cooldown=3,
                      adv_series=None,
                      slip_bps=1.0,
                      impact_k=0.0005,
                      risk_gate_max_fut_frac=0.55,
                      risk_gate_max_leverage=2.2,
                      kelly_max_step=0.25,
                      tqqq_slip_bps=1.0,
                      qqq5_slip_bps=15.0,
                      adv_series_tqqq=None,
                      adv_series_qqq5=None,
                      adv_daily_frac_cap=0.10,
                      metrics_csv_path=None,
                      crash_mode="soft_scale",
                      crash_tail_frac=0.05):

    crash_mode = (crash_mode or "soft_scale").lower()
    if crash_mode not in {"none", "monitor_only", "hard_cap", "soft_scale"}:
        crash_mode = "soft_scale"
    px = df.copy()
    ret = px.pct_change().fillna(0.0)
    idx = px.index
    # policy 初始化
    d_ctx = 9  # compute_context_vector 的特征维度
    brain = PolicyBrain(d=d_ctx, alpha=bandit_alpha, enable=(policy_mode=='bandit'), use_risk_gate=use_risk_gate)
    vix_aligned = None
    if vix_series is not None:
        vix_aligned = pd.to_numeric(vix_series, errors='coerce').reindex(px.index).ffill().bfill()
    w_fut, w_tqqq, w_qqq5 = 1.0, 0.0, 0.0
    equity = [1.0]
    w_records, trades, debug_notes = [], [], []
    qqq, tqqq, qqq5 = px['QQQ'], px['TQQQ'], px['QQQ5']
    qqq_ath = qqq.iloc[0]; qqq_trough = qqq.iloc[0]
    tqqq_ath_global = tqqq.iloc[0]; qqq5_ath_global = qqq5.iloc[0]
    next_upstep_level = None
    added_qqq5_steps = 0
    L_base = 0.0; last_rebal_day = 0
    roll_mu = ret['QQQ'].rolling(kelly_lookback_days).mean()*252.0
    roll_sigma = ret['QQQ'].rolling(kelly_lookback_days).std()*np.sqrt(252.0)
    roll_mu_full = ret['QQQ'].expanding().mean()*252.0
    roll_sigma_full = ret['QQQ'].expanding().std()*np.sqrt(252.0)
    short_vol20 = ret['QQQ'].rolling(20).std()*np.sqrt(252.0)
    # 初始策略参数（如 policy=none 则使用它）
    static_triggers = [-0.20, -0.30, -0.35]
    rebound_step = 0.10
    kelly_frac_live = base_kelly_frac
    triggers_live = list(static_triggers)
    positions_records = [(idx[0], equity[0], w_fut*equity[0], w_tqqq*equity[0], w_qqq5*equity[0], L_base, kelly_frac_live)]
    def _adv_for(sleeve, date):
        ser = adv_series_tqqq if sleeve == 'TQQQ' else adv_series_qqq5 if sleeve == 'QQQ5' else None
        if ser is None:
            ser = adv_series
        if ser is None:
            return None
        try:
            if hasattr(ser, "asof"):
                val = ser.asof(date)
            else:
                val = ser.reindex([date]).ffill().bfill().iloc[0]
        except Exception:
            return None
        try:
            val = float(val)
        except Exception:
            return None
        return val if np.isfinite(val) else None

    crash_prob = 0.0
    crash_L_cap = None
    crash_qqq5_cap = None
    crash_cap_hit_flag = 0
    qqq5_cap_hit_flag = 0
    L_base_pre_crash = 0.0

    for i in range(1, len(idx)):
        d = idx[i]
        day_traded_notional = {'TQQQ': 0.0, 'QQQ5': 0.0}
        # ATH / DD
        if qqq.iloc[i] > qqq_ath: qqq_ath = qqq.iloc[i]
        drawdown = (qqq.iloc[i]/qqq_ath) - 1.0
        if qqq.iloc[i] < qqq_trough:
            qqq_trough = qqq.iloc[i]; next_upstep_level = qqq_trough*(1.0 + rebound_step); added_qqq5_steps = 0
        if tqqq.iloc[i] > tqqq_ath_global: tqqq_ath_global = tqqq.iloc[i]
        if qqq5.iloc[i] > qqq5_ath_global: qqq5_ath_global = qqq5.iloc[i]
        rf_today = get_rf_value_for_date(rf_series, d, rf_ann)
        risk_flag = risk_gate_signal(i, px, vix_series=vix_aligned) if use_risk_gate else False
        mom3 = ret['QQQ'].iloc[max(0, i-3):i].mean() if i >= 3 else ret['QQQ'].iloc[:i].mean()
        mom10 = ret['QQQ'].iloc[max(0, i-10):i].mean() if i >= 10 else ret['QQQ'].iloc[:i].mean()
        mom21 = (qqq.iloc[i]/qqq.iloc[max(0, i-21)] - 1.0) if i >= 21 else 0.0
        mom63 = (qqq.iloc[i]/qqq.iloc[max(0, i-63)] - 1.0) if i >= 63 else 0.0
        vix_today = None
        if vix_series is not None and d in vix_series.index:
            try:
                vix_today = float(vix_series.loc[d])
            except Exception:
                vix_today = None
        regime_score = compute_regime_score(mom21, mom63, vix_today if vix_today is not None else 20.0)
        cap_qqq5 = max(0.0, min(dl_max_qqq5 + 0.20*regime_score, 0.50))
        cap_tqqq = max(0.0, min(dl_max_tqqq + 0.30*regime_score, 0.95))
        # 周期 Kelly 更新 + policy 决策
        notes_today = f"risk={int(risk_flag)}"
        if (i - last_rebal_day) >= rebalance_every_days and i >= 1:
            mu_est = roll_mu.iloc[i]
            sigma_est = roll_sigma.iloc[i]
            if not np.isfinite(mu_est):
                mu_est = roll_mu_full.iloc[i]
            if not np.isfinite(sigma_est) or sigma_est <= 0:
                sigma_est = roll_sigma_full.iloc[i]
            mu = float(mu_est) if np.isfinite(mu_est) else 0.0
            sigma = float(sigma_est) if np.isfinite(sigma_est) else 0.0
            L_star = kelly_star(mu, rf_today, sigma)
            L_star = float(np.clip(L_star, 0.0, 3.0))
            # 产生上下文并由 policy 决策
            ctx = compute_context_vector(i, df=px, qqq_ath=qqq_ath, roll_mu=roll_mu, roll_sigma=roll_sigma, vix_series=vix_aligned)
            decision = brain.decide(ctx, base_triggers=static_triggers)
            # 风险门
            decision = brain.apply_risk_gate(decision, risk_flag=risk_flag)
            if policy_mode == 'bandit':
                try:
                    brain.diag_rows.append(_collect_bandit_diag(brain, d))
                except Exception:
                    pass
            # 应用
            desired_kf = decision["kelly_frac"]
            if kelly_max_step > 0.0:
                delta_kf = desired_kf - kelly_frac_live
                step = float(kelly_max_step)
                if abs(delta_kf) > step:
                    desired_kf = kelly_frac_live + math.copysign(step, delta_kf)
            kelly_frac_live = desired_kf
            triggers_live = decision["triggers"]
            rebound_step = decision["rebound_step"]
            L_base = kelly_frac_live * L_star
            L_base = min(L_base, regime_leverage_cap(float(regime_score)))
            if sigma > 1e-8 and target_vol is not None and target_vol > 0:
                dv = dynamic_target_vol(float(short_vol20.iloc[i]) if not np.isnan(short_vol20.iloc[i]) else None, target_vol)
                if dv is not None and dv > 0:
                    L_volcap = dv / float(sigma)
                    L_base = float(min(L_base, L_volcap))
            if not risk_flag:
                target_kelly = 0.8 + 0.6*regime_score
                kelly_frac_live = min(1.8, max(kelly_frac_live, target_kelly))
                target_L = 1.0 + 2.0*regime_score
                L_base = min(3.2, max(L_base, target_L))
            else:
                kelly_frac_live = min(kelly_frac_live, 0.6)
                L_base = min(L_base, 1.0)
            last_rebal_day = i
            notes_today = f"{decision['notes']}, risk={int(risk_flag)}"
        else:
            # 非再平衡日，沿用上次 decision；policy=none 时保持静态触发值
            if policy_mode == 'none':
                triggers_live = static_triggers
        # Returns + costs
        r_q  = ret['QQQ'].iloc[i]
        r_t3 = ret['TQQQ'].iloc[i] - (tqqq_expense/252.0)
        r_q5 = ret['QQQ5'].iloc[i] - (qqq5_expense/252.0)
        fut_fin_drag = (rf_today + fut_fin_spread) * max(L_base-1.0, 0.0)/252.0
        allow_aggressive = (regime_score > 0.25) and (mom3 > -0.002) and (mom10 > 0.0)
        r_fut = L_base * r_q - fut_fin_drag
        prev_eq = equity[-1]
        new_eq = prev_eq * (1.0 + w_fut*r_fut + w_tqqq*r_t3 + w_qqq5*r_q5)
        new_eq = max(new_eq, 1e-12)
        equity.append(new_eq)
        def book_trade(frm,to,frac):
            nonlocal w_fut,w_tqqq,w_qqq5, notes_today
            def available_cash():
                return max(0.0, 1.0 - (w_fut + w_tqqq + w_qqq5))
            frac = max(0.0, min(frac, 1.0))
            if frac <= 0: return 0.0
            moved = 0.0
            target_sleeve = to if to in ('TQQQ','QQQ5') else None
            if target_sleeve is not None and adv_daily_frac_cap > 0:
                adv_today = _adv_for(target_sleeve, d)
                if adv_today is not None and adv_today > 0:
                    dollar_left = max(0.0, adv_daily_frac_cap * adv_today - day_traded_notional[target_sleeve])
                    frac_left = dollar_left / max(new_eq, 1e-12)
                    if frac_left <= 1e-10:
                        notes_today = f"{notes_today} | {target_sleeve}_adv_cap_exhausted"
                        return 0.0
                    if frac_left < frac:
                        frac = frac_left
                        notes_today = f"{notes_today} | {target_sleeve}_adv_cap"
                    frac = min(frac, frac_left)
            if frm=='FUT':
                amt=min(w_fut, frac); w_fut-=amt; moved=amt
                if to=='TQQQ': w_tqqq+=amt
                elif to=='QQQ5': w_qqq5+=amt
            elif frm=='TQQQ' and to=='QQQ5':
                amt=min(w_tqqq, frac); w_tqqq-=amt; w_qqq5+=amt; moved=amt
            elif frm=='QQQ5' and to=='TQQQ':
                amt=min(w_qqq5, frac); w_qqq5-=amt; w_tqqq+=amt; moved=amt
            elif frm in ('TQQQ','QQQ5') and to=='FUT':
                if frm=='TQQQ':
                    amt=min(w_tqqq, frac); w_tqqq-=amt; w_fut+=amt; moved=amt
                else:
                    amt=min(w_qqq5, frac); w_qqq5-=amt; w_fut+=amt; moved=amt
            elif frm=='FUT' and to=='CASH':
                amt=min(w_fut, frac); w_fut-=amt; moved=amt
            elif frm=='CASH' and to=='FUT':
                cash_amt = min(available_cash(), frac)
                w_fut += cash_amt; moved = cash_amt
            if moved>0:
                if to in ('TQQQ','QQQ5'):
                    day_traded_notional[to] += moved * new_eq
                slip_used = 0.0
                if 'TQQQ' in (frm, to):
                    slip_used += tqqq_slip_bps
                if 'QQQ5' in (frm, to):
                    slip_used += qqq5_slip_bps
                if slip_used == 0.0:
                    slip_used = slip_bps
                cost = ((trade_cost_bps + slip_used)/10000.0) * moved * new_eq
                try:
                    impact_sleeve = to if to in ('TQQQ','QQQ5') else frm if frm in ('TQQQ','QQQ5') else None
                    adv_today = _adv_for(impact_sleeve, d) if impact_sleeve else None
                    if adv_today is None and adv_series is not None and impact_sleeve is None:
                        adv_today = _adv_for(None, d)
                    if adv_today is not None and adv_today > 0:
                        dollar_trade = moved * new_eq
                        impact_frac = impact_k * (dollar_trade / adv_today)
                        cost += impact_frac * dollar_trade
                except Exception:
                    pass
                equity[-1] = max(equity[-1]-cost, 1e-12)
                trades.append((d, frm, to, moved, cost))
            return moved
        def enforce_crash_caps():
            nonlocal w_fut, w_tqqq, w_qqq5, L_base, crash_cap_hit_flag, qqq5_cap_hit_flag, notes_today
            def leverage_with_base(base):
                fut_beta = max(base, 0.0) * w_fut
                return fut_beta + 3.0*w_tqqq + 5.0*w_qqq5
            beta_for_calc = L_base_pre_crash if not np.isnan(L_base_pre_crash) else L_base
            if crash_qqq5_cap is not None and crash_qqq5_cap >= 0.0:
                if w_qqq5 > crash_qqq5_cap + 1e-8:
                    qqq5_cap_hit_flag = 1
                    book_trade('QQQ5','FUT', w_qqq5 - crash_qqq5_cap)
            if crash_L_cap is None:
                return
            L_eff = leverage_with_base(beta_for_calc)
            if L_eff <= crash_L_cap + 1e-8:
                return
            crash_cap_hit_flag = 1
            if notes_today:
                notes_today = f"{notes_today} | crash_L_cap_enforced"
            else:
                notes_today = "crash_L_cap_enforced"
            if w_qqq5 > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap)/5.0)
                cut = min(w_qqq5, need)
                if cut > 1e-8:
                    book_trade('QQQ5','FUT', cut)
                    L_eff = leverage_with_base(beta_for_calc)
            if w_tqqq > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap)/3.0)
                cut = min(w_tqqq, need)
                if cut > 1e-8:
                    book_trade('TQQQ','FUT', cut)
                    L_eff = leverage_with_base(beta_for_calc)
            fut_beta = max(beta_for_calc, 1e-8)
            if w_fut > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap)/fut_beta)
                cut = min(w_fut, need)
                if cut > 1e-8:
                    book_trade('FUT','CASH', cut)
            L_base = min(L_base, crash_L_cap)
        def enforce_caps():
            if w_qqq5 > cap_qqq5 + 1e-8:
                book_trade('QQQ5', 'FUT', w_qqq5 - cap_qqq5)
            if w_tqqq > cap_tqqq + 1e-8:
                book_trade('TQQQ', 'FUT', w_tqqq - cap_tqqq)
        prev_drop = ret['QQQ'].iloc[i-1] if i > 1 else 0.0
        if (r_q <= -0.03 or (r_q <= -0.02 and prev_drop <= -0.01)) and w_qqq5 > 1e-6:
            book_trade('QQQ5', 'FUT', w_qqq5)
        if risk_flag and use_risk_gate:
            if w_qqq5 > 1e-8:
                book_trade('QQQ5', 'FUT', w_qqq5)
            if w_tqqq > 1e-8:
                book_trade('TQQQ', 'FUT', w_tqqq)
            if w_fut > risk_gate_max_fut_frac + 1e-6:
                book_trade('FUT', 'CASH', w_fut - risk_gate_max_fut_frac)
            L_base = min(L_base, risk_gate_max_leverage)
        else:
            # Regime-driven proactive allocation
            if not risk_flag:
                target_t = min(cap_tqqq, max(0.0, 0.35*regime_score))
                target_q5 = min(cap_qqq5, max(0.0, 0.25*max(regime_score-0.30, 0.0)))
                adj_limit = 0.25 * max(0.25, regime_score)
                if w_qqq5 > target_q5 + 1e-6:
                    book_trade('QQQ5', 'FUT', min(w_qqq5 - target_q5, adj_limit))
                if w_tqqq > target_t + 1e-6:
                    book_trade('TQQQ', 'FUT', min(w_tqqq - target_t, adj_limit))
                if w_tqqq < target_t - 1e-6:
                    need_t = min(target_t - w_tqqq, w_fut, adj_limit)
                    if need_t > 1e-6:
                        book_trade('FUT', 'TQQQ', need_t)
                if w_qqq5 < target_q5 - 1e-6:
                    need_q5 = min(target_q5 - w_qqq5, adj_limit)
                    if need_q5 > 1e-6 and w_tqqq > target_t:
                        give = min(need_q5, w_tqqq - target_t)
                        if give > 1e-6:
                            book_trade('TQQQ', 'QQQ5', give)
                            need_q5 -= give
                    if need_q5 > 1e-6:
                        book_trade('FUT', 'QQQ5', min(need_q5, w_fut))
            # Dip ladder (dynamic triggers)
            thr1, thr2, thr3 = triggers_live
            if drawdown <= thr3:
                book_trade('FUT','TQQQ', min(w_fut, max(0.0, 0.6 + 0.15*regime_score)))
                dd_pct = -drawdown*100.0
                steps_needed = max(0, int((dd_pct - abs(thr3)*100)//10))
                while added_qqq5_steps < steps_needed:
                    if not allow_aggressive:
                        break
                    book_trade('TQQQ','QQQ5', min(0.12 + 0.08*regime_score, max(0.0, cap_qqq5 - w_qqq5)))
                    added_qqq5_steps += 1
            elif drawdown <= thr2:
                book_trade('FUT','TQQQ', min(max(0.0, 0.20 + 0.10*regime_score), w_fut))
            elif drawdown <= thr1:
                if w_tqqq < 0.50:
                    need = min(max(0.0, 0.45 + 0.15*regime_score - w_tqqq), max(0.0, cap_tqqq - w_tqqq))
                    if need > 1e-6:
                        book_trade('FUT','TQQQ', need)
            # Recovery with ATH gate + rebound step
            if next_upstep_level is not None and qqq.iloc[i] >= next_upstep_level:
                pass_ath = True
                if w_tqqq > 1e-8 and tqqq.iloc[i] < tqqq_ath_global: pass_ath = False
                if w_qqq5 > 1e-8 and qqq5.iloc[i] < qqq5_ath_global: pass_ath = False
                if pass_ath:
                    if w_qqq5 > 1e-6:
                        book_trade('QQQ5','FUT', min(0.20, w_qqq5))
                    elif w_tqqq > 1e-6:
                        book_trade('TQQQ','FUT', min(0.20, w_tqqq))
                    next_upstep_level *= (1.0 + rebound_step)
        L_base = min(L_base, regime_leverage_cap(float(regime_score)))
        L_base_pre_crash = float(L_base)
        if crash_mode == "hard_cap":
            crash_cap_hit_flag = 0
            qqq5_cap_hit_flag = 0
            enforce_crash_caps()
        elif crash_mode == "monitor_only":
            crash_cap_hit_flag = 0
            qqq5_cap_hit_flag = 0
        enforce_caps()
        # Normalize
        tot = w_fut + w_tqqq + w_qqq5
        if tot > 0 and abs(tot-1.0) > 1e-10:
            w_fut, w_tqqq, w_qqq5 = w_fut/tot, w_tqqq/tot, w_qqq5/tot
        w_records.append((d, w_fut, w_tqqq, w_qqq5, L_base, kelly_frac_live))
        cur_eq = equity[-1]
        positions_records.append((d, cur_eq, w_fut*cur_eq, w_tqqq*cur_eq, w_qqq5*cur_eq, L_base, kelly_frac_live))
        debug_notes.append((d, notes_today, crash_mode, crash_tail_frac))
        daily_ret = float(equity[-1]/equity[-2] - 1.0) if len(equity) >= 2 else 0.0
        cum_max = max(equity)
        effective_leverage_today = float(w_fut * L_base + 3.0*w_tqqq + 5.0*w_qqq5)
        metrics = {
            "date": d.strftime("%Y-%m-%d"),
            "mode": "baseline",
            "equity": float(equity[-1]),
            "daily_ret": daily_ret,
            "cum_max_equity": float(cum_max),
            "drawdown": float(equity[-1]/cum_max - 1.0) if cum_max > 0 else 0.0,
            "tqqq_weight": float(w_tqqq),
            "qqq5_weight": float(w_qqq5),
            "fut_weight": float(w_fut),
            "cash_weight": float(max(0.0, 1.0 - (w_fut + w_tqqq + w_qqq5))),
            "effective_leverage": effective_leverage_today,
            "kelly_frac_live": float(locals().get('kelly_frac_live', base_kelly_frac)),
            "regime_score": float(locals().get('regime_score', 0.0)),
            "risk_flag": int(bool(locals().get('risk_flag', False))),
            "crash_prob": 0.0,
            "crash_L_cap": float(crash_L_cap) if crash_L_cap is not None else 0.0,
            "L_base_pre_crash": float(L_base_pre_crash),
            "crash_cap_hit": int(crash_cap_hit_flag),
            "crash_qqq5_cap": float(crash_qqq5_cap) if crash_qqq5_cap is not None else 0.0,
            "qqq5_cap_hit": int(qqq5_cap_hit_flag),
            "crash_mode": str(crash_mode),
            "crash_tail_frac": float(crash_tail_frac),
            "num_trades": int(locals().get('day_trades', 0)),
            "turnover": float(locals().get('day_turnover', 0.0)),
            "realized_pnl": float(locals().get('day_realized_pnl', 0.0)),
            "fees_and_costs": float(locals().get('day_costs', 0.0)),
            "comment": str(locals().get('notes_today', "")),
        }
        if metrics_csv_path:
            log_daily_metrics(metrics, csv_path=metrics_csv_path)
        # —— 用当日收益作为 bandit 奖励 —— #
        day_ret = daily_ret
        # 惩罚尾部（简单版）：若本日组合回撤<-5%则罚 0.5%
        penalty = 0.0
        if (equity[-1]/max(equity[:-1]) - 1.0) <= -0.05:
            penalty = 0.005
        brain.record_reward(day_ret - penalty)
    equity = pd.Series(equity, index=px.index, name='Equity')
    weights_df = pd.DataFrame(w_records, columns=['Date','w_fut','w_tqqq','w_qqq5','L_base','kelly_frac']).set_index('Date')
    notes_df = pd.DataFrame(
        debug_notes,
        columns=['Date','Notes','crash_mode','crash_tail_frac']
    ).set_index('Date')
    trades_df = pd.DataFrame(trades, columns=['Date','From','To','Fraction','TradeCost']).set_index('Date')
    positions_df = pd.DataFrame(
        positions_records,
        columns=['Date','Equity','FUT_Notional','TQQQ_Notional','QQQ5_Notional','L_base','kelly_frac']
    ).set_index('Date')
    # Metrics
    ret_eq = equity.pct_change().fillna(0.0)
    years = len(equity)/252.0
    cagr = equity.iloc[-1]**(1/years) - 1.0
    vol = ret_eq.std()*np.sqrt(252.0)
    dd = (equity/equity.cummax()) - 1.0
    maxdd = dd.min()
    sharpe = cagr/vol if vol>1e-8 else float('nan')
    calmar = cagr/abs(maxdd) if maxdd<0 else float('nan')
    # TQQQ buy&hold
    t_ret = px['TQQQ'].pct_change().fillna(0.0) - (tqqq_expense/252.0)
    t_eq = (1+t_ret).cumprod()
    t_cagr = t_eq.iloc[-1]**(1/years) - 1.0
    t_vol = t_ret.std()*np.sqrt(252.0)
    t_dd = (t_eq/t_eq.cummax()) - 1.0
    t_mdd = t_dd.min()
    t_sharpe = t_cagr/t_vol if t_vol>1e-8 else float('nan')
    t_calmar = t_cagr/abs(t_mdd) if t_mdd<0 else float('nan')
    report = {
        'CAGR': cagr, 'Vol': vol, 'Sharpe_ex_rf0': sharpe, 'MaxDD': maxdd, 'Calmar': calmar,
        'Trades': len(trades_df), 'Final_Equity': equity.iloc[-1],
        'TQQQ_CAGR': t_cagr, 'TQQQ_Vol': t_vol, 'TQQQ_Sharpe': t_sharpe,
        'TQQQ_MaxDD': t_mdd, 'TQQQ_Calmar': t_calmar, 'TQQQ_Final_Equity': t_eq.iloc[-1]
    }
    eff_asof, eff_val = compute_effective_leverage_from_weights(weights_df)
    if np.isfinite(eff_val):
        report['Effective_Leverage_Today'] = eff_val
    if eff_asof is not None:
        try:
            report['Effective_Leverage_AsOf'] = pd.Timestamp(eff_asof).strftime('%Y-%m-%d')
        except Exception:
            report['Effective_Leverage_AsOf'] = str(eff_asof)
    bandit_diag = None
    try:
        if policy_mode == 'bandit' and getattr(brain, "diag_rows", None):
            cols = (
                ['Date', 'Brain', 'choice_kf'] + [f"p_kf_{a}" for a in brain.bandit_kelly.arms] +
                ['choice_off'] + [f"p_off_{a}" for a in brain.bandit_offset.arms] +
                ['choice_rb'] + [f"p_rb_{a}" for a in brain.bandit_rebound.arms] +
                ['pseudo_regret_sum']
            )
            bandit_diag = pd.DataFrame(brain.diag_rows, columns=cols).set_index('Date')
    except Exception:
        bandit_diag = None
    return equity, dd, report, weights_df, trades_df, t_eq, notes_df, positions_df, bandit_diag, None
# ------------------ Simple MA crossover (TQQQ) ------------------
def ma_crossover_backtest(df,
                          fast=5,
                          slow=20,
                          tqqq_expense=0.009,
                          trade_cost_bps=1.0,
                          slip_bps=1.0):
    """
    Long TQQQ when fast MA > slow MA; otherwise sit in cash.
    Position is applied on the next bar (no lookahead).
    """
    px = df.copy()
    t = pd.to_numeric(px['TQQQ'], errors='coerce')
    idx = t.index
    ret_tqqq = t.pct_change().fillna(0.0) - (tqqq_expense / 252.0)
    ma_fast = t.rolling(fast).mean()
    ma_slow = t.rolling(slow).mean()
    signal = (ma_fast > ma_slow).astype(float)
    position = signal.shift(1).fillna(0.0)

    equity = [1.0]
    last_pos = 0.0
    w_records, trades, notes, positions_records = [], [], [], []
    w_records.append((idx[0], 0.0, 0.0, 0.0, 0.0, 0.0))
    positions_records.append((idx[0], equity[0], 0.0, 0.0, 0.0, 0.0, 0.0))
    notes.append((idx[0], f"pos={int(last_pos)}"))

    for i in range(1, len(idx)):
        d = idx[i]
        pos = float(position.iloc[i]) if np.isfinite(position.iloc[i]) else 0.0
        prev_eq = equity[-1]
        daily_ret = pos * ret_tqqq.iloc[i] if np.isfinite(ret_tqqq.iloc[i]) else 0.0
        new_eq = prev_eq * (1.0 + daily_ret)
        if pos != last_pos:
            delta = abs(pos - last_pos)
            cost = delta * (trade_cost_bps + slip_bps) / 10000.0 * prev_eq
            new_eq = max(new_eq - cost, 1e-12)
            from_leg = 'CASH' if last_pos <= pos else 'TQQQ'
            to_leg = 'TQQQ' if pos > last_pos else 'CASH'
            trades.append((d, from_leg, to_leg, delta, cost))
        equity.append(max(new_eq, 1e-12))
        last_pos = pos
        w_records.append((d, 0.0, pos, 0.0, 0.0, pos))
        positions_records.append((d, new_eq, 0.0, pos * new_eq, 0.0, 0.0, pos))
        notes.append((d, f"pos={int(pos)}"))

    equity = pd.Series(equity, index=idx, name='Equity')
    dd = (equity / equity.cummax()) - 1.0
    weights_df = pd.DataFrame(
        w_records,
        columns=['Date', 'w_fut', 'w_tqqq', 'w_qqq5', 'L_base', 'kelly_frac']
    ).set_index('Date')
    trades_df = pd.DataFrame(trades, columns=['Date', 'From', 'To', 'Fraction', 'TradeCost']).set_index('Date')
    notes_df = pd.DataFrame(notes, columns=['Date', 'Notes']).set_index('Date')
    positions_df = pd.DataFrame(
        positions_records,
        columns=['Date', 'Equity', 'FUT_Notional', 'TQQQ_Notional', 'QQQ5_Notional', 'L_base', 'kelly_frac']
    ).set_index('Date')

    ret_eq = equity.pct_change().fillna(0.0)
    years = len(equity) / 252.0
    cagr = equity.iloc[-1]**(1/years) - 1.0
    vol = ret_eq.std() * np.sqrt(252.0)
    maxdd = dd.min()
    sharpe = cagr / vol if vol > 1e-8 else float('nan')
    calmar = cagr / abs(maxdd) if maxdd < 0 else float('nan')

    t_eq = (1.0 + ret_tqqq).cumprod()
    t_years = len(t_eq) / 252.0
    t_cagr = t_eq.iloc[-1]**(1/t_years) - 1.0 if t_years > 0 else np.nan
    t_vol = ret_tqqq.std() * np.sqrt(252.0)
    t_dd = (t_eq / t_eq.cummax()) - 1.0
    t_mdd = t_dd.min()
    t_sharpe = t_cagr / t_vol if t_vol > 1e-8 else float('nan')
    t_calmar = t_cagr / abs(t_mdd) if t_mdd < 0 else float('nan')

    report = {
        'CAGR': cagr, 'Vol': vol, 'Sharpe_ex_rf0': sharpe, 'MaxDD': maxdd, 'Calmar': calmar,
        'Trades': len(trades_df), 'Final_Equity': equity.iloc[-1],
        'TQQQ_CAGR': t_cagr, 'TQQQ_Vol': t_vol, 'TQQQ_Sharpe': t_sharpe,
        'TQQQ_MaxDD': t_mdd, 'TQQQ_Calmar': t_calmar, 'TQQQ_Final_Equity': t_eq.iloc[-1]
    }
    return equity, dd, report, weights_df, trades_df, t_eq, notes_df, positions_df, None, None
# ------------------ DL-enhanced backtest with the same PolicyBrain hooks ------------------
def validate_initial_train_end_for_dl(initial_train_end):
    if initial_train_end is None:
        raise ValueError(
            "initial_train_end is required for DL backtests; refusing to train on the full df."
        )
    return pd.Timestamp(initial_train_end)


def deep_learning_backtest(df,
                           rf_series=None,
                           rf_ann=0.02,
                           base_kelly_frac=0.7,
                           kelly_lookback_days=252*3,
                           rebalance_every_days=5,
                           fut_fin_spread=0.002,
                           tqqq_expense=0.009,
                           qqq5_expense=0.0095,
                           trade_cost_bps=1.0,
                           policy_mode='bandit',
                           bandit_alpha=0.6,
                           use_risk_gate=True,
                           vix_series=None,
                           target_vol=0.35,
                           dl_conf=0.62,
                           dl_max_daily_qqq5=0.08,
                           dl_delever_threshold=-0.004,
                           dl_delever_frac=0.05,
                           dl_max_qqq5=0.45,
                           dl_max_tqqq=0.60,
                           dl_trade_max_frac=0.20,
                           dl_cooldown=3,
                           initial_train_end=None,
                           adv_series=None,
                           slip_bps=1.0,
                           impact_k=0.0005,
                           risk_gate_max_fut_frac=0.55,
                           risk_gate_max_leverage=2.2,
                           kelly_max_step=0.25,
                           tqqq_slip_bps=1.0,
                           qqq5_slip_bps=15.0,
                           adv_series_tqqq=None,
                           adv_series_qqq5=None,
                           adv_daily_frac_cap=0.10,
                           metrics_csv_path=None,
                           crash_mode="soft_scale",
                           crash_tail_frac=0.05):

    initial_train_end_ts = validate_initial_train_end_for_dl(initial_train_end)
    crash_mode = (crash_mode or "soft_scale").lower()
    if crash_mode not in {"none", "monitor_only", "hard_cap", "soft_scale"}:
        crash_mode = "soft_scale"
    px = df.copy()
    ret = px.pct_change().fillna(0.0)
    idx = px.index
    features = create_technical_features(df)
    model, scaler, predict_fn, dl_stats = (None, None, None, None)
    dl_retrain_log = []
    if DL_AVAILABLE:
        train_df = df.loc[:initial_train_end_ts]
        if len(train_df) < 252 * 4:
            print(
                "DL: initial training skipped; "
                f"initial_train_end={initial_train_end_ts.date()} leaves only "
                f"{len(train_df)} samples (< {252 * 4}). Using rule logic until retrain succeeds."
            )
        else:
            train_feat = features.loc[train_df.index]
            init = train_deep_learning_model(
                train_df,
                train_feat,
                prev_thresholds=None,
                crash_tail_frac=crash_tail_frac
            )
            if init:
                model, scaler, predict_fn, dl_stats = init
                print(f"DL: initial model trained through {initial_train_end_ts.date()}.")
                if dl_stats is not None:
                    th = dl_stats.get("thresholds", (np.nan, np.nan))
                    cr = dl_stats.get("class_returns", [np.nan, np.nan, np.nan])
                    cs = dl_stats.get("class_scores", [np.nan, np.nan, np.nan])
                    cc = dl_stats.get("class_counts", [0, 0, 0])
                    dl_retrain_log.append([idx[0], th[0], th[1], *(cr or []), *(cs or []), *(cc or [])])
            else:
                print("DL: initial training unavailable; will use rule logic until retrain succeeds.")
    else:
        print("DL: not available; using rules.")
    # policy 初始化
    d_ctx = 9
    brain = PolicyBrain(d=d_ctx, alpha=bandit_alpha, enable=(policy_mode=='bandit'), use_risk_gate=use_risk_gate)
    vix_aligned = None
    if vix_series is not None:
        vix_aligned = pd.to_numeric(vix_series, errors='coerce').reindex(px.index).ffill().bfill()
    w_fut, w_tqqq, w_qqq5 = 1.0, 0.0, 0.0
    equity = [1.0]
    w_records, trades, debug_notes = [], [], []
    qqq, tqqq, qqq5 = px['QQQ'], px['TQQQ'], px['QQQ5']
    qqq_ath = qqq.iloc[0]; qqq_trough = qqq.iloc[0]
    tqqq_ath_global = tqqq.iloc[0]; qqq5_ath_global = qqq5.iloc[0]
    next_upstep_level = None
    added_qqq5_steps = 0
    L_base = 0.0; last_rebal_day = 0; last_retrain_day = -10**9
    roll_mu = ret['QQQ'].rolling(kelly_lookback_days).mean()*252.0
    roll_sigma = ret['QQQ'].rolling(kelly_lookback_days).std()*np.sqrt(252.0)
    roll_mu_full = ret['QQQ'].expanding().mean()*252.0
    roll_sigma_full = ret['QQQ'].expanding().std()*np.sqrt(252.0)
    short_vol20 = ret['QQQ'].rolling(20).std()*np.sqrt(252.0)
    static_triggers = [-0.20, -0.30, -0.35]
    rebound_step = 0.10
    kelly_frac_live = base_kelly_frac
    triggers_live = list(static_triggers)
    positions_records = [(idx[0], equity[0], w_fut*equity[0], w_tqqq*equity[0], w_qqq5*equity[0], L_base, kelly_frac_live)]
    risk_cooldown_left = 0
    crash_prob_history = []
    crash_quantile_window = 252
    crash_quantile_min = 60
    crash_mild_quantile = 0.90
    crash_severe_quantile = 0.97
    crash_mild_fallback = 0.59
    crash_severe_fallback = 0.78
    crash_mild_caps = (1.8, 0.10)
    crash_severe_caps = (1.5, 0.0)
    def _adv_for(sleeve, date):
        ser = adv_series_tqqq if sleeve == 'TQQQ' else adv_series_qqq5 if sleeve == 'QQQ5' else None
        if ser is None:
            ser = adv_series
        if ser is None:
            return None
        try:
            if hasattr(ser, "asof"):
                val = ser.asof(date)
            else:
                val = ser.reindex([date]).ffill().bfill().iloc[0]
        except Exception:
            return None
        try:
            val = float(val)
        except Exception:
            return None
        return val if np.isfinite(val) else None

    for i in range(1, len(idx)):
        d = idx[i]
        day_traded_notional = {'TQQQ': 0.0, 'QQQ5': 0.0}
        risk_cooldown_left = max(0, risk_cooldown_left - 1)
        # ATH / DD
        if qqq.iloc[i] > qqq_ath: qqq_ath = qqq.iloc[i]
        drawdown = (qqq.iloc[i]/qqq_ath) - 1.0
        if qqq.iloc[i] < qqq_trough:
            qqq_trough = qqq.iloc[i]; next_upstep_level = qqq_trough*(1.0 + rebound_step); added_qqq5_steps = 0
        if tqqq.iloc[i] > tqqq_ath_global: tqqq_ath_global = tqqq.iloc[i]
        if qqq5.iloc[i] > qqq5_ath_global: qqq5_ath_global = qqq5.iloc[i]
        rf_today = get_rf_value_for_date(rf_series, d, rf_ann)
        risk_flag = risk_gate_signal(i, px, vix_series=vix_aligned) if use_risk_gate else False
        mom3 = ret['QQQ'].iloc[max(0, i-3):i].mean() if i >= 3 else ret['QQQ'].iloc[:i].mean()
        mom10 = ret['QQQ'].iloc[max(0, i-10):i].mean() if i >= 10 else ret['QQQ'].iloc[:i].mean()
        mom21 = (qqq.iloc[i]/qqq.iloc[max(0, i-21)] - 1.0) if i >= 21 else 0.0
        mom63 = (qqq.iloc[i]/qqq.iloc[max(0, i-63)] - 1.0) if i >= 63 else 0.0
        vix_today = None
        if vix_series is not None and d in vix_series.index:
            try:
                vix_today = float(vix_series.loc[d])
            except Exception:
                vix_today = None
        regime_score = compute_regime_score(mom21, mom63, vix_today if vix_today is not None else 20.0)
        cap_qqq5 = max(0.0, min(dl_max_qqq5 + 0.40*regime_score, 0.75))
        cap_tqqq = max(0.0, min(dl_max_tqqq + 0.50*regime_score, 1.30))
        crash_prob = None
        crash_L_cap = None
        crash_qqq5_cap = None
        crash_cap_hit_flag = 0
        qqq5_cap_hit_flag = 0
        L_base_pre_crash = float('nan')
        crash_cap_hit_flag = 0
        qqq5_cap_hit_flag = 0
        # 周期 Kelly 更新 + policy 决策 + DL 滚动再训练
        notes_today = f"risk={int(risk_flag)}, cooldown={risk_cooldown_left}"
        if (i - last_rebal_day) >= rebalance_every_days and i >= 1:
            mu_est = roll_mu.iloc[i]
            sigma_est = roll_sigma.iloc[i]
            if not np.isfinite(mu_est):
                mu_est = roll_mu_full.iloc[i]
            if not np.isfinite(sigma_est) or sigma_est <= 0:
                sigma_est = roll_sigma_full.iloc[i]
            mu = float(mu_est) if np.isfinite(mu_est) else 0.0
            sigma = float(sigma_est) if np.isfinite(sigma_est) else 0.0
            L_star = kelly_star(mu, rf_today, sigma)
            L_star = float(np.clip(L_star, 0.0, 3.0))
            # policy 决策
            ctx = compute_context_vector(i, df=px, qqq_ath=qqq_ath, roll_mu=roll_mu, roll_sigma=roll_sigma, vix_series=vix_aligned)
            decision = brain.decide(ctx, base_triggers=static_triggers)
            decision = brain.apply_risk_gate(decision, risk_flag=risk_flag)
            if policy_mode == 'bandit':
                try:
                    brain.diag_rows.append(_collect_bandit_diag(brain, d))
                except Exception:
                    pass
            desired_kf = decision["kelly_frac"]
            if kelly_max_step > 0.0:
                delta_kf = desired_kf - kelly_frac_live
                step = float(kelly_max_step)
                if abs(delta_kf) > step:
                    desired_kf = kelly_frac_live + math.copysign(step, delta_kf)
            kelly_frac_live = desired_kf
            triggers_live = decision["triggers"]
            rebound_step = decision["rebound_step"]
            L_base = kelly_frac_live * L_star
            if sigma > 1e-8 and target_vol is not None and target_vol > 0:
                dv = dynamic_target_vol(float(short_vol20.iloc[i]) if not np.isnan(short_vol20.iloc[i]) else None, target_vol)
                if dv is not None and dv > 0:
                    L_volcap = dv / float(sigma)
                    L_base = float(min(L_base, L_volcap))
            if not risk_flag:
                target_kelly = 1.6 + 1.4*regime_score
                kelly_frac_live = min(3.0, max(kelly_frac_live, target_kelly))
                target_L = 2.5 + 5.2*regime_score
                L_base = min(6.0, max(L_base, target_L))
                L_base = min(L_base, regime_leverage_cap(float(regime_score)))
            else:
                kelly_frac_live = min(kelly_frac_live, 0.85)
                L_base = min(L_base, 1.35)
            last_rebal_day = i
            notes_today = f"{decision['notes']}, risk={int(risk_flag)}, cooldown={risk_cooldown_left}"
            # DL 滚动再训练（最多 500 日窗口）
            if DL_AVAILABLE and i >= 252 and (i - last_retrain_day) >= rebalance_every_days:
                start_idx = max(0, i - 500)
                rec_feat = features.iloc[start_idx:i+1]
                rec_df = df.iloc[start_idx:i+1]
                prev_th = None
                if dl_stats is not None:
                    prev_th = dl_stats.get("thresholds")
                upd = train_deep_learning_model(
                    rec_df,
                    rec_feat,
                    prev_thresholds=prev_th,
                    crash_tail_frac=crash_tail_frac
                )
                if upd:
                    model, scaler, predict_fn, dl_stats = upd
                    th = dl_stats.get("thresholds", (np.nan, np.nan))
                    cr = dl_stats.get("class_returns", [np.nan, np.nan, np.nan])
                    cs = dl_stats.get("class_scores", [np.nan, np.nan, np.nan])
                    cc = dl_stats.get("class_counts", [0, 0, 0])
                    dl_retrain_log.append([d, th[0], th[1], *(cr or []), *(cs or []), *(cc or [])])
                    last_retrain_day = i
        else:
            if policy_mode == 'none':
                triggers_live = static_triggers
        L_base_pre_crash = float(L_base)
        # returns + costs
        r_q  = ret['QQQ'].iloc[i]
        r_t3 = ret['TQQQ'].iloc[i] - (tqqq_expense/252.0)
        r_q5 = ret['QQQ5'].iloc[i] - (qqq5_expense/252.0)
        fut_fin_drag = (rf_today + fut_fin_spread) * max(L_base-1.0, 0.0)/252.0
        allow_aggressive = (regime_score > 0.30) and (mom3 > -0.002) and (mom10 > 0.0)
        r_fut = L_base * r_q - fut_fin_drag
        prev_eq = equity[-1]
        new_eq = prev_eq*(1.0 + w_fut*r_fut + w_tqqq*r_t3 + w_qqq5*r_q5)
        new_eq = max(new_eq, 1e-12)
        equity.append(new_eq)
        def book_trade(frm,to,frac):
            nonlocal w_fut,w_tqqq,w_qqq5, notes_today
            def available_cash():
                return max(0.0, 1.0 - (w_fut + w_tqqq + w_qqq5))
            frac = max(0.0, min(frac, 1.0))
            if frac <= 0: return 0.0
            target_sleeve = to if to in ('TQQQ','QQQ5') else None
            if target_sleeve is not None and adv_daily_frac_cap > 0:
                adv_today = _adv_for(target_sleeve, d)
                if adv_today is not None and adv_today > 0:
                    dollar_left = max(0.0, adv_daily_frac_cap * adv_today - day_traded_notional[target_sleeve])
                    frac_left = dollar_left / max(new_eq, 1e-12)
                    if frac_left <= 1e-10:
                        notes_today = f"{notes_today} | {target_sleeve}_adv_cap_exhausted"
                        return 0.0
                    if frac_left < frac:
                        frac = frac_left
                        notes_today = f"{notes_today} | {target_sleeve}_adv_cap"
            moved=0.0
            if frm=='FUT':
                amt=min(w_fut, frac); w_fut-=amt; moved=amt
                if to=='TQQQ': w_tqqq+=amt
                elif to=='QQQ5': w_qqq5+=amt
            elif frm=='TQQQ' and to=='QQQ5':
                amt=min(w_tqqq, frac); w_tqqq-=amt; w_qqq5+=amt; moved=amt
            elif frm=='QQQ5' and to=='TQQQ':
                amt=min(w_qqq5, frac); w_qqq5-=amt; w_tqqq+=amt; moved=amt
            elif frm in ('TQQQ','QQQ5') and to=='FUT':
                if frm=='TQQQ':
                    amt=min(w_tqqq, frac); w_tqqq-=amt; w_fut+=amt; moved=amt
                else:
                    amt=min(w_qqq5, frac); w_qqq5-=amt; w_fut+=amt; moved=amt
            elif frm=='FUT' and to=='CASH':
                amt=min(w_fut, frac); w_fut-=amt; moved=amt
            elif frm=='CASH' and to=='FUT':
                cash_amt = min(available_cash(), frac)
                w_fut += cash_amt; moved = cash_amt
            if moved>0:
                if to in ('TQQQ','QQQ5'):
                    day_traded_notional[to] += moved * new_eq
                slip_used = 0.0
                if 'TQQQ' in (frm, to):
                    slip_used += tqqq_slip_bps
                if 'QQQ5' in (frm, to):
                    slip_used += qqq5_slip_bps
                if slip_used == 0.0:
                    slip_used = slip_bps
                cost = ((trade_cost_bps + slip_used)/10000.0) * moved * new_eq
                try:
                    impact_sleeve = to if to in ('TQQQ','QQQ5') else frm if frm in ('TQQQ','QQQ5') else None
                    adv_today = _adv_for(impact_sleeve, d) if impact_sleeve else None
                    if adv_today is None and adv_series is not None and impact_sleeve is None:
                        adv_today = _adv_for(None, d)
                    if adv_today is not None and adv_today > 0:
                        dollar_trade = moved * new_eq
                        impact_frac = impact_k * (dollar_trade / adv_today)
                        cost += impact_frac * dollar_trade
                except Exception:
                    pass
                equity[-1] = max(equity[-1]-cost, 1e-12)
                trades.append((d, frm, to, moved, cost))
            return moved
        def enforce_crash_caps():
            nonlocal w_fut, w_tqqq, w_qqq5, L_base, crash_cap_hit_flag, qqq5_cap_hit_flag, notes_today
            beta_for_calc = L_base_pre_crash if np.isfinite(L_base_pre_crash) else L_base
            beta_for_calc = max(beta_for_calc, 0.0)
            def current_leverage():
                return beta_for_calc * w_fut + 3.0*w_tqqq + 5.0*w_qqq5
            if crash_qqq5_cap is not None and crash_qqq5_cap >= 0.0:
                if w_qqq5 > crash_qqq5_cap + 1e-8:
                    book_trade('QQQ5','FUT', w_qqq5 - crash_qqq5_cap)
                    qqq5_cap_hit_flag = 1
            if crash_L_cap is None:
                return
            L_eff = current_leverage()
            if L_eff <= crash_L_cap + 1e-8:
                return
            crash_cap_hit_flag = 1
            if notes_today:
                notes_today = f"{notes_today} | crash_L_cap_enforced"
            else:
                notes_today = "crash_L_cap_enforced"
            if w_qqq5 > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap) / 5.0)
                cut = min(w_qqq5, need)
                if cut > 1e-8:
                    book_trade('QQQ5','FUT', cut)
                    L_eff = current_leverage()
            if w_tqqq > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap) / 3.0)
                cut = min(w_tqqq, need)
                if cut > 1e-8:
                    book_trade('TQQQ','FUT', cut)
                    L_eff = current_leverage()
            fut_beta = max(beta_for_calc, 1e-8)
            if w_fut > 1e-8 and L_eff > crash_L_cap + 1e-8:
                need = max(0.0, (L_eff - crash_L_cap) / fut_beta)
                cut = min(w_fut, need)
                if cut > 1e-8:
                    book_trade('FUT','CASH', cut)
            L_base = min(L_base, crash_L_cap)
        def enforce_caps():
            if w_qqq5 > cap_qqq5 + 1e-8:
                book_trade('QQQ5', 'FUT', w_qqq5 - cap_qqq5)
            if w_tqqq > cap_tqqq + 1e-8:
                book_trade('TQQQ', 'FUT', w_tqqq - cap_tqqq)
        prev_drop = ret['QQQ'].iloc[i-1] if i > 1 else 0.0
        if (r_q <= -0.03 or (r_q <= -0.02 and prev_drop <= -0.01)) and w_qqq5 > 1e-6:
            book_trade('QQQ5','FUT', w_qqq5)
        dv_now = dynamic_target_vol(float(short_vol20.iloc[i]) if not np.isnan(short_vol20.iloc[i]) else None, target_vol)
        if dv_now is not None and not np.isnan(roll_sigma.iloc[i]) and roll_sigma.iloc[i] > 1e-8:
            L_cap_now = float(dv_now) / float(roll_sigma.iloc[i])
        else:
            L_cap_now = float('inf')
        vol_cap_block = np.isfinite(L_cap_now) and L_base >= 0.95 * L_cap_now
        dl_log = None
        if model is not None and scaler is not None and predict_fn is not None and dl_stats is not None:
            try:
                cur_feat = features.loc[d].values.reshape(1, -1)
                cur_scaled = scaler.transform(cur_feat).astype(np.float32, copy=False)
                pred_vec = predict_fn(cur_scaled)[0]
                if pred_vec.size >= 4:
                    probs = pred_vec[:3]
                    crash_prob = float(pred_vec[3])
                else:
                    probs = pred_vec
                    crash_prob = float(pred_vec[0]) if pred_vec.size > 0 else 0.0
                crash_L_cap = None
                crash_qqq5_cap = None
                soft_scale_risk = 1.0
                if crash_mode in ("hard_cap", "monitor_only"):
                    hist = crash_prob_history[-crash_quantile_window:]
                    mild_thr = crash_mild_fallback
                    severe_thr = crash_severe_fallback
                    if hist:
                        hist_arr = np.asarray(hist, dtype=float)
                        if hist_arr.size >= crash_quantile_min:
                            mild_thr = float(np.quantile(hist_arr, crash_mild_quantile))
                            severe_thr = float(np.quantile(hist_arr, crash_severe_quantile))
                    low_q = float(mild_thr)
                    high_q = float(severe_thr)
                    if not np.isfinite(low_q):
                        low_q = crash_mild_fallback
                    if not np.isfinite(high_q):
                        high_q = crash_severe_fallback
                    if high_q <= low_q + 1e-6:
                        high_q = low_q + 1e-6
                    risk_scale = 1.0
                    min_scale = 1.0 / 3.0
                    if crash_prob >= low_q:
                        t = max(0.0, min((crash_prob - low_q) / (high_q - low_q), 1.0))
                        risk_scale = 1.0 - t * (1.0 - min_scale)
                        base_cap = regime_leverage_cap(float(regime_score))
                        crash_L_cap = float(risk_scale * base_cap)
                        q_t = 1.0 - t
                        crash_qqq5_cap = float(max(0.0, min(cap_qqq5 * q_t, cap_qqq5)))
                elif crash_mode == "soft_scale":
                    cp = float(np.clip(crash_prob, 0.0, 1.0))
                    pre_scale_L_base = float(L_base)
                    pre_cap_t = float(cap_tqqq)
                    pre_cap_q5 = float(cap_qqq5)
                    low = 0.30
                    high = 0.70
                    min_scale = 0.30
                    if cp <= low:
                        soft_scale_risk = 1.0
                    elif cp >= high:
                        soft_scale_risk = min_scale
                    else:
                        t = (cp - low) / (high - low)
                        soft_scale_risk = 1.0 - t * (1.0 - min_scale)
                    crash_L_cap = float(soft_scale_risk * pre_scale_L_base)
                    crash_qqq5_cap = float(soft_scale_risk * pre_cap_q5)
                    if soft_scale_risk < 0.999:
                        L_base = pre_scale_L_base * soft_scale_risk
                        cap_tqqq = pre_cap_t * soft_scale_risk
                        cap_qqq5 = pre_cap_q5 * soft_scale_risk
                        crash_cap_hit_flag = 1
                        qqq5_cap_hit_flag = int(pre_cap_q5 > crash_qqq5_cap + 1e-8)
                        note = f"crash_soft_scale={soft_scale_risk:.2f}"
                        notes_today = f"{notes_today} | {note}" if notes_today else note
                crash_prob_history.append(crash_prob)
                if len(crash_prob_history) > crash_quantile_window:
                    crash_prob_history.pop(0)
                action = int(np.argmax(probs))
                conf = float(np.max(probs))
                class_returns = np.asarray(dl_stats.get("class_returns", [0.0, 0.0, 0.0]), dtype=np.float32)
                class_scores = np.asarray(dl_stats.get("class_scores", [0.0, 0.0, 0.0]), dtype=np.float32)
                expected_ret = float(np.dot(probs, class_returns)) if class_returns.size else 0.0
                expected_score = float(np.dot(probs, class_scores)) if class_scores.size else 0.0
                action_ret = float(class_returns[action]) if action < class_returns.size else 0.0
                action_score = float(class_scores[action]) if action < class_scores.size else 0.0
                ret_thresh_t = max(0.0015, 0.22 * max(class_returns[1], 0.0)) if class_returns.size > 1 else 0.0015
                ret_thresh_q5 = max(0.0100, 0.35 * max(class_returns[2], 0.0)) if class_returns.size > 2 else 0.0100
                score_thresh_t = max(0.015, 0.40 * max(class_scores[1], 0.0)) if class_scores.size > 1 else 0.015
                score_thresh_q5 = max(0.060, 0.55 * max(class_scores[2], 0.0)) if class_scores.size > 2 else 0.060
                thresholds_ret = [-0.002, ret_thresh_t, ret_thresh_q5]
                thresholds_score = [-0.05, score_thresh_t, score_thresh_q5]
                risk_recent_block = risk_cooldown_left > 0
                too_much_kelly = (kelly_frac_live >= 1.40) or (L_base >= 3.2)
                dl_log = (
                    f"DL(action={action}, conf={conf:.2f}, exp_ret={expected_ret:.3f}, "
                    f"exp_score={expected_score:.2f}, crash_p={crash_prob:.2f}, "
                    f"kf={kelly_frac_live:.2f}, L={L_base:.2f}, cooldown={risk_cooldown_left})"
                )
                if action in (1, 2):
                    if expected_ret < thresholds_ret[action] or action_score < thresholds_score[action]:
                        if action == 2 and expected_ret >= thresholds_ret[1] and class_returns.size > 1 and class_returns[1] > 0:
                            action = 1
                            dl_log += "->downgrade1"
                        else:
                            action = 0
                            dl_log += "->gated"
                delever_noted = False
                if action == 0 and (conf >= dl_conf) and expected_ret < dl_delever_threshold and w_tqqq > 1e-6:
                    stress = min(1.0, abs(expected_ret - dl_delever_threshold) / max(1e-4, abs(dl_delever_threshold)))
                    trim = min(w_tqqq, dl_delever_frac * stress)
                    if trim > 1e-6:
                        book_trade('TQQQ', 'FUT', trim)
                        dl_log += '->delever'
                        delever_noted = True
                if (conf >= dl_conf) and (not risk_recent_block) and (not too_much_kelly) and (not risk_flag) and (not vol_cap_block):
                    max_move = 0.0
                    if action == 0:
                        pass
                    else:
                        target_ret = thresholds_ret[action]
                        signal_over = max(0.0, expected_ret - target_ret)
                        denom = max(1e-4, abs(action_ret) + abs(target_ret))
                        move_scale = (0.35 + 0.65*regime_score) * min(1.5, 0.25 + signal_over/denom)
                        max_move = float(dl_trade_max_frac) * move_scale * max(0.55, conf)
                    max5 = max(0.0, cap_qqq5 - w_qqq5)
                    max3 = max(0.0, cap_tqqq - w_tqqq)
                    if action == 2 and max5 > 1e-6 and allow_aggressive:
                        max_move = min(max_move, dl_max_daily_qqq5)
                        move_from_t = min(max_move, max5, w_tqqq)
                        if move_from_t > 1e-6:
                            book_trade('TQQQ','QQQ5', move_from_t)
                        else:
                            move_from_f = min(max_move, max5, w_fut)
                            if move_from_f > 1e-6:
                                book_trade('FUT','QQQ5', move_from_f)
                    elif action == 1 and max3 > 1e-6:
                        move_from_f = min(max_move, max3, w_fut)
                        if move_from_f > 1e-6:
                            book_trade('FUT','TQQQ', move_from_f)
            except Exception:
                dl_log = 'DL(action=error)'
        else:
            dl_log = 'DL(unavailable)'
        if dl_log:
            notes_today = f"{notes_today} | {dl_log}" if notes_today else dl_log
        if risk_flag and use_risk_gate:
            target_risk_t = min(cap_tqqq, max(0.05, 0.12 + 0.25*regime_score))
            if w_qqq5 > 1e-8:
                book_trade('QQQ5','FUT', w_qqq5)
            if w_tqqq > target_risk_t + 1e-6:
                book_trade('TQQQ','FUT', w_tqqq - target_risk_t)
            elif w_tqqq < target_risk_t - 1e-6 and w_fut > 1e-6:
                book_trade('FUT','TQQQ', min(target_risk_t - w_tqqq, w_fut))
            if w_fut > risk_gate_max_fut_frac + 1e-6:
                book_trade('FUT','CASH', w_fut - risk_gate_max_fut_frac)
            L_base = min(L_base, risk_gate_max_leverage)
        else:
            # Regime-driven proactive allocation
            if not risk_flag:
                raw_t = 0.55*regime_score + 0.20
                raw_q5 = max(0.0, 0.45*(regime_score - 0.10))
                total_raw = raw_t + raw_q5
                if total_raw > 0.95:
                    scale = 0.95 / total_raw
                    raw_t *= scale
                    raw_q5 *= scale
                target_t = min(cap_tqqq, max(0.0, raw_t))
                target_q5 = min(cap_qqq5, max(0.0, raw_q5))
                adj_limit = max(0.35, 0.55*regime_score)
                if w_qqq5 > target_q5 + 1e-6:
                    book_trade('QQQ5','FUT', min(w_qqq5 - target_q5, adj_limit))
                if w_tqqq > target_t + 1e-6:
                    book_trade('TQQQ','FUT', min(w_tqqq - target_t, adj_limit))
                if w_tqqq < target_t - 1e-6:
                    need_t = min(target_t - w_tqqq, w_fut, adj_limit)
                    if need_t > 1e-6:
                        book_trade('FUT','TQQQ', need_t)
                if w_qqq5 < target_q5 - 1e-6:
                    need_q5 = min(target_q5 - w_qqq5, adj_limit)
                    if need_q5 > 1e-6 and w_tqqq > target_t:
                        give = min(need_q5, w_tqqq - target_t)
                        if give > 1e-6:
                            book_trade('TQQQ','QQQ5', give)
                            need_q5 -= give
                    if need_q5 > 1e-6:
                        book_trade('FUT','QQQ5', min(need_q5, w_fut))

            # Dip ladder (dynamic triggers)
            thr1, thr2, thr3 = triggers_live
            if drawdown <= thr3:
                book_trade('FUT','TQQQ', min(w_fut, max(0.0, 0.6 + 0.2*regime_score)))
                dd_pct = -drawdown*100.0
                steps_needed = max(0, int((dd_pct - abs(thr3)*100)//10))
                while added_qqq5_steps < steps_needed:
                    if not allow_aggressive:
                        break
                    book_trade('TQQQ','QQQ5', min(0.15 + 0.10*regime_score, max(0.0, cap_qqq5 - w_qqq5)))
                    added_qqq5_steps += 1
            elif drawdown <= thr2:
                book_trade('FUT','TQQQ', min(max(0.0, 0.25 + 0.10*regime_score), w_fut))
            elif drawdown <= thr1:
                if w_tqqq < 0.50:
                    need = min(max(0.0, 0.50 + 0.20*regime_score - w_tqqq), max(0.0, cap_tqqq - w_tqqq))
                    if need > 1e-6:
                        book_trade('FUT','TQQQ', need)
            # Recovery with ATH gate + rebound step
            if next_upstep_level is not None and qqq.iloc[i] >= next_upstep_level:
                pass_ath = True
                if w_tqqq > 1e-8 and tqqq.iloc[i] < tqqq_ath_global: pass_ath = False
                if w_qqq5 > 1e-8 and qqq5.iloc[i] < qqq5_ath_global: pass_ath = False
                if pass_ath:
                    if w_qqq5 > 1e-6:
                        book_trade('QQQ5','FUT', min(0.20, w_qqq5))
                    elif w_tqqq > 1e-6:
                        book_trade('TQQQ','FUT', min(0.20, w_tqqq))
                    next_upstep_level *= (1.0 + rebound_step)
        L_base = min(L_base, regime_leverage_cap(float(regime_score)))
        L_base_pre_crash = float(L_base)
        crash_cap_hit_flag = 0
        qqq5_cap_hit_flag = 0
        enforce_crash_caps()
        enforce_caps()
        if risk_flag:
            risk_cooldown_left = int(max(1, dl_cooldown))
            notes_today = f"{notes_today} | cooldown_reset={risk_cooldown_left}"
        # Normalize
        tot = w_fut + w_tqqq + w_qqq5
        if tot > 0 and abs(tot-1.0) > 1e-10:
            w_fut, w_tqqq, w_qqq5 = w_fut/tot, w_tqqq/tot, w_qqq5/tot
        w_records.append((d, w_fut, w_tqqq, w_qqq5, L_base, kelly_frac_live))
        cur_eq = equity[-1]
        positions_records.append((d, cur_eq, w_fut*cur_eq, w_tqqq*cur_eq, w_qqq5*cur_eq, L_base, kelly_frac_live))
        debug_notes.append((d, notes_today))
        daily_ret = float(equity[-1]/equity[-2] - 1.0) if len(equity) >= 2 else 0.0
        cum_max = max(equity)
        effective_leverage_today = float(w_fut * L_base + 3.0*w_tqqq + 5.0*w_qqq5)
        metrics = {
            "date": d.strftime("%Y-%m-%d"),
            "mode": "dl",
            "equity": float(equity[-1]),
            "daily_ret": daily_ret,
            "cum_max_equity": float(cum_max),
            "drawdown": float(equity[-1]/cum_max - 1.0) if cum_max > 0 else 0.0,
            "tqqq_weight": float(w_tqqq),
            "qqq5_weight": float(w_qqq5),
            "fut_weight": float(w_fut),
            "cash_weight": float(max(0.0, 1.0 - (w_fut + w_tqqq + w_qqq5))),
            "effective_leverage": effective_leverage_today,
            "kelly_frac_live": float(kelly_frac_live),
            "regime_score": float(regime_score),
            "risk_flag": int(bool(risk_flag)),
            "crash_prob": float(crash_prob) if crash_prob is not None else 0.0,
            "crash_L_cap": float(crash_L_cap) if crash_L_cap is not None else 0.0,
            "L_base_pre_crash": float(L_base_pre_crash),
            "crash_cap_hit": int(crash_cap_hit_flag),
            "crash_qqq5_cap": float(crash_qqq5_cap) if crash_qqq5_cap is not None else 0.0,
            "qqq5_cap_hit": int(qqq5_cap_hit_flag),
            "crash_mode": str(crash_mode),
            "crash_tail_frac": float(crash_tail_frac),
            "num_trades": int(locals().get('day_trades', 0)),
            "turnover": float(locals().get('day_turnover', 0.0)),
            "realized_pnl": float(locals().get('day_realized_pnl', 0.0)),
            "fees_and_costs": float(locals().get('day_costs', 0.0)),
            "comment": str(notes_today),
        }
        if metrics_csv_path:
            log_daily_metrics(metrics, csv_path=metrics_csv_path)
        # bandit 奖励更新
        day_ret = daily_ret
        penalty = 0.0
        if (equity[-1]/max(equity[:-1]) - 1.0) <= -0.05:
            penalty = 0.005
        brain.record_reward(day_ret - penalty)
    equity = pd.Series(equity, index=px.index, name='Equity')
    weights_df = pd.DataFrame(w_records, columns=['Date','w_fut','w_tqqq','w_qqq5','L_base','kelly_frac']).set_index('Date')
    notes_df = pd.DataFrame(debug_notes, columns=['Date','Notes']).set_index('Date')
    trades_df = pd.DataFrame(trades, columns=['Date','From','To','Fraction','TradeCost']).set_index('Date')
    positions_df = pd.DataFrame(
        positions_records,
        columns=['Date','Equity','FUT_Notional','TQQQ_Notional','QQQ5_Notional','L_base','kelly_frac']
    ).set_index('Date')
    # Metrics
    ret_eq = equity.pct_change().fillna(0.0)
    years = len(equity)/252.0
    cagr = equity.iloc[-1]**(1/years) - 1.0
    vol = ret_eq.std()*np.sqrt(252.0)
    dd = (equity/equity.cummax()) - 1.0
    maxdd = dd.min()
    sharpe = cagr/vol if vol>1e-8 else float('nan')
    calmar = cagr/abs(maxdd) if maxdd<0 else float('nan')
    # TQQQ buy&hold
    t_ret = px['TQQQ'].pct_change().fillna(0.0) - (tqqq_expense/252.0)
    t_eq = (1+t_ret).cumprod()
    t_cagr = t_eq.iloc[-1]**(1/years) - 1.0
    t_vol = t_ret.std()*np.sqrt(252.0)
    t_dd = (t_eq/t_eq.cummax()) - 1.0
    t_mdd = t_dd.min()
    t_sharpe = t_cagr/t_vol if t_vol>1e-8 else float('nan')
    t_calmar = t_cagr/abs(t_mdd) if t_mdd<0 else float('nan')
    report = {
        'CAGR': cagr, 'Vol': vol, 'Sharpe_ex_rf0': sharpe, 'MaxDD': maxdd, 'Calmar': calmar,
        'Trades': len(trades_df), 'Final_Equity': equity.iloc[-1],
        'TQQQ_CAGR': t_cagr, 'TQQQ_Vol': t_vol, 'TQQQ_Sharpe': t_sharpe,
        'TQQQ_MaxDD': t_mdd, 'TQQQ_Calmar': t_calmar, 'TQQQ_Final_Equity': t_eq.iloc[-1]
    }
    eff_asof, eff_val = compute_effective_leverage_from_weights(weights_df)
    if np.isfinite(eff_val):
        report['Effective_Leverage_Today'] = eff_val
    if eff_asof is not None:
        try:
            report['Effective_Leverage_AsOf'] = pd.Timestamp(eff_asof).strftime('%Y-%m-%d')
        except Exception:
            report['Effective_Leverage_AsOf'] = str(eff_asof)
    bandit_diag = None
    try:
        if policy_mode == 'bandit' and getattr(brain, "diag_rows", None):
            cols = (
                ['Date', 'Brain', 'choice_kf'] + [f"p_kf_{a}" for a in brain.bandit_kelly.arms] +
                ['choice_off'] + [f"p_off_{a}" for a in brain.bandit_offset.arms] +
                ['choice_rb'] + [f"p_rb_{a}" for a in brain.bandit_rebound.arms] +
                ['pseudo_regret_sum']
            )
            bandit_diag = pd.DataFrame(brain.diag_rows, columns=cols).set_index('Date')
    except Exception:
        bandit_diag = None
    dl_retrain_df = None
    if len(dl_retrain_log) > 0:
        cols = ['Date','q_low_ema','q_high_ema','class_ret_0','class_ret_1','class_ret_2',
                'class_score_0','class_score_1','class_score_2','cnt_0','cnt_1','cnt_2']
        dl_retrain_df = pd.DataFrame(dl_retrain_log, columns=cols).set_index('Date')
    return equity, dd, report, weights_df, trades_df, t_eq, notes_df, positions_df, bandit_diag, dl_retrain_df
# ------------------ Evaluation helpers ------------------
def run_walk_forward(df, mode, policy, step_months=3, oos_months=6, **kw):
    kw = dict(kw)
    kw.pop("initial_train_end", None)
    idx = df.index.sort_values()
    if len(idx) < 252*5:
        print("WFA skipped: not enough data.")
        return None
    rows = []
    cur = idx.min() + pd.offsets.BDay(252*4)
    last_date = idx.max()
    while cur < last_date:
        train_end = cur
        oos_end = min(last_date, (train_end + pd.DateOffset(months=oos_months)))
        df_slice = df.loc[:oos_end]
        if df_slice.empty:
            break
        if mode == 'baseline':
            res = baseline_backtest(df_slice, policy_mode=policy, **kw)
        else:
            res = deep_learning_backtest(df_slice, policy_mode=policy, initial_train_end=train_end, **kw)
        eq = res[0]
        oos_eq = eq.loc[(eq.index > train_end) & (eq.index <= oos_end)]
        if len(oos_eq) > 5:
            years = len(oos_eq) / 252.0
            total_ret = oos_eq.iloc[-1] / oos_eq.iloc[0]
            cagr = total_ret**(1/years) - 1 if years > 0 else np.nan
            dd = (oos_eq / oos_eq.cummax()) - 1.0
            rows.append({
                'mode': mode,
                'policy': policy,
                'train_end': train_end.date(),
                'oos_end': oos_end.date(),
                'CAGR': float(cagr),
                'MaxDD': float(dd.min())
            })
        cur = cur + pd.DateOffset(months=step_months)
    if not rows:
        return None
    return pd.DataFrame(rows)
def run_abtest(df, oos_start, common_kw, initial_train_end):
    rows = []
    combos = [('baseline', 'none'), ('baseline', 'bandit'), ('dl', 'bandit')]
    oos_date = pd.to_datetime(oos_start)
    common_kw = dict(common_kw)
    common_kw.pop("initial_train_end", None)
    initial_train_end = validate_initial_train_end_for_dl(initial_train_end)
    for mode, policy in combos:
        if mode == 'baseline':
            res = baseline_backtest(df, policy_mode=policy, **common_kw)
        else:
            res = deep_learning_backtest(
                df,
                policy_mode=policy,
                initial_train_end=initial_train_end,
                **common_kw
            )
        eq, dd, rep = res[0], res[1], res[2]
        oos_eq = eq.loc[eq.index >= oos_date]
        if len(oos_eq) > 1:
            years = len(oos_eq)/252.0
            total_ret = oos_eq.iloc[-1]/oos_eq.iloc[0]
            oos_cagr = total_ret**(1/years) - 1 if years > 0 else np.nan
            oos_dd = (oos_eq/oos_eq.cummax() - 1.0).min()
        else:
            oos_cagr, oos_dd = (np.nan, np.nan)
        row = {'mode': mode, 'policy': policy, 'OOS_CAGR': oos_cagr, 'OOS_MaxDD': oos_dd}
        row.update(rep)
        rows.append(row)
    return pd.DataFrame(rows)
def run_oos_audit(df, dev_end="2018-12-31", val_end="2021-12-31",
                  mode="dl", policy="bandit", out_path="oos_audit.csv", **kw):
    kw = dict(kw)
    kw.pop("initial_train_end", None)
    dev_end = pd.to_datetime(dev_end)
    val_end = pd.to_datetime(val_end)
    idx = df.index.sort_values()
    if len(idx) == 0:
        return pd.DataFrame(columns=["slice", "CAGR", "Sharpe_ex_rf0", "MaxDD"])
    overall_start = idx.min()
    overall_end = idx.max()
    slices = [
        ("DEV", overall_start, dev_end, None),
        ("VAL", dev_end, val_end, dev_end),
        ("TEST", val_end, overall_end, val_end),
    ]
    rows = []
    for name, slice_start, slice_end, train_cutoff in slices:
        if slice_start is None or slice_end is None:
            rows.append({"slice": name, "CAGR": np.nan, "Sharpe_ex_rf0": np.nan, "MaxDD": np.nan})
            continue
        slice_start = pd.to_datetime(slice_start)
        slice_end = pd.to_datetime(slice_end)
        slice_end = min(slice_end, overall_end)
        if slice_end <= slice_start:
            rows.append({"slice": name, "CAGR": np.nan, "Sharpe_ex_rf0": np.nan, "MaxDD": np.nan})
            continue
        if mode == "baseline":
            res = baseline_backtest(df, policy_mode=policy, **kw)
        else:
            if train_cutoff is None:
                print("[oos_audit] Skipping DL DEV slice: initial_train_end is required for DL backtests.")
                rows.append({"slice": name, "CAGR": np.nan, "Sharpe_ex_rf0": np.nan, "MaxDD": np.nan})
                continue
            res = deep_learning_backtest(
                df,
                policy_mode=policy,
                initial_train_end=train_cutoff,
                **kw
            )
        eq = res[0]
        if train_cutoff is None:
            oos_start = slice_start
        else:
            oos_start = pd.to_datetime(train_cutoff) + pd.offsets.BDay(1)
        lower = max(oos_start, slice_start)
        oos_eq = eq.loc[(eq.index > lower) & (eq.index <= slice_end)]
        if len(oos_eq) < 10:
            rows.append({"slice": name, "CAGR": np.nan, "Sharpe_ex_rf0": np.nan, "MaxDD": np.nan})
            continue
        years = len(oos_eq) / 252.0
        total_ret = oos_eq.iloc[-1] / oos_eq.iloc[0]
        cagr = total_ret**(1/years) - 1 if years > 0 else np.nan
        dd = (oos_eq / oos_eq.cummax()) - 1.0
        rows.append({
            "slice": name,
            "CAGR": float(cagr),
            "Sharpe_ex_rf0": np.nan,
            "MaxDD": float(dd.min())
        })
    out = pd.DataFrame(rows)
    try:
        out.to_csv(out_path, index=False)
        print(f"[oos_audit] Saved: {out_path}")
    except Exception as exc:
        print(f"[oos_audit] Save failed: {exc}")
    print(out)
    return out
def block_bootstrap_drawdowns(ret, n_sims=5000, block=5):
    arr = np.asarray(ret.dropna(), dtype=float)
    if arr.size == 0:
        return {'dd_p50': np.nan, 'dd_p5': np.nan, 'dd_p1': np.nan}
    rng = np.random.default_rng(42)
    dds = []
    n = arr.size
    for _ in range(n_sims):
        path = []
        length = 0
        while length < n:
            start = rng.integers(0, max(1, n - block))
            seg = arr[start:start+block]
            path.extend(seg)
            length += len(seg)
        path = np.array(path[:n], dtype=float)
        if path.size == 0:
            continue
        # Avoid numerical issues if eq path becomes zero
        eq_path = (1.0 + path).cumprod()
        if eq_path.size == 0:
            continue
        drawdowns = (eq_path/np.maximum.accumulate(eq_path)) - 1.0
        dds.append(drawdowns.min())
    if not dds:
        return {'dd_p50': np.nan, 'dd_p5': np.nan, 'dd_p1': np.nan}
    dds = np.array(dds)
    return {
        'dd_p50': float(np.percentile(dds, 50)),
        'dd_p5': float(np.percentile(dds, 5)),
        'dd_p1': float(np.percentile(dds, 1))
    }
def generate_comparison_plot(results, out_prefix, show=False):
    if not results:
        return None
    try:
        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        first_key = next(iter(results))
        tqq = results[first_key][5]
        plotting_keys = {k: v for k, v in results.items() if k.lower() != 'baseline'}
        rename_map = {'dl': 'Deep Learning', 'ma': 'MA Crossover', 'ma_crossover': 'MA Crossover'}
        t_label = 'TQQQ Buy&Hold'
        legend_labels = []
        metrics_lines = []
        for name, pack in plotting_keys.items():
            eq = pack[0]
            label = rename_map.get(name.lower(), name.upper())
            (eq/eq.iloc[0]).rename(label).plot(ax=ax[0], lw=1.2)
            legend_labels.append(label)
            report = pack[2] or {}
            try:
                cagr = report.get('CAGR')
                sharpe = report.get('Sharpe_ex_rf0')
                maxdd = report.get('MaxDD')
                if cagr is not None and sharpe is not None and maxdd is not None:
                    metrics_lines.append(
                        f"{label}: CAGR {cagr:,.2%} | Sharpe {sharpe:,.2f} | MaxDD {maxdd:,.2%}"
                    )
            except Exception:
                pass
        (tqq/tqq.iloc[0]).rename(t_label).plot(ax=ax[0], lw=1.1, linestyle='--')
        base_report = results[first_key][2] or {}
        try:
            t_cagr = base_report.get('TQQQ_CAGR')
            t_sharpe = base_report.get('TQQQ_Sharpe')
            t_maxdd = base_report.get('TQQQ_MaxDD')
            if t_cagr is not None and t_sharpe is not None and t_maxdd is not None:
                metrics_lines.append(
                    f"{t_label}: CAGR {t_cagr:,.2%} | Sharpe {t_sharpe:,.2f} | MaxDD {t_maxdd:,.2%}"
                )
        except Exception:
            pass
        ax[0].set_title('Equity Curves')
        ax[0].grid(True, alpha=0.3)
        for name, pack in plotting_keys.items():
            dd = pack[1]
            label = rename_map.get(name.lower(), name.upper())
            dd.rename(label).plot(ax=ax[1], lw=1.0)
        tdd = (tqq/tqq.cummax()) - 1.0
        tdd.rename(t_label).plot(ax=ax[1], lw=1.0, linestyle='--')
        ax[1].set_title('Drawdowns')
        ax[1].grid(True, alpha=0.3)
        ax[0].legend(loc='upper left')
        ax[1].legend(loc='lower left')
        if metrics_lines:
            metrics_text = "\n".join(metrics_lines)
            fig.text(
                0.5, 0.02, metrics_text,
                ha='center',
                va='bottom',
                fontsize=9,
                bbox=dict(facecolor='white', alpha=0.9, edgecolor='none')
            )
        plt.tight_layout()
        plot_path = f"{out_prefix}_comparison.png"
        fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        if show:
            plt.show()
        plt.close(fig)
        return plot_path
    except Exception:
        return None
def build_monthly_summary_text(csv_path: str, start_date, end_date):
    """
    Build a human-readable summary text for strategy performance between start_date and end_date,
    based on the daily metrics CSV. This is intended to be used as context for an LLM “strategy health check”.
    """
    import pandas as pd
    from textwrap import dedent

    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df[(df["date"] >= pd.Timestamp(start_date)) & (df["date"] <= pd.Timestamp(end_date))].copy()
    df.sort_values("date", inplace=True)
    if df.empty:
        return "No data in the given period."

    equity_start = df["equity"].iloc[0]
    equity_end = df["equity"].iloc[-1]
    total_ret = equity_end / max(equity_start, 1e-12) - 1.0
    days = len(df)
    ann_vol = df["daily_ret"].std() * (252.0**0.5)
    max_dd = df["drawdown"].min()

    avg_turnover = df["turnover"].mean() if "turnover" in df.columns else 0.0
    avg_lev = df["effective_leverage"].mean()
    avg_kelly = df["kelly_frac_live"].mean()
    avg_qqq5 = df["qqq5_weight"].mean()
    total_costs = df["fees_and_costs"].sum() if "fees_and_costs" in df.columns else 0.0
    total_realized = df["realized_pnl"].sum() if "realized_pnl" in df.columns else 0.0
    cost_ratio = (total_costs / max(abs(total_realized), 1e-8)) if total_realized != 0 else 0.0

    worst_day = df.loc[df["drawdown"].idxmin()]
    best_day = df.loc[df["daily_ret"].idxmax()]

    text = f"""
    【总体统计】
    时间范围：{pd.Timestamp(start_date).date()} ~ {pd.Timestamp(end_date).date()}
    - 区间收益率：{total_ret*100:.2f}%
    - 年化波动率（按日收益换算）：{ann_vol*100:.2f}%
    - 最大回撤：{max_dd*100:.2f}%
    - 交易天数：{days}
    - 日均换手率：{avg_turnover*100:.2f}%
    - 日均有效杠杆：{avg_lev:.2f}x
    - 日均 Kelly fraction：{avg_kelly:.2f}
    - 日均 QQQ5 权重：{avg_qqq5*100:.2f}%
    - 费用占毛收益比：{cost_ratio*100:.2f}%

    【极端样本】
    1）最大回撤当天（{worst_day['date'].date()}）
    - 当日收益：{worst_day['daily_ret']*100:.2f}%
    - 当日回撤：{worst_day['drawdown']*100:.2f}%
    - 当日 effective_leverage: {worst_day['effective_leverage']:.2f}x
    - regime_score: {worst_day['regime_score']:.2f}
    - risk_flag: {int(worst_day['risk_flag'])}
    - 权重：TQQQ {worst_day['tqqq_weight']*100:.1f}%，QQQ5 {worst_day['qqq5_weight']*100:.1f}%，
            FUT {worst_day['fut_weight']*100:.1f}%，CASH {worst_day['cash_weight']*100:.1f}%

    2）单日最大盈利（{best_day['date'].date()}）
    - 当日收益：{best_day['daily_ret']*100:.2f}%
    - 当日 effective_leverage: {best_day['effective_leverage']:.2f}x
    - regime_score: {best_day['regime_score']:.2f}
    - risk_flag: {int(best_day['risk_flag'])}
    - 权重：TQQQ {best_day['tqqq_weight']*100:.1f}%，QQQ5 {best_day['qqq5_weight']*100:.1f}%，
            FUT {best_day['fut_weight']*100:.1f}%，CASH {best_day['cash_weight']*100:.1f}%
    """
    return dedent(text).strip()
# ------------------ Main ------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', type=str, default='both', choices=['baseline','dl','both'])
    ap.add_argument('--start', type=str, default=None)
    ap.add_argument('--end', type=str, default=None)
    ap.add_argument('--rf', type=float, default=None)  # constant override; else history series used
    ap.add_argument('--qqq_csv', type=str, default=None)
    ap.add_argument('--tqqq_csv', type=str, default=None)
    ap.add_argument('--qqq5_csv', type=str, default=None)
    ap.add_argument('--kelly_frac', type=float, default=0.493816)
    ap.add_argument('--kelly_lookback_days', type=int, default=252*3)
    ap.add_argument('--rebal_days', type=int, default=3)  # weekly-ish
    ap.add_argument('--fut_fin_spread', type=float, default=0.002)
    ap.add_argument('--tqqq_expense', type=float, default=0.009)
    ap.add_argument('--qqq5_expense', type=float, default=0.0095)
    ap.add_argument('--trade_cost_bps', type=float, default=1.0)
    ap.add_argument('--slip_bps', type=float, default=1.0, help='Per-trade slippage bps of traded notional')
    ap.add_argument('--tqqq_slip_bps', type=float, default=1.0, help='Additional slippage when trading TQQQ sleeve')
    ap.add_argument('--qqq5_slip_bps', type=float, default=15.0, help='Additional slippage when trading QQQ5 sleeve')
    ap.add_argument('--impact_k', type=float, default=0.0005, help='Impact coefficient vs ADV (per $ notional / ADV)')
    ap.add_argument('--adv_lookback', type=int, default=20, help='ADV rolling lookback window (days)')
    ap.add_argument('--adv_tqqq_csv', type=str, default=None, help='Optional CSV with TQQQ ADV (Date index, first column values)')
    ap.add_argument('--adv_qqq5_csv', type=str, default=None, help='Optional CSV with QQQ5 ADV (Date index, first column values)')
    ap.add_argument('--adv_daily_frac_cap', type=float, default=0.10, help='Max ADV fraction tradable per day per sleeve')
    ap.add_argument('--policy', type=str, default='bandit', choices=['none','bandit'])
    ap.add_argument('--bandit_alpha', type=float, default=0.69744)
    ap.add_argument('--dl_conf', type=float, default=0.62, help='Min softmax confidence for DL to act')
    ap.add_argument('--dl_max_daily_qqq5', type=float, default=0.08,
                    help='Daily cap on fresh QQQ5 allocation from DL signals')
    ap.add_argument('--dl_delever_threshold', type=float, default=-0.004,
                    help='Trigger DL delever action when expected return is below this level')
    ap.add_argument('--dl_delever_frac', type=float, default=0.05,
                    help='Fraction of TQQQ sleeve to trim when DL delever action fires')
    ap.add_argument('--dl_max_qqq5', type=float, default=0.35, help='Hard cap on QQQ5 sleeve')
    ap.add_argument('--dl_max_tqqq', type=float, default=0.60, help='Hard cap on TQQQ sleeve')
    ap.add_argument('--dl_trade_max_frac', type=float, default=0.35, help='Max fraction per DL-triggered trade')
    ap.add_argument('--dl_cooldown', type=int, default=3, help='Days to disable DL overlay after a risk-gate trigger')
    ap.add_argument('--initial_train_end', type=str, default='2018-12-31',
                    help='Required cutoff date for initial DL training; prevents full-sample DL training.')
    ap.add_argument('--target_vol', type=float, default=0.329758, help='Annualized target vol cap for QQQ exposure')
    ap.add_argument('--risk_gate_max_fut_frac', type=float, default=0.55,
                    help='Risk gate clamp for FUT sleeve; excess flows back to cash')
    ap.add_argument('--risk_gate_max_leverage', type=float, default=2.2,
                    help='Risk gate clamp for L_base when shocks persist')
    ap.add_argument('--kelly_max_step', type=float, default=0.10,
                    help='Maximum per-rebalance change applied to Kelly fraction')
    ap.add_argument('--risk_gate', action='store_true')
    ap.add_argument('--no_risk_gate', dest='risk_gate', action='store_false')
    ap.add_argument('--plot', action='store_true')
    ap.add_argument('--walk_forward', action='store_true')
    ap.add_argument('--wf_step_months', type=int, default=3)
    ap.add_argument('--wf_oos_months', type=int, default=6)
    ap.add_argument('--abtest', action='store_true')
    ap.add_argument('--oos_start', type=str, default='2023-01-01')
    ap.add_argument('--sensitivity', action='store_true')
    ap.add_argument('--stress_mc', action='store_true')
    ap.add_argument('--oos_audit', action='store_true')
    ap.add_argument('--oos_dev_end', type=str, default='2018-12-31')
    ap.add_argument('--oos_val_end', type=str, default='2021-12-31')
    ap.add_argument('--min_years', type=float, default=4.0,
                    help='Minimum data span (in years) required for Kelly estimation')
    ap.add_argument('--out_prefix', type=str, default='qqq_compare')
    ap.add_argument('--metrics_csv', type=str, default=DAILY_METRICS_CSV,
                    help='Path to write daily metrics for this run (set to blank to disable logging).')
    ap.add_argument(
        "--crash_mode",
        type=str,
        default="soft_scale",
        choices=["none", "monitor_only", "hard_cap", "soft_scale"],
        help="How to use the crash head in execution: "
             "'none' = ignore crash_prob; "
             "'monitor_only' = compute crash caps but do not apply them; "
             "'hard_cap' = enforce crash-driven leverage caps; "
             "'soft_scale' = smoothly scale Kelly / target leverage."
    )
    ap.add_argument(
        "--crash_tail_frac",
        type=float,
        default=0.05,
        help="Tail fraction for the binary crash label (e.g., 0.05 = worst 5% windows)."
    )
    ap.add_argument("--summary", action="store_true",
                    help="Print a human-readable summary for an LLM health check, based on daily metrics CSV.")
    ap.add_argument("--summary_csv", type=str, default="logs/strategy_daily_metrics.csv",
                    help="Path to the daily metrics CSV.")
    ap.add_argument("--summary_start", type=str, default=None,
                    help="Start date (YYYY-MM-DD) for summary. If None and --summary is set, uses first day of the last full month.")
    ap.add_argument("--summary_end", type=str, default=None,
                    help="End date (YYYY-MM-DD) for summary. If None and --summary is set, uses last day of the last full month.")
    ap.add_argument("--summary_save", type=str, default=None,
                    help="Optional path to save the summary text (e.g., out_prefix+'_summary.txt').")
    ap.set_defaults(risk_gate=True)
    args = ap.parse_args()
    set_global_seed(42)
    import datetime as dt
    if args.summary:
        if args.summary_start is None or args.summary_end is None:
            s, e = _last_full_month_range()
            if args.summary_start is not None:
                s = pd.Timestamp(args.summary_start)
            if args.summary_end is not None:
                e = pd.Timestamp(args.summary_end)
        else:
            s = pd.Timestamp(args.summary_start)
            e = pd.Timestamp(args.summary_end)
        try:
            txt = build_monthly_summary_text(args.summary_csv, s, e)
        except Exception as ex:
            print("[summary] Failed to build summary:", ex)
            sys.exit(1)
        print("\n" + txt + "\n")
        if args.summary_save:
            try:
                with open(args.summary_save, "w", encoding="utf-8") as f:
                    f.write(txt)
                print(f"[summary] Saved to: {args.summary_save}")
            except Exception as ex:
                print("[summary] Save failed:", ex)
        sys.exit(0)
    if not args.start:
        args.start = "2015-01-01"
    if not args.end or str(args.end).lower() in {"auto", "today"}:
        args.end = dt.date.today().isoformat()
    print(f"Backtest window: {args.start} to {args.end}")
    df, synth5 = load_prices(args)
    df = df.loc[(df.index>=args.start) & (df.index<=args.end)]
    if len(df) < int(252 * args.min_years):
        raise SystemExit(f"Need >= ~{args.min_years:.1f} years of data for rolling Kelly.")
    # rf series (or constant override)
    rf_series = None
    if args.rf is None:
        rf_series = fetch_rf_series_or_default(args.start, args.end)
        if rf_series is None or len(rf_series) == 0:
            rf_ann = 0.02
        else:
            last_date = getattr(rf_series, "index", None)
            last_date = last_date[-1] if last_date is not None and len(last_date) else None
            if last_date is None:
                rf_ann = 0.02
            else:
                rf_ann = get_rf_value_for_date(rf_series, last_date, 0.02)
    else:
        rf_ann = float(args.rf)
    # VIX series (risk gate 辅助，不可用则 None）
    vix_series = fetch_vix_series_or_none(args.start, args.end)
    adv_series = fetch_tqqq_adv_series(args.start, args.end, args.adv_lookback)
    if adv_series is not None:
        adv_series = pd.to_numeric(adv_series, errors='coerce').sort_index()
    def _load_adv_csv(path: str | None):
        if not path:
            return None
        try:
            df_adv = pd.read_csv(path, index_col=0)
            try:
                df_adv.index = pd.to_datetime(df_adv.index)
            except Exception:
                pass
            if isinstance(df_adv, pd.DataFrame):
                if df_adv.shape[1] == 0:
                    return None
                ser = df_adv.iloc[:, 0]
            else:
                ser = df_adv
            ser = pd.to_numeric(ser, errors='coerce').dropna()
            return ser.sort_index()
        except Exception as exc:
            print(f"[warn] Failed to load ADV CSV {path}: {exc}")
            return None
    adv_tqqq = _load_adv_csv(args.adv_tqqq_csv)
    adv_qqq5 = _load_adv_csv(args.adv_qqq5_csv)
    results = {}
    if args.mode in ('baseline','both'):
        print("Running BASELINE+POLICY strategy...")
        beq, bdd, brep, bwt, btr, btq, bnotes, bpos, bbandit, _ = baseline_backtest(
            df,
            rf_series=rf_series,
            rf_ann=rf_ann,
            base_kelly_frac=args.kelly_frac,
            kelly_lookback_days=args.kelly_lookback_days,
            rebalance_every_days=args.rebal_days,
            fut_fin_spread=args.fut_fin_spread,
            tqqq_expense=args.tqqq_expense,
            qqq5_expense=args.qqq5_expense,
            trade_cost_bps=args.trade_cost_bps,
            policy_mode=args.policy,
            bandit_alpha=args.bandit_alpha,
            use_risk_gate=args.risk_gate,
            vix_series=vix_series,
            target_vol=args.target_vol,
            dl_conf=args.dl_conf,
            dl_max_qqq5=args.dl_max_qqq5,
            dl_max_tqqq=args.dl_max_tqqq,
            dl_trade_max_frac=args.dl_trade_max_frac,
            dl_cooldown=args.dl_cooldown,
            adv_series=adv_series,
            slip_bps=args.slip_bps,
            impact_k=args.impact_k,
            tqqq_slip_bps=args.tqqq_slip_bps,
            qqq5_slip_bps=args.qqq5_slip_bps,
            adv_series_tqqq=adv_tqqq,
            adv_series_qqq5=adv_qqq5,
            adv_daily_frac_cap=args.adv_daily_frac_cap,
            metrics_csv_path=args.metrics_csv,
            crash_mode=args.crash_mode,
            crash_tail_frac=args.crash_tail_frac
        )
        results['baseline'] = (beq, bdd, brep, bwt, btr, btq, bnotes, bpos, bbandit, None)
    if args.mode in ('dl','both'):
        print("Running DL+POLICY strategy...")
        deq, ddd, drep, dwt, dtr, dtq, dnotes, dpos, dbandit, dlretrain = deep_learning_backtest(
            df,
            rf_series=rf_series,
            rf_ann=rf_ann,
            base_kelly_frac=args.kelly_frac,
            kelly_lookback_days=args.kelly_lookback_days,
            rebalance_every_days=args.rebal_days,
            fut_fin_spread=args.fut_fin_spread,
            tqqq_expense=args.tqqq_expense,
            qqq5_expense=args.qqq5_expense,
            trade_cost_bps=args.trade_cost_bps,
            policy_mode=args.policy,
            bandit_alpha=args.bandit_alpha,
            use_risk_gate=args.risk_gate,
            vix_series=vix_series,
            target_vol=args.target_vol,
            dl_conf=args.dl_conf,
            dl_max_qqq5=args.dl_max_qqq5,
            dl_max_tqqq=args.dl_max_tqqq,
            dl_trade_max_frac=args.dl_trade_max_frac,
            dl_cooldown=args.dl_cooldown,
            adv_series=adv_series,
            slip_bps=args.slip_bps,
            impact_k=args.impact_k,
            tqqq_slip_bps=args.tqqq_slip_bps,
            qqq5_slip_bps=args.qqq5_slip_bps,
            adv_series_tqqq=adv_tqqq,
            adv_series_qqq5=adv_qqq5,
            adv_daily_frac_cap=args.adv_daily_frac_cap,
            initial_train_end=args.initial_train_end,
            metrics_csv_path=args.metrics_csv,
            crash_mode=args.crash_mode,
            crash_tail_frac=args.crash_tail_frac
        )
        results['dl'] = (deq, ddd, drep, dwt, dtr, dtq, dnotes, dpos, dbandit, dlretrain)
    # Simple MA crossover (TQQQ only)
    print("Running MA crossover strategy...")
    meq, mdd, mrep, mwt, mtr, mtq, mnotes, mpos, _, _ = ma_crossover_backtest(
        df,
        fast=5,
        slow=20,
        tqqq_expense=args.tqqq_expense,
        trade_cost_bps=args.trade_cost_bps,
        slip_bps=args.slip_bps
    )
    results['ma'] = (meq, mdd, mrep, mwt, mtr, mtq, mnotes, mpos, None, None)
    # Save CSVs and print report
    for name, pack in results.items():
        eq, dd, rep, wt, tr, tqqq_eq, notes_df, pos_df, bandit_diag, dl_retrain_df = pack
        out_df = pd.concat([df, eq.rename(f'{name}_Equity'),
                            dd.rename(f'{name}_Drawdown'),
                            tqqq_eq.rename(f'{name}_TQQQ_BuyHold')], axis=1)
        eq_path = f"{args.out_prefix}_{name}_equity.csv"
        wt_path = f"{args.out_prefix}_{name}_weights.csv"
        tr_path = f"{args.out_prefix}_{name}_trades.csv"
        nt_path = f"{args.out_prefix}_{name}_notes.csv"
        pos_path = f"{args.out_prefix}_{name}_positions.csv"
        out_df.to_csv(eq_path); wt.to_csv(wt_path); tr.to_csv(tr_path); notes_df.to_csv(nt_path); pos_df.to_csv(pos_path)
        print(f"[{name}] Saved: {eq_path}, {wt_path}, {tr_path}, {nt_path}, {pos_path}")
        if bandit_diag is not None:
            bd_path = f"{args.out_prefix}_{name}_bandit_diag.csv"
            bandit_diag.to_csv(bd_path)
            print(f"[{name}] Saved bandit diag: {bd_path}")
        if dl_retrain_df is not None:
            dr_path = f"{args.out_prefix}_{name}_dl_retrain.csv"
            dl_retrain_df.to_csv(dr_path)
            print(f"[{name}] Saved DL retrain log: {dr_path}")
    print("\n==== REPORT ====")
    for name, pack in results.items():
        rep = pack[2]
        print(f"\n--- {name.upper()} ---")
        for k,v in rep.items():
            print(f"{k}: {v:.4f}" if isinstance(v,(int,float)) else f"{k}: {v}")
    if synth5:
        print("Note: QQQ5 synthesized (with airbag) + hybrid with live series when available.")
    common_kw = dict(
        rf_series=rf_series, rf_ann=rf_ann, base_kelly_frac=args.kelly_frac,
        kelly_lookback_days=args.kelly_lookback_days, rebalance_every_days=args.rebal_days,
        fut_fin_spread=args.fut_fin_spread, tqqq_expense=args.tqqq_expense, qqq5_expense=args.qqq5_expense,
        trade_cost_bps=args.trade_cost_bps, bandit_alpha=args.bandit_alpha, use_risk_gate=args.risk_gate,
        vix_series=vix_series, target_vol=args.target_vol, dl_conf=args.dl_conf,
        dl_max_daily_qqq5=args.dl_max_daily_qqq5, dl_delever_threshold=args.dl_delever_threshold,
        dl_delever_frac=args.dl_delever_frac, dl_max_qqq5=args.dl_max_qqq5, dl_max_tqqq=args.dl_max_tqqq,
        dl_trade_max_frac=args.dl_trade_max_frac, dl_cooldown=args.dl_cooldown,
        adv_series=adv_series, slip_bps=args.slip_bps, impact_k=args.impact_k,
        risk_gate_max_fut_frac=args.risk_gate_max_fut_frac,
        risk_gate_max_leverage=args.risk_gate_max_leverage,
        kelly_max_step=args.kelly_max_step,
        tqqq_slip_bps=args.tqqq_slip_bps,
        qqq5_slip_bps=args.qqq5_slip_bps,
        adv_series_tqqq=adv_tqqq,
        adv_series_qqq5=adv_qqq5,
        adv_daily_frac_cap=args.adv_daily_frac_cap,
        metrics_csv_path=None,
        crash_mode=args.crash_mode,
        crash_tail_frac=args.crash_tail_frac
    )
    if args.oos_audit:
        audit_mode = 'baseline' if args.mode == 'baseline' else 'dl'
        run_oos_audit(
            df,
            dev_end=args.oos_dev_end,
            val_end=args.oos_val_end,
            mode=audit_mode,
            policy=args.policy,
            out_path=f"{args.out_prefix}_oos_audit.csv",
            **common_kw
        )
        return
    if args.walk_forward:
        print("Running Walk-Forward validation...")
        if args.mode in ('baseline', 'both'):
            wfa_b = run_walk_forward(df, 'baseline', args.policy, args.wf_step_months, args.wf_oos_months, **common_kw)
            if wfa_b is not None:
                wfa_path = f"{args.out_prefix}_wfa_baseline.csv"
                wfa_b.to_csv(wfa_path, index=False)
                print(f"[baseline] Walk-forward stats saved: {wfa_path}")
                print(wfa_b.describe())
        if args.mode in ('dl', 'both'):
            wfa_d = run_walk_forward(df, 'dl', args.policy, args.wf_step_months, args.wf_oos_months, **common_kw)
            if wfa_d is not None:
                wfa_path = f"{args.out_prefix}_wfa_dl.csv"
                wfa_d.to_csv(wfa_path, index=False)
                print(f"[dl] Walk-forward stats saved: {wfa_path}")
                print(wfa_d.describe())
    if args.abtest:
        print("Running AB test...")
        ab = run_abtest(df, args.oos_start, common_kw, args.initial_train_end)
        ab_path = f"{args.out_prefix}_abtest.csv"
        ab.to_csv(ab_path, index=False)
        print(f"AB test saved: {ab_path}")
        print(ab)
    if args.sensitivity:
        print("Running parameter sensitivity...")
        base_cfg = dict(common_kw)
        grid_params = ['base_kelly_frac', 'target_vol', 'fut_fin_spread', 'trade_cost_bps', 'bandit_alpha']
        deltas = [-0.2, -0.1, 0.1, 0.2]
        rows = []
        for param in grid_params:
            baseline_value = base_cfg[param]
            for pct in deltas:
                cfg = dict(base_cfg)
                cfg[param] = baseline_value * (1.0 + pct)
                res = baseline_backtest(df, policy_mode='bandit', **cfg)
                rep = res[2]
                rows.append({'param': param, 'delta_pct': pct, **rep})
        sens = pd.DataFrame(rows)
        sens_path = f"{args.out_prefix}_sensitivity.csv"
        sens.to_csv(sens_path, index=False)
        print(f"Sensitivity results saved: {sens_path}")
        print(sens.groupby('param')[['CAGR','MaxDD','Sharpe_ex_rf0']].mean())
    if args.stress_mc:
        print("Running stress MC...")
        res = baseline_backtest(
            df,
            policy_mode='bandit',
            **common_kw
        )
        eq_series = res[0]
        ret = eq_series.pct_change().dropna()
        stress = block_bootstrap_drawdowns(ret, n_sims=5000, block=5)
        stress_path = f"{args.out_prefix}_stress_mc.csv"
        pd.Series(stress).to_csv(stress_path)
        print(f"Stress MC saved: {stress_path}")
        print(stress)
    if args.oos_audit:
        audit_mode = 'baseline' if args.mode == 'baseline' else 'dl'
        run_oos_audit(
            df,
            dev_end=args.oos_dev_end,
            val_end=args.oos_val_end,
            mode=audit_mode,
            policy=args.policy,
            out_path=f"{args.out_prefix}_oos_audit.csv",
            **common_kw
        )
    plot_path = generate_comparison_plot(results, args.out_prefix, show=args.plot)
    if plot_path:
        open_file_with_default_viewer(plot_path)
if __name__ == "__main__":
    main()
