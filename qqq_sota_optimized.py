#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qqq_ultima_strategy.py

Logic: "Hybrid Aggressive"
1. Base: Hold TQQQ in Bull Trends (Price > SMA200).
2. Alpha 1 (Sniper): BUY TQQQ when RSI < 30 (Oversold), even in Bear Trends.
3. Alpha 2 (Safety): SELL/CASH when VIX > 35 (Systemic Panic) UNLESS RSI is Oversold.
4. Execution: Strictly Next-Day Open (No Look-ahead).

Goal: Beat TQQQ Buy & Hold by cutting the deepest drawdowns while catching V-bottoms.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
import argparse
import warnings

warnings.filterwarnings("ignore")

# ------------------ Configuration ------------------
RISK_FREE_RATE = 0.04
SLIPPAGE_BPS = 10 # 10 basis points slippage per trade

def get_data(start):
    print(f"Downloading data from {start}...")
    # Tickers: TQQQ for trading, QQQ/VIX for signals
    tickers = ['QQQ', 'TQQQ', '^VIX']
    df = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    
    # Handle MultiIndex columns
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
        'TQQQ_OPEN': open_['TQQQ'],
        'VIX': close['^VIX']
    })
    
    # Clean VIX
    data['VIX'] = data['VIX'].ffill().fillna(20.0)
    return data.dropna()

