# Data contract

## events.csv — one information event per row

| Column | Meaning |
|---|---|
| event_id | Unique stable identifier |
| series | Exact statistic, e.g. US_CPI_HEADLINE_MOM_SA |
| release_utc | ISO-8601 timestamp with timezone offset |
| actual | First-release value, finite number |
| consensus | Same-unit pre-release consensus, finite number |
| consensus_asof_utc | Timestamp strictly before the release |
| unit | Stable within each series, e.g. percentage_points |
| source | Provider and vintage identifier; SIMULATED for fixtures |

If 0.3 means 0.3%, use the same convention for both actual and consensus. A surprise of 0.1 percentage points equals 10 basis points of an inflation rate; this is separate from price-return basis points.

Include older announcements if needed for surprise-scale warm-up. Events without quote coverage remain in the audit output and are excluded from price estimation. Do not substitute a forecast published after the release. Do not treat a subsequently revised economic series as the first release.

## quotes.csv — top-of-book quote observations

| Column | Meaning |
|---|---|
| symbol | Instrument and contract identifier |
| timestamp_utc | Time the quote was available, with explicit UTC offset |
| bid | Finite positive best bid |
| ask | Finite best ask, at least bid |

One row per symbol/timestamp. Duplicate timestamps are rejected rather than silently collapsed. If a source has sequence numbers, aggregate deterministically upstream and document the policy. Quotes may be in any order; the loader sorts them. This is a CSV reference implementation that loads data in memory, intended for event-window extracts rather than entire exchange archives.

Trades, OHLC candles and bid/ask quotes are not interchangeable. Do not fill bid and ask with candle close to imply quote-level precision. For futures, document contract selection and roll rules and avoid stitching discontinuities inside an event window. No exchange-feed downloader is implemented in this version.

## Provenance checklist for the real-data study

For each dataset, record provider, retrieval date, permitted usage, original file hash, instrument definition, timestamp precision, timezone convention, exchange versus receive timestamp, adjustments, and cleaning rules in `docs/data-catalog.md`. Keep downloaded originals under `data/raw/` and transformations separately. Do not publish vendor data unless its licence permits it.

`--mode supplied` means user-supplied, not independently authenticated. Run manifests hash both input files and source modules. The CLI rejects simulation-marked event sources in supplied mode, but cannot authenticate arbitrary files.

## Initial scope

Proposed empirical study: US CPI releases and S&P 500/Treasury futures, subject to the user's selection and available data. No real market or consensus data is bundled. BLS supplies releases; historical pre-release consensus and intraday quote data require separately identified sources. Scheduled times should be verified against historical releases rather than inferred from today's calendar.
