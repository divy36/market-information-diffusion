# Release verification — 23 September 2026

- Full offline HistData replay completed using the original audited local inputs.
- All **32 scientific outputs matched byte for byte**: fifteen analysis JSON files, fifteen event-metrics CSV files, one sensitivity summary and one model-comparison JSON file. Run timestamps, filesystem locations and generated HTML metadata are outside this equality claim.
- All **91 tests passed** afresh in the working project on Python 3.12 and in the exported package on Python 3.9.
- Every code cell of the new guided notebook executed in order on Python 3.9; the synthetic run produced 46 accepted and two review responses. These simulated counts are not the real-study counts.
- Exported text files were scanned for private home paths, private-key headers and common credential/token patterns; no matches were found. This is a bounded pattern scan, not a guarantee against every possible secret format.
- Raw/processed third-party data, environment files, caches and original local result manifests are excluded. Only derived charts and aggregate empirical summaries are included.
- Markdown file links were checked. The public walkthrough retains numerical outputs for reading on GitHub; these are checked again during release preparation.

[File-by-file hashes](reproducibility.json) document the empirical comparison. A public recipient can run the synthetic workflow immediately; reproducing empirical outputs requires the audited data inputs described in [reproduction instructions](reproduction.md).

No open-source license has been selected; third-party data redistribution rights are not granted.

## Publication verification — 16 October 2026

- The prepared source, tests and configurations match the original research workspace.
- All 91 unit tests passed on Python 3.9.6.
- All five code cells of the guided notebook executed successfully.
- The complete labelled synthetic reproduction finished, including chronological
  evaluation, sensitivity analysis and model checks.
- The 32-output empirical replay above is the historical September verification;
  this publication check did not repeat the private-data study.
- GitHub Actions runs the tests, demo, notebook and synthetic reproduction on
  Python 3.9 and 3.12.
