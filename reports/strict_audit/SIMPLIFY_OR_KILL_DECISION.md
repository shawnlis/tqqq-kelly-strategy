# Simplify-or-Kill Decision

## Executive Decision

NO-GO for paper trading the current complex strategy as a deployable strategy.

The project should not continue to define its objective as "beat long-term TQQQ." The strict audit result was `PARTIAL PASS`, the behavior attribution found structural bull-market underexposure, and the benchmark comparison concluded: current complex strategy is not yet justified.

New recommended objective:

Build and validate a TQQQ risk-management strategy that reduces drawdown and improves risk-adjusted returns versus simple TQQQ/QQQ/cash and volatility-targeted benchmarks under strict OOS rules. CAGR outperformance versus raw TQQQ may be reported only as a secondary result and only when it survives strict capped-leverage OOS testing.

## Evidence Base

Inputs reviewed:

- `STRICT_AUDIT_RESULTS.md`
- `STRICT_AUDIT_BEHAVIOR_ATTRIBUTION.md`
- `BENCHMARK_COMPARISON.md`
- `strict_audit_summary.csv`
- `benchmark_comparison_summary.csv`

Key facts:

- Strict audit verdict: `PARTIAL PASS`.
- Baseline strict strategy did not consistently beat TQQQ CAGR.
- Baseline CAGR lagged TQQQ in 2019-2021 and 2024-latest.
- MaxDD improved versus TQQQ in every retained strict slice.
- Average effective leverage was far below 3x in every slice.
- Benchmark comparison states: `Current strategy complexity is not yet justified.`
- DL strict strategy did not beat baseline in any retained CAGR slice.
- QQQ5 was disabled for headline strict audit; synthetic/hybrid QQQ5 remains excluded from main conclusions.

## Module Decisions

| Module | Decision | Evidence | Required Treatment |
| --- | --- | --- | --- |
| Strict OOS / walk-forward infrastructure | KEEP | Prevents full-future-path slicing and is required for any credible result. | Keep as mandatory audit harness. |
| Raw TQQQ buy-and-hold benchmark | KEEP | Needed for headline comparison and confirmed no extra expense double count. | Keep as primary benchmark. |
| Simple benchmark suite | KEEP | Vol-targeted TQQQ and fixed blends are strong hurdles; current strategy does not clearly beat them. | Make this a mandatory gate before claiming complexity value. |
| Effective leverage cap | KEEP | Strict audit confirmed max effective leverage below 3x. | Required for any TQQQ comparison. |
| QQQ5 source isolation / QQQ5 disable gate | KEEP | Prevents synthetic/hybrid QQQ5 from contaminating headline claims. | Keep QQQ5 disabled for headline results. |
| Baseline defensive strategy | KEEP AS RESEARCH CANDIDATE | It improved MaxDD in every retained slice and had the best baseline result in 2022-2023. | Reframe as risk-management candidate, not TQQQ killer. |
| Risk gate / drawdown defense | KEEP BUT REQUIRE ABLATION | Slice-level evidence suggests drawdown value, but daily notes were not retained to prove which risk controls helped. | Keep only inside audited baseline until component ablation proves value. |
| Kelly sizing | DOWNGRADE TO RESEARCH-ONLY | Current evidence does not isolate Kelly alpha versus simple lower exposure or vol targeting. | Do not treat as proven sizing edge. |
| Bandit / adaptive policy | DOWNGRADE TO RESEARCH-ONLY | No retained evidence shows Bandit added value over simple benchmarks or baseline without Bandit. | Require ablation before use in any headline strategy. |
| DL overlay | DOWNGRADE TO RESEARCH-ONLY | DL lagged baseline CAGR in all retained slices and had fallback limitations in the first slice. | Do not cite as effective; require clean ablation and forward evidence. |
| DL crash probability / delever / soft-scale controls | DOWNGRADE TO RESEARCH-ONLY | No daily notes or action attribution proves incremental value; DL path was weaker than baseline. | Keep disabled in any minimal candidate. |
| Synthetic / hybrid QQQ5 sleeve | KILL FOR HEADLINE, RESEARCH-ONLY ONLY | QQQ5 source was hybrid and disabled; synthetic/hybrid QQQ5 cannot support live-tradable proof. | Exclude from main strategy and paper-trading candidate. |
| Complex multi-sleeve allocation | DOWNGRADE TO RESEARCH-ONLY | Current returns look explainable by lower exposure and defensive state, not proven alpha. | Simplify before further deployment work. |

## Minimum Viable Strategy

The minimum viable strategy should not be the current full stack.

Recommended minimum viable research candidate:

1. TQQQ-only or QQQ/TQQQ/cash only.
2. QQQ5 disabled.
3. No DL overlay.
4. No synthetic or hybrid instruments.
5. Effective leverage capped at 3x.
6. Raw TQQQ benchmark, no extra expense deduction.
7. Strict OOS / walk-forward only.
8. Daily artifacts retained: equity, weights, notes, trades, effective leverage, risk state.
9. Must beat or clearly differentiate from:
   - TQQQ buy-and-hold;
   - 80% TQQQ + 20% cash;
   - 70% TQQQ + 30% QQQ;
   - 50% TQQQ + 50% QQQ;
   - vol-targeted TQQQ;
   - shifted MA crossover TQQQ.

If the simplified baseline cannot beat or clearly improve on those simple benchmarks, the project should stop building complex overlays and remain a research archive.

## Paper-Trading Decision

NO-GO for paper trading the current complex strategy.

Reason:

- The strategy is not proven to beat TQQQ.
- The strategy is not proven to beat simple benchmark alternatives.
- DL is unproven and likely a drag in the retained strict results.
- Kelly / Bandit / complex allocation have not been isolated from simple lower-exposure effects.
- Daily strict OOS notes/trades/weights were not retained, so module-level attribution is incomplete.

Permitted next step:

Offline shadow simulation only, using strict daily artifact capture and no capital deployment claims. A future paper-trading decision should require a simplified candidate that passes the simple benchmark gate and has daily evidence explaining why it works.

## Go / No-Go

Decision: NO-GO for current complex strategy.

Go only for a simplified research track:

- Keep strict backtest infrastructure.
- Keep simple benchmark comparison as the hurdle.
- Keep baseline defensive idea as a candidate.
- Freeze DL, Bandit, synthetic QQQ5, and complex multi-sleeve allocation as research-only until ablation proves incremental value.

## Three Research Tasks Only

1. Component ablation with daily strict artifacts.
   Run baseline variants that isolate risk gate, Kelly sizing, Bandit policy, and DL overlay. Retain daily equity, weights, notes, trades, and effective leverage so module attribution is evidence-based rather than inferred.

2. Minimal strategy hurdle test.
   Define one simplified TQQQ-only or QQQ/TQQQ/cash candidate with QQQ5 disabled and no DL. Compare it against the simple benchmark suite on the same strict OOS slices. The candidate must show value versus vol-targeted TQQQ and fixed blends before any paper-trading discussion.

3. Forward shadow protocol design.
   Before paper trading, specify a no-tuning forward simulation protocol with frozen code, frozen parameters, daily logs, trade-cost/slippage stress, tax-aware reporting, and predeclared go/no-go metrics. This is not parameter optimization.

## Final Recommendation

Stop presenting the current system as a TQQQ outperformance strategy.

Keep the project, but narrow it to a risk-management research program. The current complex strategy is not yet justified, and its unsupported modules should be downgraded until they beat the simple benchmark gate under strict OOS evidence.
