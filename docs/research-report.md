# Market information diffusion: completed FX event study

**Updated 23 September 2026 · HistData study v2 · 35 US CPI dates, 2022–2025**

The project measures how EUR/USD and GBP/USD prices adjust around US inflation announcements. The complete research pipeline ran on real public data. Its principal finding is a limitation: **only 5 of 70 event/currency responses pass the recorded primary screens**. This sample does not establish a reliable currency speed ranking, causal information transmission or a profitable trading signal.

“Diffusion” here describes price adjustment to news; this is not a generative AI diffusion model. The implemented curve estimates normalized price adjustment, not a directly observed fraction of information absorbed.

## 1. Research scope and data acquisition

The initial ES/ZN futures proposal was replaced with a publicly accessible FX study. No account or API key was required for the completed HistData acquisition. The two quote providers were studied separately; the final expanded results use HistData throughout.

| Input | Source and execution | Completed scope |
|---|---|---|
| CPI actuals and release dates | Preserved BLS release extracts and audited parsing | 46 parseable monthly actuals across release years 2022–2025 |
| Pre-release consensus | Preserved Scotiabank morning-report PDFs, with publication-time checks | 35 usable dates; headline and core values |
| Bid/ask quotes | HistData public monthly ASCII tick ZIP downloads | 70 original archives; 994,263,551 bytes |
| Imported event windows | Full archive scans; validated conversion and timestamp filtering | 159,440,567 source rows scanned; 478,393 unique quotes retained |
| Scheduled-news audit | Calendar records plus documented correction from a DOL release | Two restricted news cohorts evaluated |

The 35 dates comprise 9 in 2022, 10 in 2023, 8 in 2024 and 8 in 2025. Six missing consensus PDFs and five ambiguous publication timestamps remove 11 of the 46 available actuals. This is an availability-selected sample, not every CPI announcement. Missing monthly values and unpublished releases are not invented. Headline and core observations describe the same 35 joint releases, not 70 independent announcements.

