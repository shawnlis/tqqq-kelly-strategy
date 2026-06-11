# robust_fast.py
# 鲁棒性测试（极速版）：快筛 + 分段早停 + 断点续跑 + 线程限速 + 缓存
# 用法示例：
#   # v5e shortlist（自动启用预设、去重 Top-K、写出 final manifest）
#   python robust_fast.py --strategy qqq_deep_learning_and_baseline.py ^
#       --start 2015-01-01 --end 2025-10-01 --mode baseline --threads 4 ^
#       --final-shortlist --preset-v5e --out-prefix robust_rel_v5e ^
#       --resume robust_ckpt_rel_v5e.json
#
#   # v5e + DL 验证 + OOS（2010-2025）+ 外部 grid JSON
#   python robust_fast.py --strategy qqq_deep_learning_and_baseline.py ^
#       --start 2015-01-01 --end 2025-10-01 --mode baseline --threads 4 ^
#       --grid-json recommended_narrowed_grid.json ^
#       --final-shortlist --preset-v5e --validate-dl --dl-keep-if improve ^
#       --oos-verify --oos-start 2010-01-01 ^
#       --out-prefix robust_rel_v5e_oos --resume robust_ckpt_rel_v5e_oos.json
#
# 注意：脚本仅写少量 CSV（summary、topk 等），避免海量 I/O；你可按需打开详细保存。

import os, sys, json, math, time, argparse, itertools, hashlib, random
from pathlib import Path
import numpy as np
import pandas as pd
import importlib.util
from datetime import datetime

GLOBAL_SEED = 42

def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch  # type: ignore
    except Exception:
        return
    try:
        torch.manual_seed(seed)
        if hasattr(torch, "cuda"):
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass

# ---------- 1) 线程限速：显著降温并保持稳定 ----------
def set_thread_limits(n: int = 4):
    n = max(1, int(n))
    os.environ["OMP_NUM_THREADS"] = str(n)
    os.environ["MKL_NUM_THREADS"] = str(n)
    os.environ["OPENBLAS_NUM_THREADS"] = str(n)
    os.environ["NUMEXPR_NUM_THREADS"] = str(n)
    os.environ.setdefault("MKL_DYNAMIC", "FALSE")

