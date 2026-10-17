# Setup and reproduction

## Run without market-data access

Python 3.9 or newer is sufficient for the core analysis. Run commands from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m market_diffusion demo --output results/demo
python -m unittest discover -s tests -q
```

Open `results/demo/analysis/report.html`. This generates explicitly labelled synthetic data and does not reproduce the empirical findings. Choose a new output directory each time.

For the complete synthetic workflow with chronological evaluation and robustness:

```sh
PYTHONPATH=src python scripts/reproduce.py --output results/simulation-replay
```

## Inspect the guided notebook

`notebooks/02_portfolio_walkthrough.ipynb` runs without third-party imports. Open it in a notebook viewer; optional JupyterLab installation is `python -m pip install -r requirements-notebooks.txt`. Its cells locate the repository, demonstrate the mathematics, generate a synthetic study, and read the included aggregate real-study results. Restarting and running all cells is supported; each demo uses a fresh temporary directory.

## Reproduce empirical findings with locally acquired data

Third-party raw archives, consensus PDFs, release extracts and transformed observations are deliberately not distributed. The public package contains derived charts and aggregate findings. Exact local HTML reports and per-event evidence referenced by the report are not included; this page replaces their private filesystem links.

Required local paths:

- `data/processed/cpi-2022-2025-headline-v1/events.csv`
- `data/interim/histdata/cpi-2022-2025-v1/quotes.csv`
- `data/interim/consensus-2022-2025-v1/news_audit_v2.json`

Use `docs/data-contract.md` for observation formats. Acquisition and import code is available, but rebuilding the audited consensus/news inputs requires the source-vintage review described in the report; there is no claim of a one-click public data reconstruction.

With the original audited inputs present:

```sh
PYTHONPATH=src python scripts/run_histdata_study.py --output results/histdata-replay
```

This runs both chronological partitions, the primary analysis, ten sensitivity scenarios, shape diagnostics and two news cohorts. Output manifests record hashes and execution metadata. Compare scientific outputs, not timestamps or output-location strings.

Optional charts require Python 3.12 and `python -m pip install -r requirements-figures.txt`. The figure script reads canonical `results/histdata-expanded-v2`, so retain that original run or adjust its study path explicitly. Provider SDKs are not required for the offline study or public demo.
