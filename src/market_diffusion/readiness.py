"""Read-only checks of the CPI pilot's calendar, request windows and quote coverage.

This command never contacts a provider or fits a price-response model. A matching
request plan is not evidence that any quotes have actually been downloaded.
"""

import json
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from .alignment import Config, QuoteIndex
from .data import read_events, read_quotes, rows, standardized_surprises, utc
from .databento_api import read_plan
from .pipeline import digest


def check_readiness(events_path: Path, actuals_path: Path, plan_path: Path,
                    output: Path, config: Config, quotes_path=None):
    if output.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    events = read_events(events_path)
    actuals = rows(actuals_path, ["event_id", "release_utc"])
    calendar = {}
    for row in actuals:
        if row["event_id"] in calendar:
            raise ValueError("Duplicate event in actual-release calendar")
        calendar[row["event_id"]] = utc(row["release_utc"])
    for event in events:
        if calendar.get(event.event_id) != event.release:
            raise ValueError("Joined event does not match the actual-release calendar")
    requests = read_plan(plan_path)
    histories = standardized_surprises(events, config.min_history)
    quotes = read_quotes(Path(quotes_path)) if quotes_path is not None else None
    indexes = {symbol: QuoteIndex(values) for symbol, values in quotes.items()} if quotes else {}
    records = []
    for event in events:
        for symbol in ("ES", "ZN"):
            matching = [request for request in requests
                        if request["event_id"] == event.event_id and request["root"] == symbol]
            # The earliest grid point may need a still-fresh preceding quote.
            start = event.release - timedelta(seconds=config.pre_seconds + config.max_staleness_seconds)
            end = event.release + timedelta(seconds=config.post_seconds)
            planned = len(matching) == 1 and utc(matching[0]["start"]) <= start and utc(matching[0]["end"]) > end
            coverage = None
            if symbol in indexes:
                grid = indexes[symbol].align(event, config)
                segments = {"pre": [point for point in grid if point.tau < 0],
                            "post": [point for point in grid if point.tau >= 0],
                            "endpoint": [point for point in grid if point.tau > config.post_seconds - config.endpoint_seconds]}
                coverage = {name: sum(point.mid is not None for point in points) / len(points)
                            for name, points in segments.items()}
            records.append({"event_id": event.event_id, "symbol": symbol,
                "plan_window_valid": planned, "quote_coverage": coverage,
                "coverage_pass": coverage is not None and min(coverage.values()) >= config.min_coverage})
    blocking = []
    if any(not row["plan_window_valid"] for row in records):
        blocking.append("Missing, duplicated or too-short planned event/market windows")
    if quotes is None:
        blocking.append("No quote file supplied; real price coverage is unverified")
    elif any(not row["coverage_pass"] for row in records):
        blocking.append("Some event/market windows fail fresh-quote coverage")
    event_mode = "simulation" if any("SIMULATED" in event.source.upper() for event in events) else "supplied"
    result = {"status": "blocked" if blocking else "ready_for_quality_analysis", "event_mode": event_mode,
        "config": asdict(config), "actual_release_count": len(calendar), "joined_event_count": len(events),
        "unjoined_event_ids": sorted(set(calendar) - {event.event_id for event in events}),
        "standardized_surprise_available": sum(row["standardized_surprise"] is not None for row in histories.values()),
        "surprise_history": histories, "request_count": len(requests),
        "api_key_present": bool(os.environ.get("DATABENTO_API_KEY")),
        "quotes_supplied": quotes is not None, "blocking_issues": blocking, "event_windows": records,
        "limits": "Readiness is not source authentication, accepted fits, a sufficiently powered sample or empirical validation."}
    paths = [events_path, actuals_path, plan_path] + ([Path(quotes_path)] if quotes_path is not None else [])
    output.mkdir(parents=True, exist_ok=False)
    (output / "readiness.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    (output / "manifest.json").write_text(json.dumps({"inputs": {str(path.resolve()): digest(path) for path in paths},
        "code_sha256": {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
        "config": asdict(config)}, indent=2) + "\n")
    lines = ["# CPI pilot data readiness — " + event_mode.upper(), "", "Status: **" + result["status"] + "**", "",
        "| Check | Result |", "|---|---:|",
        "| Actual release dates | %d |" % len(calendar),
        "| Joined events with consensus | %d |" % len(events),
        "| Dates without a joined event | %d |" % len(result["unjoined_event_ids"]),
        "| Events with a prior-only standardized surprise | %d |" % result["standardized_surprise_available"],
        "| Planned requests | %d |" % len(requests),
        "| Valid planned event/market windows | %d / %d |" % (sum(row["plan_window_valid"] for row in records), len(records)),
        "| Quote file supplied | %s |" % result["quotes_supplied"],
        "| API key present in this process | %s |" % result["api_key_present"], "",
        "API-key presence does not establish authentication, entitlements or price coverage. "
        "A key is unnecessary when a valid local quote file is supplied.", "", "## Pending items", ""]
    lines += ["- " + issue for issue in blocking] or ["- Coverage is sufficient to proceed to quality analysis; fitting checks still apply."]
    lines += ["", result["limits"], "", "Detailed history, missing dates and individual window coverage are in readiness.json.", ""]
    (output / "report.md").write_text("\n".join(lines))
    return {"status": result["status"], "blocking_issues": blocking, "report": str((output / "report.md").resolve())}