# ---------- 2) 动态加载你的策略模块 ----------
def import_strategy_module(path: str):
    path = str(Path(path).resolve())
    spec = importlib.util.spec_from_file_location("strategy_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod

# ---------- 3) 缓存工具 ----------
def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

def cache_put(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, pd.Series):
        obj = obj.to_frame(name=obj.name or "value")
    if isinstance(obj, pd.DataFrame):
        obj.to_parquet(path)
    else:
        path.write_text(json.dumps(obj, default=str))

def cache_get(path: Path):
    if not path.exists():
        return None
    if path.suffix == ".parquet":
        try:
            obj = pd.read_parquet(path)
            if isinstance(obj, pd.DataFrame) and obj.shape[1] == 1:
                return obj.iloc[:, 0]
            return obj
        except Exception:
            try:
                return json.loads(path.read_text())
            except Exception:
                return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None

# ---------- 4) 构造参数网格 ----------
DEFAULT_GRID = {
    "base_kelly_frac":      [0.45, 0.50],
    "target_vol":           [0.31, 0.32],
    "fut_fin_spread":       [0.0015],
    "trade_cost_bps":       [0.5, 1.0],
    "bandit_alpha":         [0.7, 0.8],
    "rebalance_every_days": [5],
}

def load_grid_from_json(path: str | None) -> dict | None:
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(f"--grid-json not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        grid = json.load(f)
    if not isinstance(grid, dict) or not all(isinstance(v, list) for v in grid.values()):
        raise ValueError("grid-json must be a dict[str, list]")
    return grid

def build_grid(grid_override: dict | None = None):
    grid_dict = grid_override or {k: list(v) for k, v in DEFAULT_GRID.items()}
    keys = list(grid_dict.keys())
    vals = [grid_dict[k] for k in keys]
    combos = [dict(zip(keys, combo)) for combo in itertools.product(*vals)]
    return grid_dict, combos

def default_grid(grid_override: dict | None = None):
    _, combos = build_grid(grid_override)
    for combo in combos:
        yield combo

def deflated_sharpe_ratio(returns: np.ndarray, rf: float = 0.0) -> float:
    """Bailey et al. (2014) deflated Sharpe ratio approximation."""
    if returns is None:
        return float("-inf")
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 30:
        return float("-inf")
    excess = arr - rf / 252.0
    std = excess.std(ddof=1)
    if std <= 1e-12 or not np.isfinite(std):
        return float("-inf")
    sr = excess.mean() / std * math.sqrt(252.0)
    if not np.isfinite(sr):
        return float("-inf")
    series = pd.Series(excess)
    skew = series.skew()
    kurt = series.kurt()
    skew = 0.0 if not np.isfinite(skew) else float(skew)
    kurt = 0.0 if not np.isfinite(kurt) else float(kurt)
    n = excess.size
    penalty = (1 - skew * sr + (kurt - 1.0) * (sr ** 2) / 4.0) / max(n, 1)
    sr_adj = sr * (1 - penalty)
    if not np.isfinite(sr_adj):
        return float("-inf")
    return float(sr_adj)

def calmar_ratio(cagr: float, maxdd: float) -> float:
    try:
        return float(cagr) / max(1e-12, abs(float(maxdd)))
    except Exception:
        return float("-inf")

def last12m_sharpe(ret: pd.Series) -> float:
    if ret is None:
        return float("-inf")
    x = ret.dropna()
    if x.empty:
        return float("-inf")
    window = x.iloc[-252:]
    if window.size < 60:
        return float("-inf")
    mu = window.mean()
    sd = window.std(ddof=1)
    if not np.isfinite(sd) or sd <= 1e-12:
        return float("-inf")
    return float((mu / sd) * math.sqrt(252.0))

def daily_alpha_vs_tqqq(ret: pd.Series, t_ret: pd.Series) -> float:
    if ret is None or t_ret is None:
        return float("-inf")
    data = pd.concat([ret, t_ret], axis=1).dropna()
    if data.shape[0] < 120:
        return float("-inf")
    y = data.iloc[:, 0].values
    x = data.iloc[:, 1].values
    x_mean = x.mean()
    y_mean = y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom <= 1e-12:
        beta = 0.0
        alpha = y_mean
    else:
        beta = ((x - x_mean) * (y - y_mean)).sum() / denom
        alpha = y_mean - beta * x_mean
    if not np.isfinite(alpha):
        return float("-inf")
    return float(alpha * 252.0)


def tqqq_price_returns_for_benchmark(df: pd.DataFrame, common_kw: dict | None = None) -> pd.Series | None:
    if not isinstance(df, pd.DataFrame) or "TQQQ" not in df.columns:
        return None
    ret = df["TQQQ"].pct_change().fillna(0.0)
    kw = common_kw or {}
    if bool(kw.get("deduct_tqqq_expense_in_returns", False)):
        ret = ret - (float(kw.get("tqqq_expense", 0.0) or 0.0) / 252.0)
    return ret


def metric_from_report(obj, *names, default=None):
    if obj is None:
        return default
    for name in names:
        try:
            val = obj.get(name)
        except AttributeError:
            val = None
        if val is None:
            continue
        try:
            if pd.isna(val):
                continue
        except Exception:
            pass
        return val
    return default


def preferred_metric_column(df: pd.DataFrame, *names) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def qqq5_source_from_df(mod, df: pd.DataFrame | None) -> str:
    if df is None:
        return "unknown"
    try:
        if hasattr(mod, "_qqq5_source_from_df"):
            return str(mod._qqq5_source_from_df(df))
    except Exception:
        pass
    meta = getattr(df, "attrs", {}).get("price_meta", {}) if df is not None else {}
    if isinstance(meta, dict):
        qqq5_meta = meta.get("QQQ5", {})
        if isinstance(qqq5_meta, dict):
            return str(qqq5_meta.get("source", "unknown"))
    return "unknown"


def ensure_price_meta(mod, df: pd.DataFrame | None, args=None) -> pd.DataFrame | None:
    if df is None:
        return df
    meta = getattr(df, "attrs", {}).get("price_meta", None)
    if isinstance(meta, dict) and isinstance(meta.get("QQQ5"), dict):
        return df
    source = qqq5_source_from_df(mod, df)
    df.attrs["price_meta"] = {
        "QQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "TQQQ": {"source": "market_or_adjusted", "expense_embedded": True},
        "QQQ5": {
            "source": source,
            "expense_embedded": True,
            "headline_eligible": False,
            "allow_synthetic_qqq5": bool(getattr(args, "allow_synthetic_qqq5", False)) if args is not None else False,
            "disable_qqq5": bool(getattr(args, "disable_qqq5", False)) if args is not None else False,
        },
    }
    return df


def compute_cagr_and_maxdd(eq_series: pd.Series) -> tuple[float, float]:
    if eq_series is None or eq_series.empty:
        return float("nan"), float("nan")
    eq = eq_series.astype(float)
    if eq.empty:
        return float("nan"), float("nan")
    years = max(1e-9, len(eq) / 252.0)
    start_val = float(eq.iloc[0])
    end_val = float(eq.iloc[-1])
    if start_val <= 0 or end_val <= 0:
        cagr = float("nan")
    else:
        cagr = (end_val / start_val) ** (1.0 / years) - 1.0
    maxdd = ((eq / eq.cummax()) - 1.0).min()
    return float(cagr), float(maxdd)

def compute_quality_metrics(eq_obj,
                            tqqq_returns: pd.Series | None = None) -> dict:
    metrics = {
        "DSR": float("nan"),
        "Calmar": float("nan"),
        "Alpha": float("nan"),
        "Alpha_vs_Benchmark_Ann": float("nan"),
        "InformationRatio_vs_Benchmark": float("nan"),
        "TrackingError_vs_Benchmark": float("nan"),
        "CAGR_over_Vol": float("nan"),
        "Sharpe_DailyExcess": float("nan"),
        "Sharpe12m": float("nan"),
        "CAGR": float("nan"),
        "MaxDD": float("nan"),
    }
    if isinstance(eq_obj, pd.DataFrame):
        eq_series = eq_obj.iloc[:, 0].dropna()
    elif isinstance(eq_obj, pd.Series):
        eq_series = eq_obj.dropna()
    else:
        eq_series = pd.Series(dtype=float)
    if eq_series.empty:
        return metrics
    returns = eq_series.pct_change().dropna()
    if not returns.empty:
        metrics["DSR"] = deflated_sharpe_ratio(returns.values)
        std = returns.std(ddof=1)
        if np.isfinite(std) and std > 1e-12:
            metrics["Sharpe_DailyExcess"] = float((returns.mean() / std) * math.sqrt(252.0))
        metrics["Sharpe12m"] = last12m_sharpe(returns)
    cagr, maxdd = compute_cagr_and_maxdd(eq_series)
    metrics["CAGR"] = cagr
    metrics["MaxDD"] = maxdd
    metrics["Calmar"] = calmar_ratio(cagr, maxdd)
    vol = returns.std(ddof=1) * math.sqrt(252.0) if not returns.empty else float("nan")
    if np.isfinite(vol) and abs(vol) > 1e-12:
        metrics["CAGR_over_Vol"] = float(cagr / vol)
    if tqqq_returns is not None and isinstance(tqqq_returns, pd.Series):
        metrics["Alpha"] = daily_alpha_vs_tqqq(returns, tqqq_returns)
        metrics["Alpha_vs_Benchmark_Ann"] = metrics["Alpha"]
        data = pd.concat([returns.rename("strategy"), tqqq_returns.rename("benchmark")], axis=1).dropna()
        if data.shape[0] > 1:
            active = data["strategy"] - data["benchmark"]
            active_std = active.std(ddof=1)
            if np.isfinite(active_std):
                metrics["TrackingError_vs_Benchmark"] = float(active_std * math.sqrt(252.0))
            if np.isfinite(active_std) and active_std > 1e-12:
                metrics["InformationRatio_vs_Benchmark"] = float((active.mean() / active_std) * math.sqrt(252.0))
    return metrics

# ---------- Timeout wrapper (multiprocessing based) ----------
def run_with_timeout(func, kwargs: dict, timeout: int | float | None):
    """
    Runs func(**kwargs) in a separate process; kills it if it exceeds timeout seconds.
    Returns (ok, payload) where ok=False and payload={"timeout": True} on timeout.
    """
    warned_attr = "_warned_direct"
    fallback_attr = "_warned_fallback"

    if os.name == "nt":
        if timeout and timeout > 0 and not getattr(run_with_timeout, warned_attr, False):
            print("[warn] Per-config timeout falls back to direct execution on Windows in this environment.")
            setattr(run_with_timeout, warned_attr, True)
        try:
            seed_everything(GLOBAL_SEED)
            return True, func(**kwargs)
        except Exception as e:
            return False, {"error": repr(e)}

    if not timeout or timeout <= 0:
        try:
            seed_everything(GLOBAL_SEED)
            return True, func(**kwargs)
        except Exception as e:
            return False, {"error": repr(e)}

    try:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
    except Exception as exc:
        if not getattr(run_with_timeout, fallback_attr, False):
            print(f"[warn] Timeout multiprocessing fallback: {exc}; running without timeout.")
            setattr(run_with_timeout, fallback_attr, True)
        try:
            seed_everything(GLOBAL_SEED)
            return True, func(**kwargs)
        except Exception as e:
            return False, {"error": repr(e)}

    parent_conn, child_conn = ctx.Pipe(duplex=False)

    def _worker(conn, fn, fn_kwargs):
        try:
            seed_everything(GLOBAL_SEED)
            out = fn(**fn_kwargs)
            conn.send(("ok", out))
        except Exception as e:
            try:
                conn.send(("err", repr(e)))
            except Exception:
                pass
        finally:
            conn.close()

    proc = ctx.Process(target=_worker, args=(child_conn, func, kwargs))
    proc.daemon = False
    proc.start()
    child_conn.close()

    try:
        if parent_conn.poll(timeout):
            status, payload = parent_conn.recv()
            if status == "ok":
                return True, payload
            return False, {"error": payload}
        else:
            return False, {"timeout": True}
    finally:
        if proc.is_alive():
            proc.terminate()
        proc.join()
        parent_conn.close()

def pareto_front(df: pd.DataFrame, cols_up=("DSR",), cols_down=("MaxDD",)):
    if df.empty:
        return df
    indices = []
    reset = df.reset_index(drop=False)
    for idx_i, row_i in reset.iterrows():
        dominated = False
        for idx_j, row_j in reset.iterrows():
            if idx_i == idx_j:
                continue
            better_up = True
            better_down = True
            strictly_better = False
            for col in cols_up:
                if col not in row_i or col not in row_j or not np.isfinite(row_j[col]):
                    better_up = False
                    break
                if row_j[col] < row_i[col]:
                    better_up = False
                    break
                if row_j[col] > row_i[col]:
                    strictly_better = True
            if not better_up:
                continue
            for col in cols_down:
                if col not in row_i or col not in row_j or not np.isfinite(row_j[col]):
                    better_down = False
                    break
                if row_j[col] > row_i[col]:
                    better_down = False
                    break
                if row_j[col] < row_i[col]:
                    strictly_better = True
            if better_up and better_down and strictly_better:
                dominated = True
                break
        if not dominated:
            indices.append(row_i["index"])
    return df.loc[indices]

def extract_returns(eq_obj) -> pd.Series:
    if isinstance(eq_obj, pd.Series):
        return eq_obj.pct_change().dropna()
    if isinstance(eq_obj, pd.DataFrame) and not eq_obj.empty:
        return eq_obj.iloc[:, 0].pct_change().dropna()
    return pd.Series(dtype=float)

def cfg_from_row(row: pd.Series, param_keys: list[str]) -> dict:
    cfg = {}
    for key in param_keys:
        if key not in row:
            continue
        val = row[key]
        if pd.isna(val):
            continue
        if isinstance(val, (np.generic,)):
            val = val.item()
        cfg[key] = val
    return cfg

def select_diverse_topk(df_candidates: pd.DataFrame,
                        param_keys: list[str],
                        target_count: int,
                        corr_thresh: float,
                        min_keep: int,
                        metric: str,
                        mod,
                        df_full: pd.DataFrame,
                        common_kw: dict,
                        mode: str,
                        timeout_seconds: int | float):
    diag_columns = [
        "rank",
        "cfg_json",
        "metric_value",
        "selected",
        "reason",
        "max_abs_corr",
        "corr_against_selected",
        "n_returns",
        "error",
    ]

    if target_count <= 0 or df_candidates.empty:
        return df_candidates.copy(), pd.DataFrame(columns=diag_columns), set()

    metric_candidates = {
        "dsr": ("DSR",),
        "sharpe": ("Sharpe_DailyExcess", "Sharpe_ex_rf0"),
        "calmar": ("Calmar",),
    }.get(metric.lower(), ("DSR",))
    metric_col = preferred_metric_column(df_candidates, *metric_candidates) or metric_candidates[-1]

    ordered_df = df_candidates.copy()
    if metric_col in ordered_df.columns:
        ordered_df = ordered_df.sort_values(metric_col, ascending=False, na_position="last")
    else:
        ordered_df = ordered_df.sort_values("CAGR", ascending=False, na_position="last")

    target_count = min(target_count, len(ordered_df))
    min_keep = max(0, min(min_keep, target_count))

    selected_indices: list[int] = []
    selected_returns: list[pd.Series] = []
    diag_rows: list[dict] = []
    corr_thresh = float(corr_thresh)

    for rank, (idx, row) in enumerate(ordered_df.iterrows(), start=1):
        cfg = cfg_from_row(row, param_keys)
        cfg_json_val = row.get("cfg_json") or json.dumps(cfg, sort_keys=True)
        metric_val = row.get(metric_col)

        eq_obj, _rep, err = run_backtest(mod, df_full, common_kw, cfg, mode, timeout_seconds)
        returns = extract_returns(eq_obj)

        diag_entry = {
            "rank": rank,
            "cfg_json": cfg_json_val,
            "metric_value": float(metric_val) if isinstance(metric_val, (int, float)) else float("nan"),
            "selected": False,
            "reason": "",
            "max_abs_corr": float("nan"),
            "corr_against_selected": "",
            "n_returns": int(returns.size) if returns is not None else 0,
            "error": err,
        }

        if err or returns is None or returns.empty:
            diag_entry["reason"] = err or "no_returns"
            diag_rows.append(diag_entry)
            continue

        corr_pairs: list[tuple[str, float]] = []
        keep = True

        for chosen_returns, chosen_idx in zip(selected_returns, selected_indices):
            corr = returns.corr(chosen_returns)
            if np.isfinite(corr):
                cfg_sel = ordered_df.loc[chosen_idx, "cfg_json"] if "cfg_json" in ordered_df.columns else json.dumps(cfg_from_row(ordered_df.loc[chosen_idx], param_keys), sort_keys=True)
                corr_pairs.append((cfg_sel, float(corr)))
                if len(selected_indices) >= min_keep and abs(corr) >= corr_thresh:
                    keep = False
                    diag_entry["reason"] = f"corr={corr:.3f} vs {cfg_sel}"
                    break

        if corr_pairs:
            diag_entry["max_abs_corr"] = max(abs(v) for _, v in corr_pairs)
            diag_entry["corr_against_selected"] = json.dumps({cfg: val for cfg, val in corr_pairs})
        else:
            diag_entry["max_abs_corr"] = 0.0
            diag_entry["corr_against_selected"] = json.dumps({})

        if keep or len(selected_indices) < min_keep:
            selected_indices.append(idx)
            selected_returns.append(returns)
            diag_entry["selected"] = True
            if not diag_entry["reason"]:
                diag_entry["reason"] = "kept"
        else:
            if not diag_entry["reason"]:
                diag_entry["reason"] = "correlation_threshold"

        diag_rows.append(diag_entry)

        if len(selected_indices) >= target_count:
            break

    diverse_df = ordered_df.loc[selected_indices].copy()
    selected_cfgs = set(diverse_df["cfg_json"].tolist()) if "cfg_json" in diverse_df.columns else set()
    return diverse_df, pd.DataFrame(diag_rows, columns=diag_columns), selected_cfgs

def run_dl_validation(df_candidates: pd.DataFrame,
                      param_keys: list[str],
                      mod,
                      df_full: pd.DataFrame,
                      common_kw: dict,
                      timeout_seconds: int | float,
                      keep_policy: str):
    records: list[dict] = []
    passed_indices: list[int] = []
    for idx, row in df_candidates.iterrows():
        cfg = cfg_from_row(row, param_keys)
        eq_obj, rep, err = run_backtest(mod, df_full, common_kw, cfg, "dl", timeout_seconds)
        returns = extract_returns(eq_obj)
        dl_cagr = rep.get("CAGR") if isinstance(rep, dict) else None
        dl_maxdd = rep.get("MaxDD") if isinstance(rep, dict) else None
        dl_sharpe = metric_from_report(rep, "Sharpe_DailyExcess", "Sharpe_ex_rf0")
        dl_cagr_over_vol = metric_from_report(rep, "CAGR_over_Vol")
        dl_dsr = deflated_sharpe_ratio(returns.values) if returns is not None and returns.size > 0 else float("-inf")
        baseline_cagr = row.get("CAGR")
        baseline_maxdd = row.get("MaxDD")
        baseline_dsr = row.get("DSR")
        if not isinstance(baseline_dsr, (int, float)):
            baseline_dsr = float("-inf")
        keep = False
        if err:
            keep = False
        else:
            if keep_policy == "improve":
                keep = (dl_cagr is not None and baseline_cagr is not None and float(dl_cagr) >= float(baseline_cagr)) \
                    and (dl_maxdd is not None and baseline_maxdd is not None and float(dl_maxdd) >= float(baseline_maxdd)) \
                    and (dl_dsr >= baseline_dsr)
            else:
                tolerance_cagr = 0.02
                tolerance_dd = 0.03
                tolerance_dsr = 0.10
                cond_cagr = (dl_cagr is not None and baseline_cagr is not None and float(dl_cagr) >= float(baseline_cagr) - tolerance_cagr)
                cond_dd = (dl_maxdd is not None and baseline_maxdd is not None and float(dl_maxdd) >= float(baseline_maxdd) - tolerance_dd)
                cond_dsr = dl_dsr >= baseline_dsr - tolerance_dsr
                keep = cond_cagr and cond_dd and cond_dsr
        record = {
            "index": idx,
            "cfg_json": row.get("cfg_json"),
            "DL_CAGR": dl_cagr,
            "DL_MaxDD": dl_maxdd,
            "DL_Sharpe_DailyExcess": dl_sharpe,
            "DL_Sharpe_ex_rf0": dl_sharpe,
            "DL_CAGR_over_Vol": dl_cagr_over_vol,
            "DL_DSR": dl_dsr,
            "DL_Error": err,
            "baseline_CAGR": baseline_cagr,
            "baseline_MaxDD": baseline_maxdd,
            "baseline_DSR": baseline_dsr,
            "keep": bool(keep),
        }
        records.append(record)
        if keep:
            passed_indices.append(idx)
    eval_df = pd.DataFrame(records)
    filtered = df_candidates.loc[passed_indices].copy()
    return filtered, eval_df

def run_oos_verification(df_candidates: pd.DataFrame,
                         param_keys: list[str],
                         mod,
                         args,
                         span_payload: list[dict],
                         timeout_seconds: int | float,
                         enforce_gate: bool):
    if df_candidates.empty:
        return df_candidates.copy(), pd.DataFrame()

    rows: list[dict] = []
    keep_cfgs: set[str] = set()

    for _, row in df_candidates.iterrows():
        cfg = cfg_from_row(row, param_keys)
        cfg_json_val = row.get("cfg_json") or json.dumps(cfg, sort_keys=True)

        record = {"cfg_json": cfg_json_val}
        primary_pass = False
        primary_error = None
        primary_cagr = None
        primary_maxdd = None

        for idx, span in enumerate(span_payload):
            label = span["label"]
            df_span = span["df"]
            common_kw_span = span["common_kw"]
            t_ret_span = span["tqqq_returns"]

            eq_obj, rep, err = run_backtest(mod, df_span, common_kw_span, cfg, args.mode, timeout_seconds)
            metrics = compute_quality_metrics(eq_obj, t_ret_span)

            cagr_val = metric_from_report(rep, "CAGR") if isinstance(rep, dict) else None
            maxdd_val = metric_from_report(rep, "MaxDD") if isinstance(rep, dict) else None
            sharpe_val = metric_from_report(rep, "Sharpe_DailyExcess", "Sharpe_ex_rf0") if isinstance(rep, dict) else None
            cagr_over_vol_val = metric_from_report(rep, "CAGR_over_Vol") if isinstance(rep, dict) else None
            alpha_val = metric_from_report(rep, "Alpha_vs_Benchmark_Ann", "Alpha", default=metrics["Alpha"]) if isinstance(rep, dict) else metrics["Alpha"]
            ir_val = metric_from_report(rep, "InformationRatio_vs_Benchmark", default=metrics["InformationRatio_vs_Benchmark"]) if isinstance(rep, dict) else metrics["InformationRatio_vs_Benchmark"]

            record[f"OOS_CAGR_{label}"] = cagr_val
            record[f"OOS_MaxDD_{label}"] = maxdd_val
            record[f"OOS_Sharpe_{label}"] = sharpe_val
            record[f"OOS_CAGR_over_Vol_{label}"] = cagr_over_vol_val
            record[f"OOS_DSR_{label}"] = metrics["DSR"]
            record[f"OOS_Calmar_{label}"] = metrics["Calmar"]
            record[f"OOS_Alpha_{label}"] = alpha_val
            record[f"OOS_InformationRatio_{label}"] = ir_val
            record[f"OOS_Sharpe12m_{label}"] = metrics["Sharpe12m"]
            record[f"OOS_Error_{label}"] = err

            if idx == 0:
                primary_error = err
                primary_cagr = cagr_val
                primary_maxdd = maxdd_val
                if not err and isinstance(cagr_val, (int, float)) and isinstance(maxdd_val, (int, float)):
                    primary_pass = (float(cagr_val) >= args.pilot_min_cagr) and (float(maxdd_val) >= args.pilot_max_dd)

        record["OOS_Pass"] = bool(primary_pass and not primary_error)
        rows.append(record)

        if record["OOS_Pass"] or not enforce_gate:
            keep_cfgs.add(cfg_json_val)

    eval_df = pd.DataFrame(rows)

    if not keep_cfgs and not eval_df.empty:
        # fallback: keep highest first span DSR
        first_label = span_payload[0]["label"]
        sort_col = f"OOS_DSR_{first_label}"
        eval_df[sort_col] = pd.to_numeric(eval_df.get(sort_col), errors="coerce")
        eval_df = eval_df.sort_values(sort_col, ascending=False)
        if not eval_df.empty:
            keep_cfgs = {eval_df.iloc[0]["cfg_json"]}

    filtered = df_candidates[df_candidates["cfg_json"].isin(keep_cfgs)].copy()
    if not eval_df.empty:
        filtered = filtered.merge(eval_df, on="cfg_json", how="left")
    filtered = filtered.sort_values(["DSR", "CAGR"], ascending=[False, False], na_position="last")
    return filtered, eval_df


def run_strict_oos_verification(df_candidates: pd.DataFrame,
                                param_keys: list[str],
                                mod,
                                args,
                                span_payload: list[dict],
                                timeout_seconds: int | float,
                                enforce_gate: bool):
    if df_candidates.empty:
        return df_candidates.copy(), pd.DataFrame()
    if not hasattr(mod, "run_strict_oos_slice"):
        raise AttributeError("strategy module must expose run_strict_oos_slice for strict OOS verification.")

    rows: list[dict] = []
    keep_cfgs: set[str] = set()

    for _, row in df_candidates.iterrows():
        cfg = cfg_from_row(row, param_keys)
        cfg_json_val = row.get("cfg_json") or json.dumps(cfg, sort_keys=True)

        record = {"cfg_json": cfg_json_val}
        primary_pass = False
        primary_error = None
        primary_cagr = None
        primary_maxdd = None

        for idx, span in enumerate(span_payload):
            label = span["label"]
            common_kw_span = dict(span["common_kw"])
            common_kw_span.update(cfg)
            strict_kwargs = {
                "df": span["df"],
                "train_start": span["train_start"],
                "train_end": span["train_end"],
                "test_start": span["test_start"],
                "test_end": span["test_end"],
                "mode": args.mode,
                "policy": "bandit",
                "common_kw": common_kw_span,
                "initial_train_end": common_kw_span.get("initial_train_end"),
            }
            ok, payload = run_with_timeout(mod.run_strict_oos_slice, strict_kwargs, timeout_seconds)
            if not ok:
                err = "timeout" if isinstance(payload, dict) and payload.get("timeout") else (
                    payload.get("error") if isinstance(payload, dict) else repr(payload)
                )
                eq_obj, rep = None, {}
            else:
                err = None
                eq_obj = payload.get("equity_oos") if isinstance(payload, dict) else None
                rep = payload.get("report_oos", {}) if isinstance(payload, dict) else {}
                if not isinstance(rep, dict):
                    rep = {}
            metrics = compute_quality_metrics(eq_obj, span.get("tqqq_returns"))

            cagr_val = metric_from_report(rep, "CAGR", default=metrics.get("CAGR")) if isinstance(rep, dict) else metrics.get("CAGR")
            maxdd_val = metric_from_report(rep, "MaxDD", default=metrics.get("MaxDD")) if isinstance(rep, dict) else metrics.get("MaxDD")
            sharpe_val = metric_from_report(rep, "Sharpe_DailyExcess", "Sharpe_ex_rf0") if isinstance(rep, dict) else None
            cagr_over_vol_val = metric_from_report(rep, "CAGR_over_Vol", default=metrics.get("CAGR_over_Vol")) if isinstance(rep, dict) else metrics.get("CAGR_over_Vol")
            alpha_val = metric_from_report(rep, "Alpha_vs_Benchmark_Ann", "Alpha", default=metrics["Alpha"]) if isinstance(rep, dict) else metrics["Alpha"]
            ir_val = metric_from_report(rep, "InformationRatio_vs_Benchmark", default=metrics["InformationRatio_vs_Benchmark"]) if isinstance(rep, dict) else metrics["InformationRatio_vs_Benchmark"]

            record[f"OOS_CAGR_{label}"] = cagr_val
            record[f"OOS_MaxDD_{label}"] = maxdd_val
            record[f"OOS_Sharpe_{label}"] = sharpe_val
            record[f"OOS_CAGR_over_Vol_{label}"] = cagr_over_vol_val
            record[f"OOS_DSR_{label}"] = metrics["DSR"]
            record[f"OOS_Calmar_{label}"] = metrics["Calmar"]
            record[f"OOS_Alpha_{label}"] = alpha_val
            record[f"OOS_InformationRatio_{label}"] = ir_val
            record[f"OOS_Sharpe12m_{label}"] = metrics["Sharpe12m"]
            record[f"OOS_Error_{label}"] = err

            if idx == 0:
                primary_error = err
                primary_cagr = cagr_val
                primary_maxdd = maxdd_val
                if not err and isinstance(cagr_val, (int, float)) and isinstance(maxdd_val, (int, float)):
                    primary_pass = (float(cagr_val) >= args.pilot_min_cagr) and (float(maxdd_val) >= args.pilot_max_dd)

        record["OOS_Pass"] = bool(primary_pass and not primary_error)
        rows.append(record)
        if record["OOS_Pass"] or not enforce_gate:
            keep_cfgs.add(cfg_json_val)

    eval_df = pd.DataFrame(rows)
    if not keep_cfgs and not eval_df.empty:
        first_label = span_payload[0]["label"]
        sort_col = f"OOS_DSR_{first_label}"
        eval_df[sort_col] = pd.to_numeric(eval_df.get(sort_col), errors="coerce")
        eval_df = eval_df.sort_values(sort_col, ascending=False)
        if not eval_df.empty:
            keep_cfgs = {eval_df.iloc[0]["cfg_json"]}

    filtered = df_candidates[df_candidates["cfg_json"].isin(keep_cfgs)].copy()
    if not eval_df.empty:
        filtered = filtered.merge(eval_df, on="cfg_json", how="left")
    filtered = filtered.sort_values(["DSR", "CAGR"], ascending=[False, False], na_position="last")
    return filtered, eval_df


def build_manifest(df_final: pd.DataFrame,
                   param_keys: list[str],
                   dl_eval: pd.DataFrame | None = None,
                   oos_eval: pd.DataFrame | None = None,
                   metadata: dict | None = None) -> dict:
    dl_map = {}
    if dl_eval is not None and not dl_eval.empty:
        for _, row in dl_eval.iterrows():
            dl_map[row.get("cfg_json")] = {k: row.get(k) for k in [
                "DL_CAGR",
                "DL_MaxDD",
                "DL_Sharpe_DailyExcess",
                "DL_Sharpe_ex_rf0",
                "DL_CAGR_over_Vol",
                "DL_DSR",
                "DL_Error",
                "keep",
            ]}
    oos_map = {}
    if oos_eval is not None and not oos_eval.empty:
        for _, row in oos_eval.iterrows():
            oos_map[row.get("cfg_json")] = {k: row.get(k) for k in row.index if k != "cfg_json"}
    manifest = []
    for _, row in df_final.iterrows():
        cfg = cfg_from_row(row, param_keys)
        cfg_json = row.get("cfg_json")
        entry = {
            "config": cfg,
            "det_seed": row.get("det_seed"),
            "baseline": {
                "CAGR": row.get("CAGR"),
                "MaxDD": row.get("MaxDD"),
                "Sharpe_DailyExcess": row.get("Sharpe_DailyExcess"),
                "Sharpe_ex_rf0": row.get("Sharpe_ex_rf0"),
                "CAGR_over_Vol": row.get("CAGR_over_Vol"),
                "DSR": row.get("DSR"),
                "Calmar": row.get("Calmar"),
            },
        }
        if cfg_json in dl_map:
            entry["dl"] = dl_map[cfg_json]
        if cfg_json in oos_map:
            entry["oos"] = oos_map[cfg_json]
        else:
            oos_cols = {k: row.get(k) for k in row.index if isinstance(k, str) and k.startswith("OOS_")}
            if oos_cols:
                entry["oos"] = oos_cols
        if "oos_flag" in row:
            entry["oos_flag"] = row.get("oos_flag")
        manifest.append(entry)
    return {
        "metadata": metadata or {},
        "entries": manifest,
    }

def derive_span_label(start: str, end: str, index: int, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    try:
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end)
        years = abs((end_dt - start_dt).days) / 365.25
    except Exception:
        years = None
    if index == 0 and start.startswith("2010"):
        return "2010p"
    if years is not None and abs(years - 5.0) < 0.35:
        return "5y"
    label = start.replace("-", "")
    if not label:
        label = f"span{index+1}"
    return label

def parse_oos_spans(args) -> list[tuple[str, str, str]]:
    spans: list[tuple[str, str, str]] = []
    if args.oos_spans:
        parts = [p.strip() for p in args.oos_spans.split(",") if p.strip()]
        for idx, part in enumerate(parts):
            label = None
            if "@" in part:
                se, label = part.split("@", 1)
            else:
                se = part
            if ":" not in se:
                continue
            start, end = [s.strip() for s in se.split(":", 1)]
            label = label.strip() if label else derive_span_label(start, end, idx)
            spans.append((label, start, end))
    else:
        primary_label = derive_span_label(args.oos_start, args.end, 0)
        spans.append((primary_label, args.oos_start, args.end))
        try:
            start_5y = (pd.to_datetime(args.end) - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
        except Exception:
            start_5y = args.oos_start
        spans.append((derive_span_label(start_5y, args.end, 1, fallback="5y"), start_5y, args.end))
    return spans


def parse_strict_oos_spans(args) -> list[tuple[str, str, str, str, str]]:
    spans: list[tuple[str, str, str, str, str]] = []
    raw = getattr(args, "strict_oos_spans", None)
    if raw:
        parts = [p.strip() for p in str(raw).split(",") if p.strip()]
        for idx, part in enumerate(parts):
            label = None
            if "@" in part:
                se, label = part.split("@", 1)
            else:
                se = part
            fields = [s.strip() for s in se.split(":")]
            if len(fields) != 4 or not all(fields):
                raise ValueError(
                    "--strict-oos-spans entries must use "
                    "train_start:train_end:test_start:test_end@label"
                )
            train_start, train_end, test_start, test_end = fields
            label = label.strip() if label else f"strict{idx+1}"
            spans.append((label, train_start, train_end, test_start, test_end))
    else:
        train_start = getattr(args, "start", None)
        train_end = getattr(args, "initial_train_end", None)
        test_start = getattr(args, "oos_start", None)
        test_end = getattr(args, "end", None)
        if not all([train_start, train_end, test_start, test_end]):
            raise ValueError("strict OOS default span requires start, initial_train_end, oos_start, and end.")
        spans.append(("strict", train_start, train_end, test_start, test_end))
    return spans


def oos_span_initial_train_end(span_start: str) -> str:
    return pd.Timestamp(span_start).strftime("%Y-%m-%d")


def common_kw_for_oos_span(common_kw: dict, span_start: str) -> dict:
    out = dict(common_kw)
    out["initial_train_end"] = oos_span_initial_train_end(span_start)
    return out

def config_hash(cfg: dict) -> str:
    return sha1(json.dumps(cfg, sort_keys=True))

def fmt_float(val):
    try:
        f = float(val)
        if math.isfinite(f):
            return f"{f:.4f}"
    except Exception:
        pass
    return "nan"


def apply_price_lag(df: pd.DataFrame, lag_days: int) -> pd.DataFrame:
    lag = int(lag_days)
    if lag <= 0:
        return df
    shifted = df.shift(lag)
    return shifted.iloc[lag:].copy()


def apply_return_noise(df: pd.DataFrame, noise_std: float, seed: int) -> pd.DataFrame:
    std = float(noise_std)
    if not math.isfinite(std) or std <= 0.0:
        return df
    numeric_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
    ]
    if not numeric_cols:
        return df
    working = df[numeric_cols].astype(float).copy()
    working = working.ffill().bfill()
    if working.isnull().any().any():
        return df
    returns = working.pct_change().fillna(0.0)
    rng = np.random.default_rng(int(seed))
    noise = rng.normal(0.0, std, size=returns.shape)
    noise_df = pd.DataFrame(noise, index=returns.index, columns=returns.columns)
    perturbed = returns + noise_df
    if not perturbed.empty:
        perturbed.iloc[0] = 0.0
    perturbed = perturbed.clip(lower=-0.95)
    factors = (1.0 + perturbed).cumprod()
    base = working.iloc[0]
    prices = factors.mul(base, axis=1)
    result = df.copy()
    result.loc[:, numeric_cols] = prices
    return result

def apply_preset_v5e(args, parser):
    preset_values = {
        "pilot_years": 5,
        "pilot_two_windows": True,
        "pilot_min_cagr": 0.28,
        "pilot_max_dd": -0.80,
        "pilot_relative": True,
        "rel_cagr_mult": 0.90,
        "rel_dd_mult": 1.00,
        "pilot_min_dsr": 0.95,
        "pilot_min_calmar": 0.40,
        "pilot_last12m_min_sharpe": 0.60,
        "alpha_min": 0.05,
        "topk": 25,
        "max_runs": 180,
    }
    for field, value in preset_values.items():
        try:
            default_val = parser.get_default(field)
        except (AttributeError, KeyError):
            default_val = None
        current = getattr(args, field, None)
        if current == default_val:
            setattr(args, field, value)

def prepare_dataset(mod, start_date, end_date, args):
    cache_dir = Path("cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{start_date}_{end_date}"
    df_cache = cache_dir / f"prices_{sha1(key)}.parquet"
    rf_cache = cache_dir / f"rf_{sha1(key)}.parquet"
    vix_cache = cache_dir / f"vix_{sha1(key)}.parquet"
    adv_cache = cache_dir / f"adv_{sha1(key)}.parquet"

    if df_cache.exists():
        df = pd.read_parquet(df_cache)
    else:
        class Dummy: pass
        _a = Dummy(); _a.start = start_date; _a.end = end_date
        _a.qqq_csv = _a.tqqq_csv = _a.qqq5_csv = None
        _a.disable_qqq5 = bool(getattr(args, "disable_qqq5", False))
        _a.allow_synthetic_qqq5 = bool(getattr(args, "allow_synthetic_qqq5", False))
        df, _synth5 = mod.load_prices(_a)
        price_meta = getattr(df, "attrs", {}).get("price_meta", None)
        df = df.loc[(df.index >= start_date) & (df.index <= end_date)]
        if price_meta is not None:
            df.attrs["price_meta"] = price_meta
        df.to_parquet(df_cache)
    df = ensure_price_meta(mod, df, args)
    price_meta = getattr(df, "attrs", {}).get("price_meta", None)

    rf_series = cache_get(rf_cache)
    if rf_series is None:
        rf_series = mod.fetch_rf_series_or_default(start_date, end_date)
        if isinstance(rf_series, (pd.Series, pd.DataFrame)):
            cache_put(rf_series, rf_cache)

    vix_series = cache_get(vix_cache)
    if vix_series is None:
        vix_series = mod.fetch_vix_series_or_none(start_date, end_date)
        if isinstance(vix_series, (pd.Series, pd.DataFrame)):
            cache_put(vix_series, vix_cache)

    adv_series = cache_get(adv_cache)
    if adv_series is None:
        adv_series = mod.fetch_tqqq_adv_series(start_date, end_date, lookback=20)
        if isinstance(adv_series, (pd.Series, pd.DataFrame)):
            cache_put(adv_series, adv_cache)

    df_processed = df
    lag_days = getattr(args, "price_lag_days", 0)
    if isinstance(lag_days, (int, float)) and int(lag_days) > 0:
        df_processed = apply_price_lag(df_processed, int(lag_days))
    noise_std = float(getattr(args, "return_noise_std", 0.0) or 0.0)
    if noise_std > 0.0 and len(df_processed) > 0:
        stress_seed = getattr(args, "stress_seed", None)
        if stress_seed is None:
            stress_seed = getattr(args, "det_seed", None)
        if stress_seed is None:
            stress_seed = getattr(args, "seed", GLOBAL_SEED)
        df_processed = apply_return_noise(df_processed, noise_std, int(stress_seed))
    df = df_processed
    if price_meta is not None:
        df.attrs["price_meta"] = price_meta
    df = ensure_price_meta(mod, df, args)
    qqq5_source = qqq5_source_from_df(mod, df)

    if isinstance(rf_series, (pd.Series, pd.DataFrame)):
        rf_series = rf_series.reindex(df.index).ffill().bfill()
    if isinstance(vix_series, (pd.Series, pd.DataFrame)):
        vix_series = vix_series.reindex(df.index).ffill().bfill()
    if isinstance(adv_series, (pd.Series, pd.DataFrame)):
        adv_series = adv_series.reindex(df.index).ffill().bfill()

    common_kw = dict(
        rf_series=rf_series if isinstance(rf_series, (pd.Series, pd.DataFrame)) else None,
        rf_ann=0.02 if not isinstance(rf_series, (pd.Series, pd.DataFrame)) else None,
        kelly_lookback_days=252*3,
        tqqq_expense=0.009,
        qqq5_expense=0.0095,
        deduct_tqqq_expense_in_returns=bool(getattr(args, "deduct_tqqq_expense_in_returns", False)),
        deduct_qqq5_expense_in_returns=bool(getattr(args, "deduct_qqq5_expense_in_returns", False)),
        max_effective_leverage=getattr(args, "max_effective_leverage", None),
        disable_qqq5=bool(getattr(args, "disable_qqq5", False)),
        allow_synthetic_qqq5=bool(getattr(args, "allow_synthetic_qqq5", False)),
        qqq5_source=qqq5_source,
        use_risk_gate=args.risk_gate,
        vix_series=vix_series if isinstance(vix_series, (pd.Series, pd.DataFrame)) else None,
        dl_conf=args.dl_conf,
        dl_max_qqq5=0.35,
        dl_max_tqqq=0.60,
        dl_trade_max_frac=0.35,
        dl_cooldown=3,
        adv_series=adv_series if isinstance(adv_series, (pd.Series, pd.DataFrame)) else None,
        slip_bps=1.0,
        impact_k=0.0005,
    )
    slip_mult = float(getattr(args, "slippage_mult", 1.0) or 0.0)
    if slip_mult < 0.0:
        slip_mult = 0.0
    if slip_mult != 1.0:
        common_kw["slip_bps"] = float(common_kw.get("slip_bps", 0.0)) * slip_mult
        common_kw["impact_k"] = float(common_kw.get("impact_k", 0.0)) * slip_mult

    return df, common_kw

def build_backtest_kwargs(df, common_kw, cfg, mode, policy_mode='bandit'):
    mode = (mode or "baseline").lower()
    bt_kwargs = dict(df=df, policy_mode=policy_mode, **common_kw, **cfg)
    if mode == "dl":
        cutoff = bt_kwargs.get("initial_train_end")
        if cutoff is None or str(cutoff).strip() == "":
            raise ValueError("initial_train_end is required for robust_fast mode=dl.")
    else:
        bt_kwargs.pop("initial_train_end", None)
    return bt_kwargs


def run_backtest(mod, df, common_kw, cfg, mode, timeout_seconds):
    fn = mod.baseline_backtest if mode == "baseline" else mod.deep_learning_backtest
    try:
        bt_kwargs = build_backtest_kwargs(df, common_kw, cfg, mode, policy_mode='bandit')
    except ValueError as exc:
        return None, None, str(exc)
    ok, payload = run_with_timeout(fn, bt_kwargs, timeout_seconds)
    if not ok:
        if isinstance(payload, dict) and payload.get("timeout"):
            return None, None, "timeout"
        err_msg = payload.get("error") if isinstance(payload, dict) else repr(payload)
        return None, None, err_msg
    result = payload
    if isinstance(result, (list, tuple)) and len(result) >= 3:
        eq = result[0]
        rep = result[2]
        return eq, rep, None
    return None, None, "unexpected_result"

# ---------- 5) 早停评估 ----------
def eval_with_early_stop(mod, df, common_kw, cfg, cutoff_cagr: float,
                         fractions=(0.35, 0.70, 1.00), optimism=0.15,
                         mode="baseline", timeout_seconds: int | float | None = None):
    """
    分段跑 35%/70%/100% 数据；若前段的“最乐观外推”也不可能进榜，则早停。
    optimism：给部分周期一个 +15% 的乐观裕度。
    """
    report_final = None
    eq_final = None
    fn = mod.baseline_backtest if mode == "baseline" else mod.deep_learning_backtest
    for frac in fractions:
        n = max(200, int(len(df) * frac))
        df_sub = df.iloc[:n]
        try:
            bt_kwargs = build_backtest_kwargs(df_sub, common_kw, cfg, mode, policy_mode='bandit')
        except ValueError as exc:
            return {"early_stopped": True, "report": {"Error": str(exc)}, "equity": None}
        ok, payload = run_with_timeout(fn, bt_kwargs, timeout_seconds)
        if not ok:
            if isinstance(payload, dict) and payload.get("timeout"):
                return {"early_stopped": True, "report": {"Error": "timeout"}, "equity": None}
            err_msg = payload.get("error") if isinstance(payload, dict) else repr(payload)
            return {"early_stopped": True, "report": {"Error": err_msg}, "equity": None}
        res = payload
        if not isinstance(res, (list, tuple)) or len(res) < 3:
            return {"early_stopped": True, "report": {"Error": "unexpected_result"}, "equity": None}
        eq, dd, rep = res[0], res[1], res[2]
        # 计算子样本 CAGR
        years = max(1e-9, len(eq) / 252.0)
        cagr = (float(eq.iloc[-1]) / float(eq.iloc[0])) ** (1.0 / years) - 1.0
        report_final = rep
        eq_final = eq

        # 非最终段，做早停判断
        if frac < 1.0:
            # 最乐观外推（+optimism）仍显著落后，则停止
            if (cagr + optimism) < cutoff_cagr:
                return {"early_stopped": True, "report": rep, "equity": eq_final}
    return {"early_stopped": False, "report": report_final, "equity": eq_final}

# ---------- 6) 主流程 ----------
def build_gate_signature(args, qqq5_source_for_run, effective_seed):
    return {
        "pilot_min_cagr": args.pilot_min_cagr,
        "pilot_max_dd": args.pilot_max_dd,
        "pilot_relative": bool(args.pilot_relative),
        "disable_pilot_gate": bool(args.disable_pilot_gate),
        "pilot_years": args.pilot_years,
        "rel_cagr_mult": args.rel_cagr_mult,
        "rel_dd_mult": args.rel_dd_mult,
        "pilot_two_windows": bool(args.pilot_two_windows),
        "pilot_min_dsr": args.pilot_min_dsr,
        "pilot_min_calmar": args.pilot_min_calmar,
        "pilot_last12m_min_sharpe": args.pilot_last12m_min_sharpe,
        "alpha_min": args.alpha_min,
        "slippage_mult": args.slippage_mult,
        "return_noise_std": args.return_noise_std,
        "price_lag_days": args.price_lag_days,
        "stress_seed": args.stress_seed if args.stress_seed is not None else effective_seed,
        "stress_auto_relax": bool(args.stress_auto_relax),
        "max_effective_leverage": args.max_effective_leverage,
        "disable_qqq5": bool(args.disable_qqq5),
        "allow_synthetic_qqq5": bool(args.allow_synthetic_qqq5),
        "qqq5_source": qqq5_source_for_run,
        "initial_train_end": args.initial_train_end,
        "strict_oos_verify": bool(getattr(args, "strict_oos_verify", False)),
        "strict_oos_spans": getattr(args, "strict_oos_spans", None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", type=str, required=True,
                    help="你的策略文件路径（含 baseline_backtest/deep_learning_backtest 等）")
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--mode", type=str, default="baseline", choices=["baseline", "dl"])
    ap.add_argument("--risk-gate", action="store_true", help="开启风控门")
    ap.add_argument("--no-risk-gate", dest="risk_gate", action="store_false")
    ap.set_defaults(risk_gate=True)

    # 运行与性能
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--max-runs", type=int, default=120, help="最多尝试的组合数（快筛前上限）")
    ap.add_argument("--pilot-years", type=int, default=1, help="先用近 N 年做快筛")
    ap.add_argument("--topk", type=int, default=20, help="仅保留前 K 组合做全量表")
    ap.add_argument("--fractions", type=str, default="0.35,0.55,0.70,1.00", help="早停分段，逗号分隔")
    ap.add_argument("--optimism", type=float, default=0.15, help="子样本外推的乐观裕度")
    ap.add_argument("--slippage-mult", type=float, default=1.0,
                    help="乘以交易费用/滑点/冲击系数的倍数（例如 2.0 表示成本翻倍）")
    ap.add_argument("--return-noise-std", type=float, default=0.0,
                    help="在价格路径上注入的日收益噪声标准差（高波动压力测试）")
    ap.add_argument("--price-lag-days", type=int, default=0,
                    help="将价格序列整体后移 N 天，模拟执行延迟")
    ap.add_argument("--stress-seed", type=int, default=None,
                    help="压力测试噪声的随机种子（默认复用 det-seed 或 seed）")
    ap.add_argument("--stress-auto-relax", action="store_true", default=True,
                    help="压力测试时自动放宽 pilot 门槛（默认开启）")
    ap.add_argument("--no-stress-auto-relax", dest="stress_auto_relax", action="store_false")
    ap.add_argument("--dl-conf", type=float, default=0.62, help="传给 DL 覆盖的阈值（若 mode=dl）")
    ap.add_argument("--initial-train-end", dest="initial_train_end", type=str, default="2018-12-31",
                    help="Required cutoff for all deep-learning backtests.")
    ap.add_argument("--deduct-tqqq-expense-in-returns", dest="deduct_tqqq_expense_in_returns",
                    action="store_true",
                    help="Explicit stress override: subtract tqqq_expense/252 from TQQQ price returns.")
    ap.add_argument("--deduct-qqq5-expense-in-returns", dest="deduct_qqq5_expense_in_returns",
                    action="store_true",
                    help="Explicit stress override: subtract qqq5_expense/252 from QQQ5 price returns.")
    ap.add_argument("--max-effective-leverage", dest="max_effective_leverage", type=float, default=None,
                    help="Optional strict audit cap on next-day target effective leverage, e.g. 3.0")
    ap.add_argument("--disable-qqq5", dest="disable_qqq5", action="store_true",
                    help="Disable all QQQ5 sleeve allocation paths for strict headline audits.")
    ap.add_argument("--allow-synthetic-qqq5", dest="allow_synthetic_qqq5", action="store_true",
                    help="Allow synthetic/hybrid QQQ5 for research only; not headline eligible.")
    ap.add_argument("--pilot-min-cagr", type=float, default=0.10, help="Pilot minimum CAGR threshold")
    ap.add_argument("--pilot-max-dd", type=float, default=-0.70, help="Pilot maximum drawdown threshold")
    ap.add_argument("--pilot-relative", action="store_true",
                    help="Allow pilot pass if relative to TQQQ performance")
    ap.add_argument("--rel-cagr-mult", type=float, default=0.95,
                    help="Pilot relative CAGR multiplier vs TQQQ")
    ap.add_argument("--rel-dd-mult", type=float, default=1.00,
                    help="Pilot relative MaxDD multiplier vs TQQQ (negative drawdown)")
    ap.add_argument("--pilot-min-dsr", type=float, default=0.95,
                    help="Pilot minimum deflated Sharpe ratio")
    ap.add_argument("--pilot-min-calmar", type=float, default=0.45,
                    help="Pilot minimum Calmar ratio (CAGR/|MaxDD|)")
    ap.add_argument("--pilot-last12m-min-sharpe", type=float, default=0.40,
                    help="Pilot minimum Sharpe over the last ~252 trading days")
    ap.add_argument("--alpha-min", type=float, default=0.05,
                    help="Minimum annualized daily alpha vs TQQQ within pilot window")
    ap.add_argument("--pilot-two-windows", action="store_true",
                    help="Split pilot window into two halves; require both to pass")
    ap.add_argument("--disable-pilot-gate", action="store_true", help="Skip the pilot gate entirely")
    ap.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    ap.add_argument("--timeout-seconds", type=int, default=900,
                    help="Per-configuration timeout in seconds (0 disables)")
    ap.add_argument("--grid-json", type=str, default=None,
                    help="Optional JSON file mapping param -> list of values; overrides default grid if provided")
    ap.add_argument("--preset-v5e", action="store_true",
                    help="Apply stricter v5e pilot/full gates (overrides defaults unless explicitly set)")
    ap.add_argument("--topk-diverse", action="store_true",
                    help="Enable correlation-aware Top-K pruning")
    ap.add_argument("--diversity-thresh", type=float, default=0.85,
                    help="Maximum allowed absolute correlation between equity curves")
    ap.add_argument("--diversity-min-k", type=int, default=10,
                    help="Minimum number of configs to keep before enforcing correlation threshold")
    ap.add_argument("--diversity-metric", type=str, default="dsr", choices=["dsr","sharpe","calmar"],
                    help="Metric used to rank candidates before diversity pruning")
    ap.add_argument("--det-seed", type=int, default=None,
                    help="Optional deterministic seed override (defaults to --seed)")
    ap.add_argument("--min-trades", type=int, default=0,
                    help="Minimum number of trades required for a config to remain eligible")
    ap.add_argument("--max-turnover", type=float, default=None,
                    help="Maximum turnover allowed for a config (if reported)")
    ap.add_argument("--oos-start", type=str, default="2010-01-01",
                    help="Out-of-sample verification start date (used with --oos-verify)")
    ap.add_argument("--oos-spans", type=str, default=None,
                    help="Comma-separated start:end or start:end@label spans for OOS verification")
    ap.add_argument("--oos-verify", action="store_true",
                    help="Re-run kept configs on [oos-start, end] and export OOS summary/Pareto")
    ap.add_argument("--strict-oos-verify", action="store_true",
                    help="Run OOS verification through strategy.run_strict_oos_slice instead of legacy full-path slicing")
    ap.add_argument("--strict-oos-spans", type=str, default=None,
                    help="Comma-separated train_start:train_end:test_start:test_end@label strict OOS spans")
    ap.add_argument("--oos-prune", action="store_true",
                    help="Drop configs whose OOS quality breaches degradation limits")
    ap.add_argument("--oos-degrade-ratio", type=float, default=0.75,
                    help="Minimum allowed OOS/IS ratio for DSR and Calmar before flagging degradation")
    ap.add_argument("--validate-dl", action="store_true",
                    help="Run deep-learning backtests for keepers and filter based on DL performance")
    ap.add_argument("--dl-keep-if", type=str, default="nonworse",
                    choices=["nonworse", "improve"],
                    help="DL pass criteria (nonworse: allow slight degradation; improve: require improvements)")
    ap.add_argument("--final-shortlist", action="store_true",
                    help="Run v5e preset + diversity + optional DL/OOS and emit final manifest")
    ap.add_argument("--out-prefix", type=str, default="robust_fast")
    ap.add_argument("--resume", type=str, default="robust_ckpt.json", help="断点文件")
    args = ap.parse_args()

    defaults = {}
    for field in [
        "pilot_min_cagr",
        "pilot_max_dd",
        "pilot_min_dsr",
        "pilot_min_calmar",
        "pilot_last12m_min_sharpe",
        "alpha_min",
    ]:
        try:
            defaults[field] = ap.get_default(field)
        except (AttributeError, KeyError):
            defaults[field] = getattr(args, field, None)
    overrides = {field: (getattr(args, field, None) != defaults.get(field)) for field in defaults}

    if args.final_shortlist and not args.preset_v5e:
        args.preset_v5e = True
    if args.preset_v5e:
        apply_preset_v5e(args, ap)
    if args.final_shortlist:
        args.topk_diverse = True
        if not args.validate_dl:
            args.validate_dl = True
        if not args.oos_verify:
            args.oos_verify = True
    if args.strict_oos_verify:
        args.oos_verify = True

    stress_triggers = []
    if args.slippage_mult > 1.0:
        stress_triggers.append("slippage")
    if args.return_noise_std > 0.0:
        stress_triggers.append("noise")
    if args.price_lag_days > 0:
        stress_triggers.append("lag")
    auto_relax_notes: list[str] = []
    if args.stress_auto_relax and stress_triggers and not args.disable_pilot_gate:
        stress_intensity = max(
            0.0,
            args.slippage_mult - 1.0,
            args.return_noise_std * 90.0,
            args.price_lag_days * 0.3,
        )
        stress_intensity = min(stress_intensity, 2.5)
        if args.slippage_mult >= 1.8 or stress_intensity >= 1.8:
            args.disable_pilot_gate = True
            auto_relax_notes.append("disable_pilot_gate")
        else:
            if not overrides["pilot_min_cagr"]:
                target_val = -0.05 - 0.15 * stress_intensity
                if args.pilot_min_cagr > target_val:
                    args.pilot_min_cagr = target_val
                    auto_relax_notes.append(f"pilot_min_cagr->{args.pilot_min_cagr:.2f}")
            if not overrides["pilot_max_dd"]:
                target_val = -0.90 - 0.25 * stress_intensity
                if args.pilot_max_dd > target_val:
                    args.pilot_max_dd = target_val
                    auto_relax_notes.append(f"pilot_max_dd->{args.pilot_max_dd:.2f}")
            if not overrides["pilot_min_dsr"]:
                target_val = 0.35 - 0.15 * stress_intensity
                if args.pilot_min_dsr > target_val:
                    args.pilot_min_dsr = target_val
                    auto_relax_notes.append(f"pilot_min_dsr->{args.pilot_min_dsr:.2f}")
            if not overrides["pilot_min_calmar"]:
                target_val = 0.20
                if args.pilot_min_calmar > target_val:
                    args.pilot_min_calmar = target_val
                    auto_relax_notes.append(f"pilot_min_calmar->{args.pilot_min_calmar:.2f}")
            if not overrides["pilot_last12m_min_sharpe"]:
                target_val = 0.20
                if args.pilot_last12m_min_sharpe > target_val:
                    args.pilot_last12m_min_sharpe = target_val
                    auto_relax_notes.append(f"pilot_last12m_min_sharpe->{args.pilot_last12m_min_sharpe:.2f}")
            if not overrides["alpha_min"]:
                target_val = 0.0
                if args.alpha_min > target_val:
                    args.alpha_min = target_val
                    auto_relax_notes.append(f"alpha_min->{args.alpha_min:.2f}")
        if auto_relax_notes:
            print(f"[stress-relax] triggers={','.join(stress_triggers)} | {'; '.join(auto_relax_notes)}")

    effective_seed = args.det_seed if args.det_seed is not None else args.seed
    os.environ["PYTHONHASHSEED"] = str(effective_seed)
    random.seed(effective_seed)
    np.random.seed(effective_seed)
    try:
        import torch  # type: ignore
        torch.manual_seed(effective_seed)
    except Exception:
        pass

    global GLOBAL_SEED
    GLOBAL_SEED = effective_seed

    print(f"[init] seed={effective_seed}, threads={args.threads}, mode={args.mode}, period={args.start}->{args.end}")
    set_thread_limits(args.threads)
    mod = import_strategy_module(args.strategy)

    # 统一准备价格 & RF/VIX/ADV，只做一次并缓存
    cache_dir = Path("cache")
    cache_dir.mkdir(exist_ok=True)
    key = f"{args.start}_{args.end}"
    df_cache = cache_dir / f"prices_{sha1(key)}.parquet"
    rf_cache = cache_dir / f"rf_{sha1(key)}.parquet"
    vix_cache = cache_dir / f"vix_{sha1(key)}.parquet"
    adv_cache = cache_dir / f"adv_{sha1(key)}.parquet"

    if df_cache.exists():
        df = pd.read_parquet(df_cache)
    else:
        # 用你的 load_prices()，自动混合 QQQ/ TQQQ / QQQ5
        class Dummy: pass
        _a = Dummy(); _a.start = args.start; _a.end = args.end
        _a.qqq_csv = _a.tqqq_csv = _a.qqq5_csv = None
        _a.disable_qqq5 = bool(args.disable_qqq5)
        _a.allow_synthetic_qqq5 = bool(args.allow_synthetic_qqq5)
        df, _synth5 = mod.load_prices(_a)
        price_meta = getattr(df, "attrs", {}).get("price_meta", None)
        df = df.loc[(df.index >= args.start) & (df.index <= args.end)]
        if price_meta is not None:
            df.attrs["price_meta"] = price_meta
        df.to_parquet(df_cache)
    df = ensure_price_meta(mod, df, args)
    qqq5_source_for_run = qqq5_source_from_df(mod, df)

    rf_series = cache_get(rf_cache)
    if rf_series is None:
        rf_series = mod.fetch_rf_series_or_default(args.start, args.end)
        if isinstance(rf_series, (pd.Series, pd.DataFrame)):
            cache_put(rf_series, rf_cache)

    vix_series = cache_get(vix_cache)
    if vix_series is None:
        vix_series = mod.fetch_vix_series_or_none(args.start, args.end)
        if isinstance(vix_series, (pd.Series, pd.DataFrame)):
            cache_put(vix_series, vix_cache)

    adv_series = cache_get(adv_cache)
    if adv_series is None:
        adv_series = mod.fetch_tqqq_adv_series(args.start, args.end, lookback=20)
        if isinstance(adv_series, (pd.Series, pd.DataFrame)):
            cache_put(adv_series, adv_cache)

    # 统一公共参数（与主策略一致）
    # 这里不改动你的核心假设，尽量少参数；其他交给 cfg 网格
    common_kw = dict(
        rf_series=rf_series if isinstance(rf_series, (pd.Series, pd.DataFrame)) else None,
        rf_ann=0.02 if not isinstance(rf_series, (pd.Series, pd.DataFrame)) else None,
        kelly_lookback_days=252*3,
        tqqq_expense=0.009,
        qqq5_expense=0.0095,
        # trade_cost_bps / bandit_alpha / fut_fin_spread / target_vol 由 cfg 控制
        use_risk_gate=args.risk_gate,
        vix_series=vix_series if isinstance(vix_series, (pd.Series, pd.DataFrame)) else None,
        dl_conf=args.dl_conf,
        deduct_tqqq_expense_in_returns=bool(args.deduct_tqqq_expense_in_returns),
        deduct_qqq5_expense_in_returns=bool(args.deduct_qqq5_expense_in_returns),
        max_effective_leverage=args.max_effective_leverage,
        disable_qqq5=bool(args.disable_qqq5),
        allow_synthetic_qqq5=bool(args.allow_synthetic_qqq5),
        qqq5_source=qqq5_source_for_run,
        initial_train_end=args.initial_train_end,
        dl_max_qqq5=0.35,
        dl_max_tqqq=0.60,
        dl_trade_max_frac=0.35,
        dl_cooldown=3,
        adv_series=adv_series if isinstance(adv_series, (pd.Series, pd.DataFrame)) else None,
        slip_bps=1.0,
        impact_k=0.0005
    )

    # 断点续跑
    ckpt_path = Path(args.resume)
    gate_signature = build_gate_signature(args, qqq5_source_for_run, effective_seed)
    print(f"[gate] {gate_signature}")
    results_map: dict[str, dict] = {}
    done_keys: set[str] = set()
    requeued = 0
    if ckpt_path.exists():
        try:
            raw_entries = json.loads(ckpt_path.read_text())
            for entry in raw_entries:
                cfg_key = entry.get("cfg")
                if not isinstance(cfg_key, dict):
                    continue
                key = json.dumps(cfg_key, sort_keys=True)
                prev_gate = entry.get("gate_signature")
                should_requeue = False
                if args.disable_pilot_gate and entry.get("pilot_pass") is False:
                    should_requeue = True
                elif entry.get("pilot_pass") is False and prev_gate != gate_signature:
                    should_requeue = True
                if should_requeue:
                    requeued += 1
                    continue
                results_map[key] = entry
            done_keys = set(results_map.keys())
            if done_keys:
                print(f"[resume] 载入已完成 {len(done_keys)} 个组合")
            if requeued:
                print(f"[resume] 因门槛变更重新排队 {requeued} 个组合")
        except Exception:
            pass

    # 快筛窗口（近 pilot-years 年）
    # Pilot window using the most recent pilot-years
    n_pilot = max(252, min(len(df), args.pilot_years * 252))
    df_pilot = df.iloc[-n_pilot:]

    # Parameter grid
    grid_override = load_grid_from_json(args.grid_json)
    grid_dict, grid = build_grid(grid_override)
    param_keys = list(grid_dict.keys())
    if args.max_runs > 0:
        grid = grid[: args.max_runs]
    print(f"[grid] evaluating up to {len(grid)} combos across {len(param_keys)} parameters")

    timeout_seconds = max(0, int(args.timeout_seconds))

    results = list(results_map.values())
    top_cagrs: list[float] = []
    for r in results:
        rep_val = (r.get("rep") or {}).get("CAGR")
        if isinstance(rep_val, (int, float)):
            top_cagrs.append(float(rep_val))
    best_cagr_seen = max(top_cagrs + [-1e9])
    cutoff_k = max(5, args.topk)

    pilot_records: list[dict] = []
    # 主循环
    t0 = time.time()
    keep_rows = []
    frac_list = tuple(float(x) for x in args.fractions.split(","))
    for i, cfg in enumerate(grid, 1):
        cfg_eff = dict(cfg)
        if args.slippage_mult != 1.0:
            for cost_key in ("trade_cost_bps", "fut_fin_spread"):
                if cost_key in cfg_eff and cfg_eff[cost_key] is not None:
                    try:
                        cfg_eff[cost_key] = float(cfg_eff[cost_key]) * args.slippage_mult
                    except Exception:
                        pass
        cfg = cfg_eff
        key = json.dumps(cfg, sort_keys=True)
        if key in done_keys:
            continue

        pilot_error = ""
        pilot_rep: dict = {}
        pilot_eq = None

        def run_pilot_segment(df_seg):
            bt_kwargs = build_backtest_kwargs(df_seg, common_kw, cfg, args.mode, policy_mode='bandit')
            if args.mode == "baseline":
                pilot_result = mod.baseline_backtest(**bt_kwargs)
            else:
                pilot_result = mod.deep_learning_backtest(**bt_kwargs)
            eq_seg, _dd_seg, rep_seg_raw = pilot_result[0], pilot_result[1], pilot_result[2]
            rep_seg = dict(rep_seg_raw)
            cagr_val = float(rep_seg.get("CAGR", -1e9))
            maxdd_val = float(rep_seg.get("MaxDD", -1e9))
            ok_seg = (cagr_val >= args.pilot_min_cagr) and (maxdd_val >= args.pilot_max_dd)

            returns_series = None
            if isinstance(eq_seg, pd.Series):
                returns_series = eq_seg.pct_change().dropna()
            elif isinstance(eq_seg, pd.DataFrame) and not eq_seg.empty:
                returns_series = eq_seg.iloc[:, 0].pct_change().dropna()

            t_ret = None
            if args.pilot_relative and not df_seg.empty and ("TQQQ" in df_seg.columns):
                t_ret = tqqq_price_returns_for_benchmark(df_seg, common_kw)
                t_eq = (1.0 + t_ret).cumprod()
                years = max(1e-9, len(t_eq) / 252.0)
                t_cagr = float(t_eq.iloc[-1]) ** (1.0 / years) - 1.0
                t_mdd = ((t_eq / t_eq.cummax()) - 1.0).min()
                rel_ok = (cagr_val >= args.rel_cagr_mult * max(t_cagr, 0.0)) and (maxdd_val >= args.rel_dd_mult * t_mdd)
                ok_seg = ok_seg and rel_ok
            else:
                t_ret = None

            dsr_val = deflated_sharpe_ratio(returns_series.values) if returns_series is not None else float("-inf")
            calmar_val = metric_from_report(rep_seg, "Calmar", default=calmar_ratio(cagr_val, maxdd_val))
            sharpe12_val = last12m_sharpe(returns_series) if isinstance(returns_series, pd.Series) else float("-inf")
            alpha_fallback = daily_alpha_vs_tqqq(returns_series, t_ret) if (isinstance(returns_series, pd.Series) and isinstance(t_ret, pd.Series)) else float("-inf")
            alpha_val = metric_from_report(rep_seg, "Alpha_vs_Benchmark_Ann", "Alpha", default=alpha_fallback)
            ir_val = metric_from_report(rep_seg, "InformationRatio_vs_Benchmark", default=float("nan"))

            ok_seg = ok_seg and (dsr_val >= args.pilot_min_dsr) and (calmar_val >= args.pilot_min_calmar) \
                     and (sharpe12_val >= args.pilot_last12m_min_sharpe) and (alpha_val >= args.alpha_min)

            rep_seg["pilot_DSR"] = dsr_val
            rep_seg["pilot_Calmar"] = calmar_val
            rep_seg["pilot_Last12mSharpe"] = sharpe12_val
            rep_seg["pilot_Alpha_Ann"] = alpha_val
            rep_seg["pilot_InformationRatio"] = ir_val

            return ok_seg, rep_seg, eq_seg

        try:
            pilot_ok, pilot_rep_full, pilot_eq = run_pilot_segment(df_pilot)
            pilot_rep = pilot_rep_full
            if args.pilot_two_windows and len(df_pilot) >= 504:
                mid_idx = len(df_pilot) // 2
                ok1, rep1, _eq1 = run_pilot_segment(df_pilot.iloc[:mid_idx])
                ok2, rep2, _eq2 = run_pilot_segment(df_pilot.iloc[mid_idx:])
                pilot_ok = pilot_ok and ok1 and ok2
                pilot_rep = {"full": pilot_rep_full, "win1": rep1, "win2": rep2}
        except Exception as e:
            pilot_ok = False
            pilot_error = str(e)
            pilot_rep = {}
            pilot_eq = None

        if args.disable_pilot_gate:
            pilot_ok = True

        pilot_dsr = float(pilot_rep.get("pilot_DSR", float("nan"))) if isinstance(pilot_rep, dict) else float("nan")

        pilot_records.append({
            "cfg": cfg,
            "report": pilot_rep,
            "passed": pilot_ok,
            "equity": pilot_eq,
            "pilot_dsr": pilot_dsr,
        })

        entry_base = {"cfg": cfg, "gate_signature": gate_signature, "pilot_report": pilot_rep, "pilot_dsr": pilot_dsr}

        if not pilot_ok:
            entry = dict(entry_base)
            entry.update({"pilot_pass": False, "rep": {}})
            if pilot_error:
                entry["error"] = pilot_error
            results_map[key] = entry
            done_keys.add(key)
            ckpt_path.write_text(json.dumps(list(results_map.values())))
            continue

        cutoff_cagr = best_cagr_seen
        if len(top_cagrs) >= cutoff_k:
            cutoff_cagr = sorted(top_cagrs, reverse=True)[cutoff_k - 1]

        try:
            out = eval_with_early_stop(
                mod, df, common_kw, cfg, cutoff_cagr,
                fractions=frac_list, optimism=args.optimism, mode=args.mode,
                timeout_seconds=timeout_seconds
            )
            rep = out["report"]
            if isinstance(rep, dict) and rep.get("Error"):
                entry = dict(entry_base)
                entry.update({"pilot_pass": True, "rep": {}, "error": rep["Error"]})
                results_map[key] = entry
                done_keys.add(key)
                ckpt_path.write_text(json.dumps(list(results_map.values())))
                continue
            eq = out.get("equity")
            row = dict(cfg)
            row.update(rep)
            row["cfg_json"] = json.dumps(cfg, sort_keys=True)
            row["early_stopped"] = bool(out["early_stopped"])
            dsr_val = float("nan")
            if isinstance(eq, pd.Series):
                dsr_val = deflated_sharpe_ratio(eq.pct_change().dropna().values)
            elif isinstance(eq, pd.DataFrame) and not eq.empty:
                dsr_val = deflated_sharpe_ratio(eq.iloc[:, 0].pct_change().dropna().values)
            row["DSR"] = dsr_val

            liquidity_flags: list[str] = []
            trades_val = rep.get("Trades") or rep.get("trades")
            if isinstance(trades_val, (int, float)) and args.min_trades and trades_val < args.min_trades:
                liquidity_flags.append("min_trades")
            turnover_val = None
            for key_name in ("Turnover", "turnover", "AvgTurnover", "avg_turnover"):
                if key_name in rep:
                    turnover_val = rep.get(key_name)
                    break
            if turnover_val is not None and args.max_turnover is not None:
                try:
                    if float(turnover_val) > float(args.max_turnover):
                        liquidity_flags.append("max_turnover")
                except Exception:
                    pass

            if liquidity_flags:
                entry = dict(entry_base)
                entry.update({
                    "pilot_pass": True,
                    "rep": rep,
                    "early_stopped": out["early_stopped"],
                    "dsr": dsr_val,
                    "liquidity_flag": ",".join(liquidity_flags)
                })
                results_map[key] = entry
                done_keys.add(key)
                ckpt_path.write_text(json.dumps(list(results_map.values())))
                continue

            row["det_seed"] = GLOBAL_SEED
            keep_rows.append(row)

            cagr_val = rep.get("CAGR")
            if isinstance(cagr_val, (int, float)):
                cagr_num = float(cagr_val)
                best_cagr_seen = max(best_cagr_seen, cagr_num)
                top_cagrs.append(cagr_num)

            entry = dict(entry_base)
            entry.update({"pilot_pass": True, "rep": rep, "early_stopped": out["early_stopped"], "dsr": dsr_val})
            results_map[key] = entry
        except Exception as e:
            entry = dict(entry_base)
            entry.update({"pilot_pass": True, "rep": {}, "error": str(e)})
            results_map[key] = entry
            done_keys.add(key)
            ckpt_path.write_text(json.dumps(list(results_map.values())))
            continue

        done_keys.add(key)
        ckpt_path.write_text(json.dumps(list(results_map.values())))

        if i % 8 == 0:
            time.sleep(3)

    if not keep_rows and not args.disable_pilot_gate:
        fallback = []
        for rec in pilot_records:
            if rec["passed"]:
                continue
            report_raw = rec.get("report") or {}
            if isinstance(report_raw, dict) and "full" in report_raw:
                primary_rep = report_raw.get("full") or {}
            else:
                primary_rep = report_raw
            cagr = primary_rep.get("CAGR")
            maxdd = primary_rep.get("MaxDD")
            pilot_dsr = rec.get("pilot_dsr")
            if isinstance(pilot_dsr, (int, float)) and np.isfinite(pilot_dsr):
                score = float(pilot_dsr)
            elif isinstance(cagr, (int, float)) and isinstance(maxdd, (int, float)):
                score = float(cagr) - 0.3 * abs(float(maxdd))
            else:
                continue
            fallback.append((score, rec))
        if fallback:
            fallback.sort(key=lambda x: x[0], reverse=True)
            take = max(1, int(len(fallback) * 0.15))
            print(f"[pilot] No configs passed gate; promoting top {take} fallback candidates.")
            for _, rec in fallback[:take]:
                cfg = rec["cfg"]
                key = json.dumps(cfg, sort_keys=True)
                pilot_rep = rec.get("report") or {}
                entry_base = {"cfg": cfg, "gate_signature": gate_signature, "pilot_report": pilot_rep, "fallback": True}
                try:
                    cutoff_cagr_fb = best_cagr_seen
                    if len(top_cagrs) >= cutoff_k:
                        cutoff_cagr_fb = sorted(top_cagrs, reverse=True)[cutoff_k - 1]
                    out = eval_with_early_stop(
                        mod, df, common_kw, cfg, cutoff_cagr_fb,
                        fractions=frac_list, optimism=args.optimism, mode=args.mode,
                        timeout_seconds=timeout_seconds
                    )
                    rep = out["report"]
                    if isinstance(rep, dict) and rep.get("Error"):
                        entry = dict(entry_base)
                        entry.update({"pilot_pass": True, "rep": {}, "error": rep["Error"]})
                        results_map[key] = entry
                        continue
                    eq = out.get("equity")
                    row = dict(cfg)
                    row.update(rep)
                    row["cfg_json"] = json.dumps(cfg, sort_keys=True)
                    row["early_stopped"] = bool(out["early_stopped"])
                    dsr_val = float("nan")
                    if isinstance(eq, pd.Series):
                        dsr_val = deflated_sharpe_ratio(eq.pct_change().dropna().values)
                    elif isinstance(eq, pd.DataFrame) and not eq.empty:
                        dsr_val = deflated_sharpe_ratio(eq.iloc[:, 0].pct_change().dropna().values)
                    row["DSR"] = dsr_val

                    liquidity_flags = []
                    trades_val = rep.get("Trades") or rep.get("trades")
                    if isinstance(trades_val, (int, float)) and args.min_trades and trades_val < args.min_trades:
                        liquidity_flags.append("min_trades")
                    turnover_val = None
                    for key_name in ("Turnover", "turnover", "AvgTurnover", "avg_turnover"):
                        if key_name in rep:
                            turnover_val = rep.get(key_name)
                            break
                    if turnover_val is not None and args.max_turnover is not None:
                        try:
                            if float(turnover_val) > float(args.max_turnover):
                                liquidity_flags.append("max_turnover")
                        except Exception:
                            pass

                    if liquidity_flags:
                        entry = dict(entry_base)
                        entry.update({
                            "pilot_pass": True,
                            "rep": rep,
                            "early_stopped": out["early_stopped"],
                            "dsr": dsr_val,
                            "liquidity_flag": ",".join(liquidity_flags)
                        })
                        results_map[key] = entry
                        done_keys.add(key)
                        ckpt_path.write_text(json.dumps(list(results_map.values())))
                        continue

                    row["det_seed"] = GLOBAL_SEED
                    keep_rows.append(row)
                    cagr_val = rep.get("CAGR")
                    if isinstance(cagr_val, (int, float)):
                        cagr_num = float(cagr_val)
                        best_cagr_seen = max(best_cagr_seen, cagr_num)
                        top_cagrs.append(cagr_num)
                    entry = dict(entry_base)
                    entry.update({"pilot_pass": True, "rep": rep, "early_stopped": out["early_stopped"], "dsr": dsr_val})
                    results_map[key] = entry
                except Exception as e:
                    entry = dict(entry_base)
                    entry.update({"pilot_pass": True, "rep": {}, "error": str(e)})
                    results_map[key] = entry
                done_keys.add(key)
                ckpt_path.write_text(json.dumps(list(results_map.values())))

    if not keep_rows:
        print("[warn] No results passed all gates; emitting empty artifacts.")

    df_res = pd.DataFrame(keep_rows)
    baseline_columns = list(dict.fromkeys(param_keys + [
        "CAGR", "AnnVol", "Vol", "Sharpe_DailyExcess", "Sharpe_ex_rf0",
        "CAGR_over_Vol", "Sortino_DailyExcess", "MaxDD", "Calmar",
        "Alpha_vs_Benchmark_Ann", "InformationRatio_vs_Benchmark",
        "TrackingError_vs_Benchmark", "Trades",
        "Final_Equity", "DSR", "det_seed", "cfg_json", "early_stopped"
    ]))
    if df_res.empty:
        df_res = pd.DataFrame(columns=baseline_columns)
    else:
        for col in baseline_columns:
            if col not in df_res.columns:
                df_res[col] = pd.Series(dtype=float if col not in param_keys and col not in ("cfg_json",) else object)

    if "DSR" not in df_res.columns:
        df_res["DSR"] = pd.Series(dtype=float)
    df_res["DSR"] = pd.to_numeric(df_res["DSR"], errors="coerce")
    if "CAGR" not in df_res.columns:
        df_res["CAGR"] = pd.Series(dtype=float)
    df_res = df_res.sort_values(["DSR", "CAGR"], ascending=[False, False], na_position="last")
    out_prefix = args.out_prefix
    summary_path = Path(f"{out_prefix}_summary.csv")
    summary_path.write_text(df_res.to_csv(index=False))

    df_pareto = pareto_front(df_res, cols_up=("DSR",), cols_down=("MaxDD",))
    pareto_path = Path(f"{out_prefix}_pareto.csv")
    pareto_path.write_text(df_pareto.to_csv(index=False))

    df_topk_raw = df_res.head(args.topk).copy()
    topk_raw_path = Path(f"{out_prefix}_topk_raw.csv")
    topk_raw_path.write_text(df_topk_raw.to_csv(index=False))

    for entry in results_map.values():
        entry.setdefault("selected_diverse", False)

    diag_columns = ["rank", "cfg_json", "metric_value", "selected", "reason", "max_abs_corr", "corr_against_selected", "n_returns", "error"]
    diverse_eval_df = pd.DataFrame(columns=diag_columns)
    selected_cfgs: set[str] = set()
    df_diverse = df_topk_raw.copy()
    if args.topk_diverse and not df_topk_raw.empty:
        df_diverse, diverse_eval_df, selected_cfgs = select_diverse_topk(
            df_topk_raw,
            param_keys,
            int(args.topk),
            float(args.diversity_thresh),
            int(args.diversity_min_k),
            args.diversity_metric,
            mod,
            df,
            common_kw,
            args.mode,
            timeout_seconds,
        )
        print(f"[diverse] topk={args.topk}, selected={len(df_diverse)} with corr<thresh {args.diversity_thresh}")
    diverse_eval_path = Path(f"{out_prefix}_diverse_eval.csv")
    diverse_eval_path.write_text(diverse_eval_df.to_csv(index=False))

    for key, entry in results_map.items():
        entry["selected_diverse"] = bool(key in selected_cfgs)
    ckpt_path.write_text(json.dumps(list(results_map.values())))

    topk_diverse_path = Path(f"{out_prefix}_topk_diverse.csv")
    topk_diverse_path.write_text(df_diverse.to_csv(index=False))

    df_selected = df_diverse.copy()
    dl_eval_df: pd.DataFrame | None = None
    dl_eval_path = None
    if args.validate_dl:
        dl_eval_path = Path(f"{out_prefix}_dl_eval.csv")
        dl_columns = [
            "cfg_json",
            "DL_CAGR",
            "DL_MaxDD",
            "DL_Sharpe_ex_rf0",
            "DL_DSR",
            "DL_Error",
            "baseline_CAGR",
            "baseline_MaxDD",
            "baseline_DSR",
            "keep",
        ]
        if df_selected.empty:
            dl_eval_df = pd.DataFrame(columns=dl_columns)
            dl_eval_path.write_text(dl_eval_df.to_csv(index=False))
        else:
            df_selected, dl_eval_df = run_dl_validation(
                df_selected,
                param_keys,
                mod,
                df,
                common_kw,
                timeout_seconds,
                args.dl_keep_if,
            )
            dl_eval_path.write_text(dl_eval_df.to_csv(index=False))
            print(f"[dl] kept {len(df_selected)} after DL validation (policy={args.dl_keep_if})")
    elif args.mode == "dl":
        dl_eval_path = Path(f"{out_prefix}_dl_eval.csv")
        dl_eval_df = df_selected.copy()
        dl_eval_path.write_text(dl_eval_df.to_csv(index=False))

    oos_eval_df: pd.DataFrame | None = None
    oos_eval_path = None
    span_defs: list[tuple[str, str, str]] = []
    span_payload: list[dict] = []
    if args.oos_verify:
        span_payload = []
        if args.strict_oos_verify:
            strict_span_defs = parse_strict_oos_spans(args)
            span_defs = [(label, test_start, test_end) for label, _train_start, _train_end, test_start, test_end in strict_span_defs]
            for label, train_start, train_end, test_start, test_end in strict_span_defs:
                df_span, common_kw_span = prepare_dataset(mod, train_start, test_end, args)
                common_kw_span["initial_train_end"] = train_end
                tqqq_df = df_span.loc[(df_span.index >= test_start) & (df_span.index <= test_end)]
                tqqq_ret = tqqq_price_returns_for_benchmark(tqqq_df, common_kw_span)
                span_payload.append({
                    "label": label,
                    "train_start": train_start,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                    "start": test_start,
                    "end": test_end,
                    "df": df_span,
                    "common_kw": common_kw_span,
                    "tqqq_returns": tqqq_ret,
                })
        else:
            span_defs = parse_oos_spans(args)
            for label, span_start, span_end in span_defs:
                df_span, common_kw_span = prepare_dataset(mod, span_start, span_end, args)
                common_kw_span = common_kw_for_oos_span(common_kw_span, span_start)
                tqqq_ret = tqqq_price_returns_for_benchmark(df_span, common_kw_span)
                span_payload.append({
                    "label": label,
                    "start": span_start,
                    "end": span_end,
                    "df": df_span,
                    "common_kw": common_kw_span,
                    "tqqq_returns": tqqq_ret,
                })

        oos_eval_path = Path(f"{out_prefix}_oos.csv")
        if span_payload:
            if args.strict_oos_verify:
                df_selected, oos_eval_df = run_strict_oos_verification(
                    df_selected,
                    param_keys,
                    mod,
                    args,
                    span_payload,
                    timeout_seconds,
                    enforce_gate=bool(args.final_shortlist),
                )
            else:
                df_selected, oos_eval_df = run_oos_verification(
                    df_selected,
                    param_keys,
                    mod,
                    args,
                    span_payload,
                    timeout_seconds,
                    enforce_gate=bool(args.final_shortlist),
                )
            print(f"[oos] spans={[(p['label'], p['start'], p['end']) for p in span_payload]} -> {len(df_selected)} configs")
        else:
            oos_eval_df = pd.DataFrame()

        if oos_eval_df is None or oos_eval_df.empty:
            oos_columns = ["cfg_json", "OOS_Pass"]
            for payload in span_payload:
                label = payload["label"]
                oos_columns.extend([
                    f"OOS_CAGR_{label}",
                    f"OOS_MaxDD_{label}",
                    f"OOS_Sharpe_{label}",
                    f"OOS_CAGR_over_Vol_{label}",
                    f"OOS_DSR_{label}",
                    f"OOS_Calmar_{label}",
                    f"OOS_Alpha_{label}",
                    f"OOS_InformationRatio_{label}",
                    f"OOS_Sharpe12m_{label}",
                    f"OOS_Error_{label}",
                ])
            oos_eval_df = pd.DataFrame(columns=list(dict.fromkeys(oos_columns)))
        oos_eval_path.write_text(oos_eval_df.to_csv(index=False))

    if args.oos_verify:
        if "oos_flag" not in df_selected.columns:
            df_selected["oos_flag"] = ""
        if span_payload and not df_selected.empty:
            primary_label = span_payload[0]["label"]
            dsr_col = f"OOS_DSR_{primary_label}"
            calmar_col = f"OOS_Calmar_{primary_label}"
            if dsr_col in df_selected.columns and calmar_col in df_selected.columns:
                dsr_is = pd.to_numeric(df_selected["DSR"], errors="coerce").abs()
                calmar_is = pd.to_numeric(df_selected["Calmar"], errors="coerce").abs()
                dsr_os = pd.to_numeric(df_selected[dsr_col], errors="coerce").abs()
                calmar_os = pd.to_numeric(df_selected[calmar_col], errors="coerce").abs()
                ratio_dsr = dsr_os / dsr_is.replace(0, np.nan)
                ratio_calmar = calmar_os / calmar_is.replace(0, np.nan)
                degrade_mask = (ratio_dsr < args.oos_degrade_ratio) | (ratio_calmar < args.oos_degrade_ratio)
                degrade_mask = degrade_mask | dsr_os.isna() | calmar_os.isna()
                df_selected.loc[degrade_mask.fillna(False), "oos_flag"] = "degraded"
                if args.oos_prune:
                    df_selected = df_selected.loc[~degrade_mask.fillna(False)].copy()
        else:
            if "oos_flag" not in df_selected.columns:
                df_selected["oos_flag"] = ""
    else:
        if "oos_flag" not in df_selected.columns:
            df_selected["oos_flag"] = ""

    if "det_seed" not in df_selected.columns:
        df_selected["det_seed"] = GLOBAL_SEED

    if not df_selected.empty:
        df_selected = df_selected.sort_values(["DSR", "CAGR"], ascending=[False, False], na_position="last")
    if df_selected.empty:
        print("[warn] No configurations remained after optional filters.")

    final_path = Path(f"{out_prefix}_topk_final.csv")
    final_path.write_text(df_selected.to_csv(index=False))
    Path(f"{out_prefix}_topk.csv").write_text(df_selected.to_csv(index=False))

    try:
        strategy_text = Path(args.strategy).read_text(encoding="utf-8", errors="ignore")
        strategy_sha = sha1(strategy_text)
    except Exception:
        strategy_sha = None
    try:
        grid_signature = sha1(json.dumps(grid_dict, sort_keys=True, default=str))
    except Exception:
        grid_signature = None
    span_meta = []
    if span_payload:
        for payload in span_payload:
            meta_row = {
                "label": payload.get("label"),
                "start": payload.get("start"),
                "end": payload.get("end"),
            }
            if args.strict_oos_verify:
                meta_row.update({
                    "train_start": payload.get("train_start"),
                    "train_end": payload.get("train_end"),
                    "test_start": payload.get("test_start"),
                    "test_end": payload.get("test_end"),
                })
            span_meta.append(meta_row)
    elif span_defs:
        span_meta = [{"label": lbl, "start": st, "end": ed} for (lbl, st, ed) in span_defs]
    metadata = {
        "strategy_path": str(Path(args.strategy)),
        "strategy_sha": strategy_sha,
        "grid_source": "json" if grid_override else "default",
        "grid_path": args.grid_json,
        "grid_sha": grid_signature,
        "gate_signature": gate_signature,
        "seed": args.seed,
        "det_seed": GLOBAL_SEED,
        "start": args.start,
        "end": args.end,
        "mode": args.mode,
        "threads": args.threads,
        "topk": args.topk,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "diversity": {
            "enabled": bool(args.topk_diverse),
            "thresh": args.diversity_thresh,
            "min_k": args.diversity_min_k,
            "metric": args.diversity_metric,
        },
        "oos_verify": bool(args.oos_verify),
        "strict_oos_verify": bool(args.strict_oos_verify),
        "oos_spans": span_meta,
        "strict_oos_spans": args.strict_oos_spans,
        "initial_train_end": args.initial_train_end,
        "oos_degrade_ratio": args.oos_degrade_ratio,
        "oos_prune": bool(args.oos_prune),
        "min_trades": args.min_trades,
        "max_turnover": args.max_turnover,
        "max_effective_leverage": args.max_effective_leverage,
        "disable_qqq5": bool(args.disable_qqq5),
        "allow_synthetic_qqq5": bool(args.allow_synthetic_qqq5),
        "qqq5_source": qqq5_source_for_run,
        "preset_v5e": bool(args.preset_v5e),
        "final_shortlist": bool(args.final_shortlist),
        "stress": {
            "slippage_mult": args.slippage_mult,
            "return_noise_std": args.return_noise_std,
            "price_lag_days": args.price_lag_days,
            "stress_seed": args.stress_seed if args.stress_seed is not None else effective_seed,
            "auto_relax": bool(args.stress_auto_relax),
        },
    }
    manifest_obj = build_manifest(df_selected, param_keys, dl_eval_df, oos_eval_df, metadata=metadata)
    manifest_path = Path(f"{out_prefix}_manifest.json")
    manifest_path.write_text(json.dumps(manifest_obj, indent=2))
    print(f"[manifest] entries={len(manifest_obj.get('entries', []))}")

    dt = time.time() - t0
    print(f"\n[done] total candidates: {len(grid)}, kept: {len(df_res)} | elapsed: {dt/60:.1f} min")
    display_cols = [c for c in [
        "CAGR",
        "MaxDD",
        "Sharpe_DailyExcess",
        "Sharpe_ex_rf0",
        "CAGR_over_Vol",
        "DSR",
        "trade_cost_bps",
        "fut_fin_spread",
        "target_vol",
        "base_kelly_frac",
        "bandit_alpha",
        "early_stopped",
    ] if c in df_topk_raw.columns]
    print(df_topk_raw[display_cols])
    saved_files = [
        summary_path.name,
        pareto_path.name,
        topk_raw_path.name,
        topk_diverse_path.name,
        final_path.name,
        f"{out_prefix}_topk.csv",
        args.resume,
    ]
    if diverse_eval_path is not None:
        saved_files.append(diverse_eval_path.name)
    if dl_eval_path is not None:
        saved_files.append(dl_eval_path.name)
    if args.oos_verify:
        if oos_eval_path is not None:
            saved_files.append(oos_eval_path.name)
    if manifest_path is not None:
        saved_files.append(manifest_path.name)
    print(f"\nSaved: {', '.join(saved_files)}")

if __name__ == "__main__":
    try:
        from multiprocessing import freeze_support
        freeze_support()
    except Exception:
        pass
    if os.name == "nt":
        try:
            import multiprocessing as mp  # type: ignore
            if mp.get_start_method(allow_none=True) != "spawn":
                mp.set_start_method("spawn", force=True)
        except Exception:
            pass
    main()
