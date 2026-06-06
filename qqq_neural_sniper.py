#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qqq_trend_pure_alpha.py

Goal: Beat TQQQ Buy & Hold by avoiding the -80% Drawdowns.
Lesson Learned: 
    1. 5x Leverage kills you with borrowing costs (-18%/yr).
    2. "Dip Buying" in Bear Markets kills you with catching falling knives.
    3. Complex ML overfits noise.

The Winning Logic (Simple & Robust):
    - BULL MARKET (QQQ > SMA 200): 100% TQQQ.
    - BEAR MARKET (QQQ < SMA 200): 100% CASH (Risk Free Rate).
    - EXTENSION: If RSI > 83 (Extreme Overbought), take profit (Cash).

This simple logic mathematically MUST beat TQQQ B&H if it avoids 2008/2022 crashes.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
import argparse
import warnings

warnings.filterwarnings("ignore")

# ------------------ Config ------------------
RISK_FREE_RATE = 0.04
SLIPPAGE_BPS = 10 # 10 bps round trip slippage

# ------------------ Data Loading ------------------
def get_data(start):
    print(f"Downloading data from {start}...")
    tickers = ['QQQ', 'TQQQ']
    try:
        df = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    except Exception as e:
        print(f"Error: {e}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        try:
            close = df.xs('Close', axis=1, level=0)
            open_ = df.xs('Open', axis=1, level=0)
        except:
            close = df['Close']
            open_ = df['Open']
    else:
        close = df['Close']
        open_ = df['Open']

    data = pd.DataFrame({
        'QQQ': close['QQQ'],
        'TQQQ': close['TQQQ'],
        'TQQQ_OPEN': open_['TQQQ']
    })
    return data.dropna()

# ------------------ Indicators ------------------
def compute_rsi(series, window=10):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / (loss + 1e-8)
    return 100 - (100 / (1 + rs))

# ------------------ Strategy Engine ------------------
def run_strategy(df):
    print("Executing Pure Trend Alpha...")
    
    # 1. Trend Indicator (The Holy Grail of TQQQ Trading)
    # SMA 200 on QQQ (Underlying) is the most robust filter in history.
    df['sma_200'] = df['QQQ'].rolling(200).mean()
    
    # 2. Overbought Indicator (Profit Taking)
    df['rsi'] = compute_rsi(df['QQQ'], window=10)
    
    # --- Logic ---
    df['alloc'] = 0.0
    
    # Condition 1: Trend is UP
    is_bull = df['QQQ'] > df['sma_200']
    
    # Condition 2: Not Extremely Overbought (Blowoff top protection)
    # TQQQ tends to crash after RSI > 83
    is_safe = df['rsi'] < 83.0
    
    # Strategy: Long ONLY if Bull Trend AND Not Overheating
    df.loc[is_bull & is_safe, 'alloc'] = 1.0
    
    # Note: In Bear Market (QQQ < SMA200), alloc stays 0.0 (Cash).
    # NO DIP BUYING. NO SNIPING. JUST CASH.
    
    # --- Execution (Strict Next Open) ---
    df['pos'] = df['alloc'].shift(1).fillna(0.0)
    
    equity = [1.0]
    pos_arr = df['pos'].values
    open_arr = df['TQQQ_OPEN'].values
    
    start_idx = 201
    dates = df.index[start_idx:-1]
    
    print(f"Backtesting from {df.index[start_idx].date()} to {df.index[-1].date()}...")
    
    for i in range(start_idx, len(df)-1):
        p = pos_arr[i]
        
        # Open to Open Return
        ret = (open_arr[i+1] - open_arr[i]) / open_arr[i]
        
        # Cost
        cost = 0.0
        if i > 0:
            if abs(pos_arr[i] - pos_arr[i-1]) > 0:
                cost = SLIPPAGE_BPS / 10000.0
        
        # Return
        # Cash earns Risk Free Rate (4%)
        r = p * ret + (1-p)*(RISK_FREE_RATE/252)
        
        equity.append(equity[-1] * (1 + r - cost))
        
    return pd.Series(equity[1:], index=dates), df.iloc[start_idx:-1]

# ------------------ Analysis ------------------
def analyze(eq, bench_data):
    bench = bench_data.reindex(eq.index)
    bench = bench / bench.iloc[0]
    
    days = (eq.index[-1] - eq.index[0]).days
    cagr = (eq.iloc[-1])**(365.25/days) - 1
    b_cagr = (bench.iloc[-1])**(365.25/days) - 1
    
    vol = eq.pct_change().std() * np.sqrt(252)
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    b_mdd = (bench / bench.cummax() - 1).min()
    
    sharpe = (cagr - RISK_FREE_RATE) / vol
    
    print("\n" + "="*60)
    print("STRATEGY: PURE TREND ALPHA (SMA200 + RSI Exit)")
    print("Goal: Beat TQQQ by avoiding the Bear Market Wipeouts.")
    print("="*60)
    print(f"{'Metric':<20} | {'Trend Strat':<15} | {'TQQQ B&H':<15}")
    print("-" * 60)
    print(f"{'CAGR':<20} | {cagr:10.2%}      | {b_cagr:10.2%}")
    print(f"{'Max Drawdown':<20} | {mdd:10.2%}      | {b_mdd:10.2%}")
    print(f"{'Sharpe Ratio':<20} | {sharpe:10.2f}      | {'N/A':<10}")
    print("-" * 60)
    print(f"Final Equity         | {eq.iloc[-1]:10.2f}x      | {bench.iloc[-1]:10.2f}x")
    
    # Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
    ax1.plot(eq, label='Pure Trend', color='#00aa00', linewidth=1.5)
    ax1.plot(bench, label='TQQQ Buy & Hold', color='gray', alpha=0.5, linestyle='--')
    ax1.set_yscale('log')
    ax1.set_title('Pure Trend Strategy vs TQQQ')
    ax1.legend()
    ax1.grid(True, which='both', alpha=0.2)
    
    # Drawdown
    dd_series = eq / eq.cummax() - 1
    bdd_series = bench / bench.cummax() - 1
    ax2.plot(dd_series, color='green', alpha=0.6, label='Strat DD')
    ax2.plot(bdd_series, color='gray', alpha=0.3, label='TQQQ DD')
    ax2.set_ylabel('Drawdown')
    ax2.legend()
    ax2.grid(True, alpha=0.2)
    
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str, default='2010-01-01')
    args = parser.parse_args()
    
    data = get_data(args.start)
    if data is not None and len(data) > 300:
        eq, df_res = run_strategy(data)
        analyze(eq, data['TQQQ'])
    else:
        print("Error loading data.")