#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_ma_executor.py

Lightweight live runner for the MA strategy used in ma_enhanced_tqqq.py.
Supports IBKR (via ib_insync) or moomoo-open if available. Default is dry-run
so you can see orders before actually sending them.

Defaults (TQQQ preset):
  fast=5, slow=80, band=1.8%, pos_mid=0.5
  vol_target / lev_max are ignored here; we just size by target weight.

Usage examples (dry run):
  python live_ma_executor.py --broker ibkr --mode tqqq --alloc-tqqq 1.0 --dry-run
  python live_ma_executor.py --broker moomoo --mode both --alloc-tqqq 0.6 --alloc-soxl 0.4 --dry-run

Set --send-orders to actually place trades. Be sure you are connected/logged in
to the broker API and have the correct account selected.
"""
import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None


# ---------- Strategy helpers ----------
@dataclass
class MAParams:
    fast: int = 5
    slow: int = 80
    band: float = 0.018
    pos_mid: float = 0.5


def fetch_history(ticker: str, start: str = "2010-01-01", end: Optional[str] = None) -> pd.Series:
    if yf is None:
        raise RuntimeError("yfinance not installed; install via pip install yfinance")
    df = yf.download(ticker, start=start, end=end, progress=False)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        if ("Adj Close", ticker) in df.columns:
            px = df[("Adj Close", ticker)]
        elif ("Close", ticker) in df.columns:
            px = df[("Close", ticker)]
        else:
            px = df.xs("Close", level=0, axis=1).iloc[:, 0]
    else:
        px = df["Adj Close"] if "Adj Close" in df.columns else df["Close"]
    return pd.to_numeric(px, errors="coerce").dropna()


def latest_signal(px: pd.Series, params: MAParams) -> float:
    ma_fast = px.rolling(params.fast).mean()
    ma_slow = px.rolling(params.slow).mean()
    spread = (ma_fast - ma_slow) / ma_slow
    if len(spread.dropna()) == 0:
        return 0.0
    s = spread.iloc[-1]
    if s > params.band:
        return 1.0
    if s < -params.band:
        return 0.0
    return params.pos_mid


# ---------- Broker wrappers ----------
class IBKRBroker:
    def __init__(self, account: Optional[str], dry_run: bool):
        try:
            from ib_insync import IB, Stock, util  # type: ignore
        except Exception as exc:
            raise RuntimeError("ib_insync not installed; pip install ib_insync") from exc
        self.IB = IB
        self.Stock = Stock
        self.util = util
        self.ib = IB()
        self.account = account
        self.dry_run = dry_run

    def connect(self, host="127.0.0.1", port=7497, client_id=9):
        self.ib.connect(host, port, clientId=client_id, readonly=self.dry_run)

    def get_positions(self) -> Dict[str, float]:
        positions = {}
        for p in self.ib.positions():
            if self.account and p.account != self.account:
                continue
            positions[p.contract.symbol] = float(p.position)
        return positions

    def place_target(self, symbol: str, target_qty: float):
        if self.dry_run:
            print(f"[DRY-RUN][IBKR] {symbol}: target {target_qty:.4f} shares")
            return
        contract = self.Stock(symbol, "SMART", "USD")
        mkt_price = float(self.ib.reqMktData(contract, "", False, False).last)
        if not np.isfinite(mkt_price) or mkt_price <= 0:
            raise RuntimeError(f"Cannot get market price for {symbol}")
        current = self.get_positions().get(symbol, 0.0)
        delta = target_qty - current
        if abs(delta) < 1e-6:
            return
        side = "BUY" if delta > 0 else "SELL"
        order_qty = abs(delta)
        order = self.ib.marketOrder(side, order_qty)
        trade = self.ib.placeOrder(contract, order)
        print(f"[IBKR] Sent {side} {order_qty:.4f} {symbol}, status={trade.orderStatus.status}")


class MooMooBroker:
    def __init__(self, account: Optional[str], dry_run: bool):
        try:
            from moomoo import OpenQuoteContext, OpenTradeContext  # type: ignore
        except Exception as exc:
            raise RuntimeError("moomoo-open SDK not installed; pip install moomoo") from exc
        self.OpenQuoteContext = OpenQuoteContext
        self.OpenTradeContext = OpenTradeContext
        self.account = account
        self.dry_run = dry_run
        self.quote_ctx = None
        self.trade_ctx = None

    def connect(self, host="127.0.0.1", port=11111):
        self.quote_ctx = self.OpenQuoteContext(host, port)
        self.trade_ctx = self.OpenTradeContext(host, port)

    def get_positions(self) -> Dict[str, float]:
        if self.trade_ctx is None:
            return {}
        ret, data = self.trade_ctx.position_list_query()
        positions = {}
        if ret == 0 and data is not None:
            for _, row in data.iterrows():
                symbol = row.get("code") or row.get("stock_code")
                qty = row.get("qty") or row.get("quantity")
                if symbol:
                    positions[symbol.split(".")[0]] = float(qty)
        return positions

    def place_target(self, symbol: str, target_qty: float):
        if self.dry_run:
            print(f"[DRY-RUN][Moomoo] {symbol}: target {target_qty:.4f} shares")
            return
        if self.trade_ctx is None:
            raise RuntimeError("Trade context not connected")
        current = self.get_positions().get(symbol, 0.0)
        delta = target_qty - current
        if abs(delta) < 1e-6:
            return
        side = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        # This uses a market order; adjust as needed.
        ret, data = self.trade_ctx.place_order(price=0, qty=qty, code=symbol, trd_side=side, order_type="MO")
        if ret == 0:
            print(f"[Moomoo] Sent {side} {qty:.4f} {symbol}, order_id={data.get('order_id')}")
        else:
            print(f"[Moomoo][ERR] {data}")


# ---------- Execution ----------
def compute_target_qty(signal: float, alloc_cash: float, last_price: float) -> float:
    if last_price <= 0 or signal <= 0:
        return 0.0
    return alloc_cash * signal / last_price


def main():
    ap = argparse.ArgumentParser(description="Live executor for MA strategy (TQQQ/SOXL).")
    ap.add_argument("--broker", choices=["ibkr", "moomoo"], required=True)
    ap.add_argument("--mode", choices=["tqqq", "soxl", "both"], default="both")
    ap.add_argument("--alloc-tqqq", type=float, default=0.6, help="Fraction of cash to TQQQ when mode=both.")
    ap.add_argument("--alloc-soxl", type=float, default=0.4, help="Fraction of cash to SOXL when mode=both.")
    ap.add_argument("--band", type=float, default=0.018)
    ap.add_argument("--pos-mid", type=float, default=0.5)
    ap.add_argument("--fast", type=int, default=5)
    ap.add_argument("--slow", type=int, default=80)
    ap.add_argument("--cash", type=float, default=None, help="Total capital to allocate. If None, assume account cash.")
    ap.add_argument("--account", type=str, default=None, help="Account id (optional).")
    ap.add_argument("--send-orders", action="store_true", help="Actually send orders (default: dry-run).")
    ap.add_argument("--start", type=str, default="2010-02-11", help="History start for signals.")
    args = ap.parse_args()

    params = MAParams(fast=args.fast, slow=args.slow, band=args.band, pos_mid=args.pos_mid)
    today = dt.date.today().isoformat()
    tickers = []
    if args.mode in ("tqqq", "both"):
        tickers.append("TQQQ")
    if args.mode in ("soxl", "both"):
        tickers.append("SOXL")

    history = {}
    signals = {}
    prices = {}
    for tk in tickers:
        px = fetch_history(tk, start=args.start, end=today)
        history[tk] = px
        signals[tk] = latest_signal(px, params)
        prices[tk] = float(px.iloc[-1])

    dry_run = not args.send_orders
    if args.broker == "ibkr":
        broker = IBKRBroker(account=args.account, dry_run=dry_run)
        broker.connect()
    else:
        broker = MooMooBroker(account=args.account, dry_run=dry_run)
        broker.connect()

    positions = broker.get_positions()
    cash = args.cash
    if cash is None:
        # Simplified: infer cash as 0 and rely on target qty vs current position; adjust to your API if needed.
        cash = 0.0
        print("[warn] cash not provided; using 0.0 (target qty will be relative to cash). Provide --cash for sizing.")

    alloc_t = cash * (args.alloc_tqqq if args.mode == "both" else 1.0 if "TQQQ" in tickers else 0.0)
    alloc_s = cash * (args.alloc_soxl if args.mode == "both" else 1.0 if "SOXL" in tickers else 0.0)

    for tk in tickers:
        sig = signals[tk]
        px = prices[tk]
        if tk == "TQQQ":
            alloc = alloc_t
        else:
            alloc = alloc_s
        target_qty = compute_target_qty(sig, alloc, px)
        print(f"[Signal] {tk}: signal={sig:.2f}, last_price={px:.2f}, target_qty={target_qty:.4f}, current={positions.get(tk,0.0):.4f}")
        broker.place_target(tk, target_qty)

    if dry_run:
        print("Dry-run complete. Use --send-orders to actually place orders.")


if __name__ == "__main__":
    main()
