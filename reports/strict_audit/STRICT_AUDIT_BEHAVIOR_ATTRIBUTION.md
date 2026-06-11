# Strict Audit Behavior Attribution

## Scope

This attribution uses only retained local artifacts in `reports/strict_audit/`:

- `strict_audit_summary.csv`
- `baseline_tqqq_only_capped3x_strict_walk_forward.csv`
- `dl_tqqq_only_capped3x_strict_walk_forward.csv`
- `tqqq_only_capped3x_strict_walk_forward.csv`

No strategy logic was changed, no new parameter search was run, no broker/live workflow was touched, and no secret files were read.

Important limitation: the retained artifacts are slice-level summaries. Daily equity, daily weights, notes, and trades CSVs were not retained. Therefore top-day capture, monthly contribution attribution, note classification, and trade timing analysis cannot be computed from the current local outputs without rerunning or generating additional daily artifacts. Those fields are included in `behavior_attribution_summary.csv` as unavailable rather than inferred.

## Executive Answer

No, this is not a TQQQ killer. The strict audit shows a defensive strategy that reduced drawdown in every retained slice, but it did not consistently beat TQQQ CAGR. The core reason is structural underexposure during strong TQQQ bull markets.

There is evidence that the strategy can be treated as a TQQQ risk-management candidate: every slice had lower MaxDD than TQQQ, QQQ5 was disabled, and effective leverage stayed below 3x. But the evidence is not enough to claim durable alpha versus buy-and-hold TQQQ.

## Exposure Attribution

| slice | mode | market_regime | avg_effective_leverage | avg_exposure_gap_vs_tqqq_3x | cagr_gap | maxdd_improvement | calmar_gap | sharpe_gap | behavior_read | primary_drag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2019_2021 | baseline | bull / recovery | 1.131 | 1.869 | -71.45% | 47.75% | 0.065 | 0.105 | drawdown protection but bull-market undercapture | underexposure in bull markets |
| 2022_2023 | baseline | bear / recovery | 1.043 | 1.957 | 28.99% | 47.87% | 0.477 | 0.265 | defensive win versus TQQQ | none at slice level |
| 2024_latest | baseline | bull / recovery | 1.151 | 1.849 | -38.18% | 35.93% | -0.157 | -0.192 | drawdown protection but bull-market undercapture | underexposure in bull markets |
| 2019_2021 | dl | bull / recovery | 0.651 | 2.349 | -85.80% | 47.52% | -0.592 | -0.276 | drawdown protection but bull-market undercapture | underexposure in bull markets |
| 2022_2023 | dl | bear / recovery | 0.687 | 2.313 | 9.72% | 40.02% | -0.032 | -0.720 | absolute return win in bear slice but weak risk-adjusted profile | DL overlay or defensive state drag |
| 2024_latest | dl | bull / recovery | 1.007 | 1.993 | -39.93% | 28.79% | -0.414 | -0.289 | drawdown protection but bull-market undercapture | underexposure in bull markets |

The dominant exposure fact is simple: TQQQ buy-and-hold is a 3x path. The strategy's average effective leverage was far below 3x in all slices. In baseline bull/recovery slices, the average exposure gap versus 3x was 1.86x. In the 2022-2023 bear/recovery slice, the baseline average exposure gap was 1.96x.

This exposure profile explains both sides of the result:

- It protected capital when TQQQ suffered large drawdowns.
- It lagged badly when TQQQ compounded through strong bull markets.

Unavailable from retained artifacts:

- P25/P50/P75/P95 effective leverage.
- Time spent in effective leverage buckets `<=0.5x`, `0.5-1.0x`, `1.0-2.0x`, `2.0-3.0x`.
- Daily previous-day exposure on top 10 up/down TQQQ days.

## Up/Down Capture

Daily up/down capture cannot be computed from the retained artifacts because daily strategy returns and daily TQQQ returns were not retained in `reports/strict_audit/`.

Slice-level proxy:

- Bull slices: strategy CAGR materially lagged TQQQ while drawdown improved. That is consistent with low up-capture caused by lower exposure.
- Bear slice: strategy CAGR beat TQQQ and MaxDD improved. That is consistent with materially lower down-capture.

Unavailable from retained artifacts:

