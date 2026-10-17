"""Run the same analysis on explicit simulations or user-supplied CSV files."""

import csv
import hashlib
import json
import platform
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from . import __version__
from .alignment import Config, QuoteIndex
from .analysis import analyze
from .data import read_events, read_quotes, standardized_surprises
from .models import bootstrap_median
from .report import render


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(events_path: Path, quotes_path: Path, output: Path, config: Config, mode: str,
        event_ids=None) -> dict:
    if mode not in ("simulation", "supplied"):
        raise ValueError("Mode must be simulation or supplied")
    if output.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    events, quotes = read_events(events_path), read_quotes(quotes_path)
    selected = set(event_ids) if event_ids is not None else {event.event_id for event in events}
    if not selected or not selected <= {event.event_id for event in events}:
        raise ValueError("Selected event IDs must be a nonempty subset of the event table")
    if mode == "supplied" and any("SIMULATED" in event.source.upper() for event in events):
        raise ValueError("Simulation-marked inputs require --mode simulation")
    surprises = standardized_surprises(events, config.min_history)
    indexes = {symbol: QuoteIndex(values) for symbol, values in quotes.items()}
    results = []
    for event in events:
        # Keep the FULL calendar for prior-only surprise scales and overlap checks.
        # Filtering the input CSV would lose development history at the holdout boundary.
        if event.event_id not in selected:
            continue
        # Conservatively reject intersecting analysis windows. This only catches
        # events present in the input table; unrecorded news still contaminates.
        overlap = any(other.event_id != event.event_id and
                      abs((other.release - event.release).total_seconds()) <= config.pre_seconds + config.post_seconds
                      for other in events)
        for symbol, index in indexes.items():
            result = analyze(event, symbol, index.align(event, config), config, overlap)
            result.update(surprises[event.event_id])
            result["release_utc"] = event.release.isoformat()
            results.append(result)
    accepted = [result for result in results if result["status"] == "accepted"]
    summary = {symbol: bootstrap_median([item["fit"]["t50_seconds"] for item in accepted
                                        if item["symbol"] == symbol], config.bootstrap_samples, config.seed)
               for symbol in sorted(quotes)}
    paired = {}
    for first, second in combinations(sorted(quotes), 2):
        a = {item["event_id"]: item["fit"]["t50_seconds"] for item in accepted if item["symbol"] == first}
        b = {item["event_id"]: item["fit"]["t50_seconds"] for item in accepted if item["symbol"] == second}
        # Pair within the SAME event before resampling, preserving common news.
        differences = [a[event_id] - b[event_id] for event_id in sorted(a.keys() & b.keys())]
        paired[first + " − " + second] = bootstrap_median(differences, config.bootstrap_samples, config.seed)
    payload = {"mode": mode, "config": asdict(config), "counts": dict(Counter(item["status"] for item in results)),
               "summary": summary, "paired_comparisons": paired, "results": results}
    output.mkdir(parents=True, exist_ok=False)
    (output / "analysis.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    metadata = {"package_version": __version__, "created_utc": datetime.now(timezone.utc).isoformat(),
                "python": platform.python_version(), "mode": mode, "config": asdict(config),
                "selected_event_ids": sorted(selected),
                "inputs": {str(path.resolve()): digest(path) for path in (events_path, quotes_path)},
                "code_sha256": {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))}}
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with (output / "event_metrics.csv").open("w", newline="") as stream:
        fields = ["event_id", "symbol", "release_utc", "status", "flags", "surprise", "standardized_surprise",
                  "move_bps", "t50_seconds", "t90_seconds", "jump", "scale_seconds", "rmse"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in results:
            flat = {**item, **(item["fit"] or {}), "flags": ";".join(item["flags"])}
            writer.writerow({field: flat.get(field) for field in fields})
    render(output / "report.html", payload)
    return payload
