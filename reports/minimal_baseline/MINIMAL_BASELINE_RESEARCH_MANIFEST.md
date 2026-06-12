# Minimal TQQQ Baseline Research Manifest

## Repository State

- Branch: `minimal-risk-baseline-v01`
- Latest completed research commit before this manifest: `f54a35b`
- Current best candidate: `MA150 risk-off QQQ`
- Current verdict: `HOLD`
- Paper-trading status: `NOT READY FOR PAPER TRADING`

## Data Range And Slices

- Data source: QQQ and TQQQ adjusted market prices loaded by the local minimal baseline runner.
- Research start: `2015-01-01`
- Strict OOS slices:
  - Train `2015-01-01` to `2018-12-31`; test `2019-01-01` to `2021-12-31`
  - Train `2015-01-01` to `2021-12-31`; test `2022-01-01` to `2023-12-31`
  - Train `2015-01-01` to `2023-12-31`; test `2024-01-01` to latest available audit date

## Allowed Modules

- Static TQQQ/QQQ/cash blends.
- MA regime exposure control using QQQ close and a one-day signal lag.
- Volatility targeting and drawdown-control baselines only as benchmark comparisons.
- Daily artifact capture for attribution.

## Disabled Modules

- DL.
- Bandit/adaptive policy.
- Kelly sizing.
- QQQ5.
- Synthetic or hybrid leveraged products.
- Options, futures, or broker execution.

## Headline Restrictions

- Headline claims must use raw TQQQ benchmark returns.
- No synthetic/hybrid QQQ5 may be included.
- No DL/Bandit/Kelly may be included.
- Effective leverage must stay at or below 3x.
- Strict OOS and no-lookahead timing must remain intact.
- CAGR alone is not sufficient; Calmar, MaxDD, UlcerIndex, PainIndex, and underwater behavior must be evaluated.

## Next Research Questions

1. Does `MA150 risk-off QQQ` still beat simple blends after daily artifact attribution?
2. Does it survive realistic slippage, cost, tax, and turnover stress?
3. Does the holding-pain improvement persist outside the existing strict slices without changing parameters?

## Do-Not-Do List

- do not tune MA windows by grid search.
- Do not reintroduce DL, Bandit, Kelly, QQQ5, synthetic products, or hybrid products into headline results.
- Do not paper trade before hurdles pass.
- Do not optimize for CAGR alone.
- Do not change strategy rules after seeing unfavorable OOS results.
- Do not commit raw daily artifact CSVs.