- Up-day capture ratio.
- Down-day capture ratio.
- Strong up-day capture when TQQQ daily return is above +5%.
- Severe down-day capture when TQQQ daily return is below -5%.
- Top 10 TQQQ up/down day attribution.

## Bull Market Underperformance

The bull/recovery slices explain why the strategy cannot claim stable TQQQ outperformance:

- Baseline 2019-2021: strategy CAGR 35.28% versus TQQQ CAGR 106.73%.
- Baseline 2024-latest: strategy CAGR 17.89% versus TQQQ CAGR 56.07%.
- Baseline average effective leverage was only 1.131x and 1.151x in those slices.

This is strong slice-level evidence of underexposure in bull markets. It is not possible from retained artifacts to identify exact months or days of contribution drag, or to prove whether risk gate cooldown or delayed re-risking specifically caused the lag. The current artifact set only supports the broader conclusion: the strategy was too defensively exposed to keep up with TQQQ's 3x compounding in strong markets.

## Bear Market Protection

The 2022-2023 slice is where the strategy worked best as a defensive strategy:

- Baseline strategy CAGR 6.70% versus TQQQ CAGR -22.29%.
- Baseline MaxDD -33.14% versus TQQQ MaxDD -81.02%.
- Baseline Calmar 0.202 versus TQQQ Calmar -0.275.

The most defensible interpretation is that the strategy mainly protected capital by running lower effective leverage, not by proving high-frequency timing alpha. The average effective leverage in the baseline 2022-2023 slice was 1.043x.

## DL vs Baseline

DL did not add stable value in the retained strict audit:

- DL CAGR was below baseline CAGR in 3 of 3 matching slices.
- DL did not beat baseline Calmar in any matching slice in the retained summary.
- The first DL strict slice reported insufficient initial leak-safe samples during the strict audit run and used rule logic until retrain; it cannot be cited as clean DL alpha evidence.

Conclusion: DL is currently a drag or at best unproven. It should be treated as an overlay requiring ablation evidence, not as a proven source of edge.

## Risk Gate / Notes Attribution

Notes CSVs were not retained, so the following cannot be computed from current local artifacts:

- risk_flag days
- cooldown days
- crash cap hit days
- effective leverage cap hit days
- QQQ5 disabled or skipped trade days
- average next-day returns by note category

Based on slice-level data, the risk system appears effective at reducing drawdown but too defensive for bull-market CAGR capture. That is an attribution hypothesis, not a daily-note proof.

## Trade Behavior

Trades CSVs were not retained, so the following cannot be computed from current local artifacts:

- trade count by slice
- turnover proxy
- buy/sell timing after drawdown
- overtrading diagnostics
- realized trade cost drag

The current strict report only supports a high-level conclusion: the strategy's defensive exposure profile improved drawdown but reduced bull-market compounding.

## Final Interpretation

Is it a TQQQ killer? No.

Is it a TQQQ risk-management candidate? Yes, with important caveats. The strongest evidence is that MaxDD improved in every retained slice while QQQ5 was disabled and leverage was capped below 3x. The caveat is that this came with large CAGR underperformance in bull markets.

Top 3 causes of failure to consistently beat TQQQ:

1. Underexposure in bull markets. Average effective leverage was far below TQQQ's 3x in all slices, especially in 2019-2021 and 2024-latest.
2. Defensive state persistence / delayed re-risking, likely but not proven from retained artifacts. The slice-level exposure gap and bull CAGR lag are consistent with this, but daily notes are needed for proof.
3. DL not adding stable alpha. DL lagged baseline in all matching CAGR slices and had fallback limitations in the first DL slice.

## Next Research Questions

These are verification tasks, not parameter-tuning instructions:

1. Add a volatility-matched benchmark and test whether the strategy beats a simple equal-vol TQQQ or QQQ/TQQQ blend.
2. Compare against simple QQQ/TQQQ blends with fixed exposure to separate risk reduction from timing alpha.
3. Study re-risk speed after drawdowns using daily weights and subsequent returns.
4. Test risk gate false positives by labeling risk-off days followed by strong TQQQ rebounds.
5. Run DL overlay ablation: baseline only, DL signals only, DL crash cap only, DL delever only.
6. Add tax, slippage, and execution stress using the same strict OOS slices.
7. Add underwater time, recovery time, and pain ratio so risk-management value is not reduced to CAGR and MaxDD only.