def compute_rsi(series, window=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / (loss + 1e-8)
    return 100 - (100 / (1 + rs))

def run_backtest(df):
    # --- Indicators ---
    # 1. Trend
    df['ma_200'] = df['QQQ'].rolling(200).mean()
    
    # 2. Mean Reversion
    df['rsi'] = compute_rsi(df['QQQ'], window=10) # Faster RSI
    
    # 3. Volatility
    df['vix'] = df['VIX']

    # --- Signal Logic (Vectorized for Speed) ---
    # Signal 1: Bull Trend (Price > MA200)
    cond_trend = df['QQQ'] > df['ma_200']
    
    # Signal 2: Extreme Panic (VIX > 35) -> Cash
    cond_panic = df['vix'] > 35.0
    
    # Signal 3: Oversold Bounce (RSI < 30) -> Buy Signal (Overrides Panic)
    cond_oversold = df['rsi'] < 30.0
    
    # --- Strategy Rules ---
    # Default: Cash (0.0)
    df['target_alloc'] = 0.0
    
    # Rule A: In Uptrend, Hold TQQQ unless Panic
    df.loc[cond_trend & (~cond_panic), 'target_alloc'] = 1.0
    
    # Rule B: Even in Panic/Downtrend, if Oversold, BUY THE DIP (Sniper)
    # This catches the V-bottoms that MA200 misses
    df.loc[cond_oversold, 'target_alloc'] = 1.0
    
    # Rule C: Hard Exit - If Panic AND Not Oversold, ensure Cash
    df.loc[cond_panic & (~cond_oversold), 'target_alloc'] = 0.0

    # --- Strict Execution: Signal(t) -> Trade(t+1) ---
    # Shift allocation by 1 to trade at Next Open
    df['final_alloc'] = df['target_alloc'].shift(1).fillna(0.0)
    
    # Calculate Returns using Next Day Open-to-Open
    # Return for holding from T+1 Open to T+2 Open
    tqqq_open = df['TQQQ_OPEN']
    
    # Pct Change of Open Prices shifted back to align with 'final_alloc' decision time
    # Trade Ret at T is (Open[T+1] - Open[T]) / Open[T]
    # We are holding based on alloc[T], which generates return Open[T] -> Open[T+1] ?
    # No. Alloc[T] (calculated at T close) trades at Open[T] is impossible.
    # Alloc[T] (calculated at T close) trades at Open[T+1].
    
    # Let's use a simpler explicit loop or aligned shift to be 100% sure.
    # We hold from Open(i) to Open(i+1) based on Signal(i-1).
    
    # Open-to-Open Return of TQQQ
    df['tqqq_open_ret'] = tqqq_open.pct_change() 
    
    # Strategy Return at Day i = Alloc(i-1) * Open_Ret(i)
    # Alloc(i-1) was determined at Close(i-2) and executed at Open(i-1).
    # Wait, let's stick to standard:
    # Signal at Close(t). Enter Open(t+1). Exit Open(t+2).
    # Return is (Open(t+2) - Open(t+1))/Open(t+1).
    
    next_open_ret = tqqq_open.pct_change().shift(-1) # Return from T to T+1 (using Open prices? No pct_change is (T - T-1)/T-1)
    
    # Correct Open-to-Open Logic:
    # We buy at Open(t+1). We sell at Open(t+2).
    # The return is (Open[t+2] - Open[t+1]) / Open[t+1]
    
    # Let's iterate to be safe and avoid vectorization bugs with "Open" times
    equity = [1.0]
    allocs = df['final_alloc'].values # This is Alloc for the "Day", decided yesterday
    opens = df['TQQQ_OPEN'].values
    dates = df.index
    
    for i in range(1, len(df)):
        # We are at Open(i). 
        # We hold the position determined by Signal(i-1), which is in `final_alloc`[i]
        
        if i == len(df) - 1: break
        
        current_pos = allocs[i]
        
        # Return from Open(i) to Open(i+1)
        r = (opens[i+1] - opens[i]) / opens[i]
        
        # Cost (Slippage)
        cost = 0.0
        if i > 1:
            turnover = abs(allocs[i] - allocs[i-1])
            cost = turnover * (SLIPPAGE_BPS / 10000.0)
            
        strat_r = current_pos * r + (1 - current_pos) * (RISK_FREE_RATE/252)
        equity.append(equity[-1] * (1 + strat_r - cost))
        
    return pd.Series(equity, index=df.index[1:len(equity)+1]), df

def show_stats(eq, bench):
    # Bench align
    b = bench.reindex(eq.index)
    b = b / b.iloc[0]
    
    # Stats
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1])**(1/years) - 1
    b_cagr = (b.iloc[-1])**(1/years) - 1
    
    vol = eq.pct_change().std() * np.sqrt(252)
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    
    b_dd = b / b.cummax() - 1
    b_mdd = b_dd.min()
    
    sharpe = (cagr - RISK_FREE_RATE) / vol
    
    print("\n" + "="*50)
    print(f"ULTIMA TQQQ STRATEGY (No Look-Ahead)")
    print("="*50)
    print(f"{'Metric':<15} | {'Strategy':<12} | {'TQQQ B&H':<12}")
    print("-" * 50)
    print(f"{'CAGR':<15} | {cagr:8.2%}     | {b_cagr:8.2%}")
    print(f"{'Max Drawdown':<15} | {mdd:8.2%}     | {b_mdd:8.2%}")
    print(f"{'Sharpe Ratio':<15} | {sharpe:8.2f}     | {'N/A':<8}")
    print("-" * 50)
    print(f"Final Equity    | {eq.iloc[-1]:8.2f}x    | {b.iloc[-1]:8.2f}x")
    print("="*50)
    
    return b

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str, default='2015-01-01')
    args = parser.parse_args()
    
    data = get_data(args.start)
    if len(data) > 200:
        eq, df_res = run_backtest(data)
        bench = show_stats(eq, data['TQQQ'])
        
        # Plot
        plt.figure(figsize=(12, 6))
        plt.plot(eq, label='Ultima Strategy', linewidth=1.5)
        plt.plot(bench, label='TQQQ Buy & Hold', alpha=0.5, linestyle='--')
        plt.yscale('log')
        plt.title('Ultima Strategy vs TQQQ (Strict Next-Open Execution)')
        plt.legend()
        plt.grid(True, which='both', alpha=0.2)
        plt.show()
    else:
        print("Not enough data.")