Sources: [HistData download service](https://www.histdata.com/download-free-forex-historical-data/), [HistData format specification](https://www.histdata.com/f-a-q/data-files-detailed-specification/). Exact source evidence and hashes remain in `data/raw/bls/cpi-2022-2025-v1`, `data/raw/scotiabank/cpi-2022-2025-v1` and `data/raw/histdata/cpi-2022-2025-v1`. The completed [acquisition/import receipt](reproduction.md) records the run.

## 2. Event definition and surprise

For release e, let T_e be its UTC timestamp, A_e its actual value, and C_e its pre-release consensus. The raw surprise is U_e = A_e − C_e, in the source's percentage-point units. Event time is τ = t − T_e, measured in seconds.

The standardized surprise is S_e = U_e / s_e, where s_e is the sample standard deviation of strictly earlier covered surprises from the same series. At least six prior observations are required; 29 of the 35 dates have enough history. This scaling does not subtract the historical mean. The model stores surprises as event descriptors; it does **not** estimate a regression of response speed on surprise or separate headline from core causal effects.

## 3. Timestamp conversion and quote alignment

HistData timestamps use fixed EST (UTC−05) throughout the year, without daylight-saving adjustment. Announcement timestamps follow their separately verified release timezone before conversion to UTC. Confusing these conventions would shift summer event windows by an hour.

The importer retains quotes within [−65,+1800] seconds for each release. The primary analysis uses a one-second grid from −60 to +300 seconds. At each grid time it selects the latest quote timestamped at or before that time; a quote older than two seconds becomes unavailable. No future quote is borrowed to fill a gap.

All archives were scanned. There were zero duplicate-timestamp updates. Two backward clock transitions outside all study windows were documented; a reversal touching an event window fails validation. Five of the 70 event/currency windows contain no quotes, and additional windows are sparse. Download completion therefore does not mean usable quote coverage.

## 4. Baseline and endpoint

Let m(τ) = (bid(τ)+ask(τ))/2 and p(τ) = log m(τ). Define:

- p⁻: median log midpoint over −60 ≤ τ < 0.
- p*: median log midpoint over 240 < τ ≤ 300.
- Δ = p* − p⁻: estimated endpoint move.
- σ_pre: standard deviation of pre-release log midpoints.

Pre-event, post-event and endpoint windows each require at least 95% fresh quote coverage. The move must satisfy |10,000Δ| ≥ max(1, 3 × 10,000σ_pre), screening out unstable small denominators. This dispersion measure is not annualized return volatility.

The endpoint is a five-minute retrospective price reference, **not a proven equilibrium price or an isolated causal CPI effect**. It cannot be used as an available-at-release trading feature.

## 5. Normalize and fit the response

The observed response is D(τ) = [p(τ)−p⁻]/Δ. Thus D=0 is the baseline and D=1 is the endpoint reference. The response is not clipped: overshoot above one and movement in the opposite direction remain visible.

Fit the jump-plus-exponential curve:

    D_hat(τ) = 1 − (1−j) exp(−τ/k), for τ ≥ 0

Here j is the fitted initial fraction of the endpoint move and k is a positive time-scale parameter in seconds. A recorded grid searches j from 0 to 0.95 in increments of 0.05 and 101 logarithmically spaced k values from 0.25 seconds to twice the horizon. Squared reconstruction error selects the fit.

For a target fraction q:

    t_q = 0                         when j ≥ q
    t_q = k log[(1−j)/(1−q)]         otherwise

The residual-gap half-life is k log 2; it differs from t50 because the model permits an initial jump. A fitted t50 of zero means the fitted jump already crosses 50%; it does not prove instantaneous discovery. Three of five accepted fits are below the one-second sampling resolution.

## 6. Quality classification and primary results

Coverage or small-denominator failures are excluded before fitting. Fit-quality, overshoot, opposite-direction, boundary and extrapolation flags trigger review. “Accepted” means passing these implemented screens, not confirmation that the economic model is true.

| Currency | Responses | Accepted | Review | Excluded | Accepted median t50 |
|---|---:|---:|---:|---:|---:|
| EUR/USD | 35 | 2 | 9 | 24 | 3.588 s |
| GBP/USD | 35 | 3 | 8 | 24 | 0.000 s |
| Total | 70 | 5 | 17 | 48 | Not pooled |

Of the 48 exclusions, 44 fail fresh-quote coverage and four have too small a move for stable normalization. Among 17 review responses, 16 overshoot, 15 have poor monotone fit and 11 move in the opposite direction at some point; these flags overlap and must not be added as independent counts.

![Primary screening by year](figures/screening.png)

All accepted event-level estimates are shown below so the small sample is visible:

| Release date | Currency | Fitted jump j | t50 (s) | t90 (s) |
|---|---|---:|---:|---:|
| 2022-11-10 | EUR/USD | 0.40 | 7.177 | 70.527 |
| 2023-11-14 | EUR/USD | 0.50 | 0.000 | 118.078 |
| 2023-11-14 | GBP/USD | 0.45 | 6.469 | 115.705 |
| 2024-02-13 | GBP/USD | 0.60 | 0.000 | 101.707 |
| 2025-01-15 | GBP/USD | 0.60 | 0.000 | 87.045 |

EUR/USD has too few accepted events for the configured bootstrap interval. GBP/USD's conditional event-bootstrap interval for median t50 is [0, 6.469] seconds, based on only three events. Five hundred event resamples use seed 42; individual seconds are not treated as independent events.

Only one date passes for both currencies. Its EUR/USD-minus-GBP/USD t50 difference is −6.469 seconds. One paired observation cannot establish a cross-currency ranking. The separate medians above use different accepted-event cohorts and should not be interpreted as a race between currencies.

![Response examples](figures/responses.png)

Examples are chosen deterministically: the first chronological response in each status, with symbol breaking ties. They illustrate fit behavior, not representative average effects. All 70 responses remain in the [primary HTML report](reproduction.md).

## 7. Chronological evaluation

| Partition | Release dates | Accepted | Review | Excluded |
|---|---:|---:|---:|---:|
| Development: 2022–2024 | 27 | 4 | 14 | 36 |
| Later evaluation: 2025 | 8 | 1 | 3 | 12 |

The cutoff is 2025-01-01. Model settings, sensitivity choices and relevant input/code hashes were recorded before HistData response fitting. The initial ingestion freeze was revised before fitting to audit out-of-window clock rollbacks; the original record remains preserved. An initial January 2022 ingestion sample and an earlier 2024 Dukascopy pilot had already been inspected.

Consequently this is a fixed-settings later-period descriptive evaluation, not an untouched trading backtest. With only one accepted 2025 response, there is no meaningful later-period currency comparison. See the [2025 report](reproduction.md).

## 8. Robustness, alternative shapes and other news

Ten recorded scenarios cover the baseline, 30/120-second endpoint windows, 15/30-minute horizons, 5-second sampling, 5-second staleness, 85% coverage, and 250/500-millisecond sampling. They test choices, rather than selecting whichever produces the best-looking answer.

Only one event per currency survives every scenario. For that fixed EUR/USD event, fitted t50 ranges from 0 to 10.591 seconds; for the fixed GBP/USD event it ranges from 0 to 20.354 seconds. Subsecond resampling cannot repair underlying missing data or establish real-time receipt latency. The [complete sensitivity tables](reproduction.md) disclose both changing-cohort and fixed-cohort estimates.

![Sensitivity results](figures/sensitivity.png)

Alternative response shapes compare an instantaneous endpoint response, the two-parameter exponential and a flexible piecewise-linear model with ten fitted heights. Among review responses, median in-sample RMSE falls from 0.63246 for the exponential to 0.18009 for the flexible model. More parameters naturally allow closer reconstruction; this is evidence of shape mismatch, not predictive superiority. See [model checks](reproduction.md).

The five-minute news restriction retains 17 dates and yields 1 accepted, 9 review and 24 excluded responses. The thirty-minute restriction retains 16 dates and yields 2 accepted, 9 review and 21 excluded responses. The latter also uses a thirty-minute response horizon, so its change in acceptance is not solely a news-filter effect. These filters mean no other news was listed in the audited source window, not proof of no confounders. A July 2024 jobless-claims omission was corrected using a preserved DOL source before HistData fitting. Headline/core CPI and associated releases still form an announcement bundle.

## 9. Engineering, validation and reproduction

The commented Python package handles event validation, prior-only surprise history, causal quote alignment, response fitting, quality flags, event resampling, paired comparisons, sensitivity and chronological evaluation. Provider importers preserve originals and hashes. Generated reports retain failed windows rather than silently dropping them.

On 23 September 2026, all **91 tests passed again**, in the working project on Python 3.12 and the exported package on Python 3.9. The guided notebook's code cells also executed successfully. Figures are generated from saved analysis JSON with input hashes; the frozen analysis code and inputs remain unchanged.

A second full offline HistData run completed all stages. **All 32 scientific output files matched byte for byte**: fifteen analysis files, fifteen event-metrics files, the sensitivity summary and model comparison. Execution timestamps and output-path metadata are excluded from this equality claim. The [release checks](release-checks.md) document the comparison. The earlier 13-file Dukascopy replay remains a separate historical verification.

See [current reproduction instructions](reproduction.md). Raw provider files are local research inputs; the project does not assume redistribution rights. The previous [eight-date pilot report](historical-pilot.md) is retained as historical evidence and must not be merged with this provider's results.

## 10. What is complete and what the project demonstrates

Parts 1–3 completed data acquisition/import, the expanded analysis and robustness/evaluation. Step 4 now supplies this consolidated report, regenerated charts, model breakdown and reproduction instructions.

The defensible achievement is an auditable end-to-end market-data research pipeline plus a documented negative result: this public quote sample and simple monotone timing model do not support stable aggregate diffusion-speed claims. The large raw archive count does not overcome only five accepted responses. Better quote coverage, broader authenticated consensus history, independent providers and explicit causal or predictive designs would be further research, not results already demonstrated here.
