#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_lookahead_audit.py

Heuristic audit for look-ahead bias in backtest code.

Usage:
    python detect_lookahead_audit.py qqq_deep_learning_and_baseline_experimental.py

What it flags:
1) Explicit negative shifts: shift(-k) or shift(-var)
2) Global distribution leakage: percentile/quantile over full sample, scaler.fit on full df
3) Same-bar trading risk: within baseline_backtest / deep_learning_backtest loops,
   any `.iloc[i]`-based signal/feature usage BEFORE equity is updated using day i returns.

This is not a formal proof, but a strong smoke test.
"""

import argparse, re, sys
from pathlib import Path
from typing import List, Tuple

NEG_SHIFT_RE = re.compile(r"shift\(\s*-\s*([0-9A-Za-z_]+)\s*\)")
PERCENTILE_RE = re.compile(r"(percentile|quantile)\s*\(")
SCALER_FIT_RE = re.compile(r"\.\s*fit\s*\(")

def load_lines(fp: Path) -> List[str]:
    return fp.read_text(encoding="utf-8", errors="ignore").splitlines()

def find_pattern(lines: List[str], regex: re.Pattern) -> List[Tuple[int,str]]:
    hits=[]
    for ln, text in enumerate(lines, start=1):
        if regex.search(text):
            hits.append((ln, text.rstrip()))
    return hits

def find_function_block(lines: List[str], name: str) -> Tuple[int,int]:
    """Return (start,end) line numbers (1-based, inclusive) of a def block."""
    start=None
    for i, t in enumerate(lines):
        if t.startswith(f"def {name}("):
            start=i
            break
    if start is None:
        return (-1,-1)
    end=len(lines)-1
    for j in range(start+1, len(lines)):
        if lines[j].startswith("def ") and not lines[j].startswith("def _"):
            end=j-1
            break
    return (start+1, end+1)

def same_bar_audit(lines: List[str], fn_name: str) -> List[str]:
    out=[]
    start,end = find_function_block(lines, fn_name)
    if start<0:
        return [f"[{fn_name}] function not found."]
    block = lines[start-1:end]

    loop_idx=None
    loop_indent=None
    for k, t in enumerate(block):
        m = re.match(r"(\s*)for\s+i\s+in\s+range\(", t)
        if m:
            loop_idx=k
            loop_indent=len(m.group(1))
            break
    if loop_idx is None:
        return [f"[{fn_name}] no `for i in range(...)` loop found."]

    eq_line_idx=None
    for k in range(loop_idx+1, len(block)):
        t = block[k]
        if len(t) - len(t.lstrip()) <= loop_indent:
            break
        if re.search(r"\bnew_eq\s*=\s*prev_eq\s*\*\s*\(", t) or "equity.append(" in t:
            eq_line_idx=k
            break
    if eq_line_idx is None:
        return [f"[{fn_name}] couldn't find equity update line inside loop."]

    risky=[]
    for k in range(loop_idx+1, eq_line_idx):
        t = block[k]
        if re.search(r"\.iloc\[\s*i\s*\]", t):
            risky.append((start + k, t.rstrip()))

    if not risky:
        out.append(f"[{fn_name}] OK: no `.iloc[i]` before equity update (same-bar bias unlikely).")
        return out

    out.append(f"[{fn_name}] POTENTIAL SAME-BAR LOOKAHEAD: `.iloc[i]` used before equity update:")
    for ln, txt in risky[:80]:
        out.append(f"  L{ln}: {txt}")
    if len(risky)>80:
        out.append(f"  ... {len(risky)-80} more lines omitted")
    out.append("  -> If these lines influence today's weights, shift them by 1 day.")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="python strategy/backtest file to audit")
    args = ap.parse_args()
    fp = Path(args.file)
    if not fp.exists():
        print(f"File not found: {fp}")
        sys.exit(1)

    lines = load_lines(fp)

    print("="*80)
    print("1) Explicit negative-shift usage")
    neg_hits = find_pattern(lines, NEG_SHIFT_RE)
    if neg_hits:
        for ln, txt in neg_hits:
            print(f"L{ln}: {txt}")
        print("-> If used ONLY for training labels, OK. If used in live decision, it's look-ahead.")
    else:
        print("No shift(-k) found.")

    print("\n"+"="*80)
    print("2) Global distribution leakage (percentile/quantile, scaler.fit)")
    pq_hits = find_pattern(lines, PERCENTILE_RE)
    if pq_hits:
        for ln, txt in pq_hits[:80]:
            print(f"L{ln}: {txt}")
        if len(pq_hits)>80:
            print(f"... {len(pq_hits)-80} more omitted")
        print("-> Verify these are computed on rolling/expanding TRAIN windows, not full history.")
    else:
        print("No percentile/quantile found.")

    fit_hits = find_pattern(lines, SCALER_FIT_RE)
    if fit_hits:
        for ln, txt in fit_hits[:80]:
            print(f"L{ln}: {txt}")
        print("-> Verify fit() is done on train-only data.")
    else:
        print("No .fit() found (or not matched).")

    print("\n"+"="*80)
    print("3) Same-bar trading risk inside backtests")
    for fn in ["baseline_backtest","deep_learning_backtest"]:
        res = same_bar_audit(lines, fn)
        print("\n".join(res))
        print("-"*80)

    print("\nDone. This is a heuristic smoke test; manual review is still required.")

if __name__=="__main__":
    main()
