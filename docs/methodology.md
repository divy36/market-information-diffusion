# Research method and notation

This project studies **price adjustment following announcements**. Here, diffusion means information-related market adjustment; this is not a generative AI diffusion model or a stochastic differential equation model.

## 1. Define an announcement

For event e, T_e is the timezone-aware release timestamp. A_e is the first released actual value and C_e is the consensus recorded strictly before T_e. Actual and consensus must refer to the same statistic, period, adjustment convention, and units.

    U_e = A_e − C_e
    S_e = U_e / sample_sd({U_i: T_i < T_e, series_i = series_e})

U is the raw surprise. S expresses that surprise in units of historical surprise variability. The sample standard deviation uses denominator n−1. We require six earlier releases by default. Early or constant histories have a missing standardized surprise, not zero. The history is expanding; even rejected price windows may contribute their valid announcement surprises.

Series identifiers must distinguish headline/core, monthly/annual, and seasonally adjusted/unadjusted releases. The first version supports one scalar surprise per event. Multiple components released together need a later multivariate model; entering them as separate simultaneous events currently triggers an overlap exclusion.

## 2. Put quotes on an event clock

    τ = t − T_e
    midpoint(t) = (bid(t) + ask(t)) / 2

τ is elapsed seconds, so τ=−10 is ten seconds before the announcement. For each grid timestamp, use the most recent quote with quote_time ≤ grid_time. Never interpolate using a later quote. Quotes older than two seconds are missing by default. UTC offsets are mandatory; convert local dates using their historical timezone rules before import.

The default grid is [−60, 300] seconds at one-second spacing. At least 95% of the baseline, post-event, and endpoint grids must have fresh quotes. These are configurable research choices, not universal market constants. Last-observation sampling cannot recover activity faster than the data resolution.

## 3. Estimate the baseline and endpoint

Let p(τ) = log(midpoint(τ)). Define:

    p_minus = median(p(τ), −60 ≤ τ < 0)
    p_star  = median(p(τ), 240 < τ ≤ 300)
    Δ       = 10,000 × (p_star − p_minus)

Δ is a log-price move in approximate basis points. p_star is a fixed-horizon endpoint proxy, not an observed true equilibrium. The implementation uses the median of log prices, then exponentiates when reporting price levels.

Reject normalization when |Δ| < max(1 bp, 3 × baseline log-price sample SD in bps). The baseline SD is a dispersion screen, not an annualized volatility estimator. Median pre-event quoted spread is also recorded. Price trends and liquidity regime controls are future extensions.

## 4. Construct the observed response

    D(τ) = (p(τ) − p_minus) / (p_star − p_minus)

D=0 means the baseline price; D=1 means the selected endpoint. Negative moves work with the same formula. Values below zero and above one are retained: opposite-direction moves and overshoots are evidence against simple monotone adjustment. D is not an observed probability or literal percentage of all information absorbed.

## 5. Fit a transparent speed model

For τ≥0, the candidate curve is:

    D_hat(τ) = 1 − (1 − j) exp(−τ / k)

j is the immediate jump fraction and k>0 is an exponential time constant in seconds. Fit by unweighted least squares on valid post-event grid points. The explicit search uses j∈{0, .05, …, .95} and 101 log-spaced k values between .25 seconds and twice the post-event horizon. Equal grid weights describe elapsed-time fit rather than trade-count weighting.

    t_q = 0                           if j ≥ q
    t_q = k × log((1 − j)/(1 − q))    otherwise

t50 and t90 refer to 50% and 90% of the fitted total endpoint move. They differ from the residual half-life k log(2) when j>0. Sub-second/sub-grid values are resolution warnings, not evidence of precise millisecond discovery.

Compare RMSE against the instantaneous-response benchmark D_hat=1. Flag large residual error (>0.15 response units), grid boundaries, overshoot (>1.2), opposite response (<−0.2), or extrapolated t90. The thresholds are heuristic and require sensitivity analysis. A zero jump is allowed without a boundary rejection. A jump at .95 is flagged because an instantaneous adjustment may be unresolved.

An event is `excluded` before fitting when its data or denominator is unsuitable; `review` means a fit exists but fails a diagnostic; `accepted` means it passes the configured screens. Passing is not proof that the model is true. Only accepted events enter aggregate timing summaries, which can create selection bias; always report counts and inspect rejected cases.

## 6. Compare markets and uncertainty

For instruments A and B, compare t50_A,e − t50_B,e on the same accepted events. Negative differences mean A has the faster fitted response. This is a descriptive timing comparison, not identified causal propagation.

Resample events with replacement, then compute the median. Report percentile 2.5% and 97.5% endpoints using 500 draws by default (raise for publication work). For paired comparisons, resample the paired differences. No interval is reported below three events. Resampling individual seconds would spuriously treat autocorrelated observations as independent. Event bootstrap still assumes suitable event-level independence and excludes model/endpoint uncertainty; block or regime-aware inference is future work.

## 7. What this version cannot establish

- No trading edge or out-of-sample forecasting claim: p_star uses future data.
- No clean causal attribution without controls for simultaneous releases and other news.
- No real empirical result from the demo: it deliberately generates exponential responses plus one misspecified case.
- No direct estimate of the effect of surprise magnitude on prices; S_e is prepared and exported for later regressions.
- No universally optimal endpoint. Repeat the analysis with several prespecified endpoint windows and horizons before reporting conclusions.
- No historical consensus validation from release data alone. Official release pages can also reflect subsequent revisions; retain first-release vintages.

## References and motivation

- Andersen, Bollerslev, Diebold and Vega, *Micro Effects of Macro Announcements: Real-Time Price Discovery in Foreign Exchange*: https://www.nber.org/papers/w8959
- Andersen, Bollerslev, Diebold and Vega, *Real-Time Price Discovery in Global Stock, Bond and Foreign Exchange Markets*: https://www.federalreserve.gov/econres/ifdp/real-time-price-discovery-in-global-stock-bond-and-foreign-exchange-markets.htm
- BLS CPI release archive and its revision notice: https://www.bls.gov/bls/news-release/cpi.htm

These sources motivate announcement studies and vintage discipline. The exponential curve, thresholds, and implementation here are this project's modelling choices, not a replication of those papers.
