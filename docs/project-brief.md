# Project brief: Market Information Diffusion

**Question:** How quickly do EUR/USD and GBP/USD prices adjust around US CPI releases, and is a single fitted speed stable?

**Data:** 35 source-verified actual/consensus dates in 2022–2025; 70 public HistData monthly archives; 478,393 event-window quotes. Coverage is source-selected, not a complete census of releases.

**Execution:** Preserve source files and hashes → convert clocks to UTC → align fresh quotes without looking ahead → estimate pre-release and five-minute price references → normalize the response → fit initial jump and exponential adjustment → screen coverage and fit → evaluate later dates and sensitivity.

**Model:** D(t) = 1 − (1−j) exp(−t/k). The jump j and time scale k produce t50 and t90. The endpoint is retrospective, so this is descriptive research rather than a live signal.

**Evidence:** Only 5 of 70 responses pass the primary screens. Forty-four fail coverage, four fail the small-move screen and 17 require review. Only one date passes for both currencies; only one response passes in 2025. Ten sensitivity scenarios expose dependence on sampling and endpoint choices.

**Conclusion:** The study does not establish stable relative speed or trading profitability. Its contribution is a reproducible research pipeline and an explicit diagnosis of where available data and model assumptions fail.

**Engineering:** Commented, modular Python; raw-file integrity checks; prior-only surprise histories; timestamp and quote-staleness validation; event-level bootstrap; immutable run outputs; synthetic tests and offline empirical replay.

![Sensitivity](figures/sensitivity.png)

**Interview takeaway:** “I built the full workflow, tested whether its assumptions held, and reported the negative result rather than forcing a trading conclusion.”
