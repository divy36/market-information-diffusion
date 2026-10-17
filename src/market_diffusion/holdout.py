"""Auditable chronological evaluation of retrospective response statistics.

A freeze records choices; it cannot prove the researcher never viewed data.
Later events are evaluated with the same code/settings, without automatic tuning.
"""

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .alignment import Config
from .data import read_events, utc
from .pipeline import digest, run


def code_hashes():
    return {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))}


def partition(events, cutoff, config):
    """Assign equal-time releases together and reject crossing analysis windows."""
    development = [event for event in events if event.release < cutoff]
    holdout = [event for event in events if event.release >= cutoff]
    if not development or not holdout:
        raise ValueError("Cutoff must leave events in both chronological partitions")
    end = max(event.release for event in development) + timedelta(seconds=config.post_seconds)
    start = min(event.release for event in holdout) - timedelta(seconds=config.pre_seconds)
    if end >= start:
        raise ValueError("Development and holdout analysis windows overlap; choose another cutoff")
    return {"development": [event.event_id for event in development],
            "holdout": [event.event_id for event in holdout]}


def freeze(events_path: Path, config_path: Path, cutoff_text: str, output: Path, mode: str):
    """Freeze an event calendar and settings without opening any quote file."""
    if mode not in ("simulation", "supplied"):
        raise ValueError("Mode must be simulation or supplied")
    config = Config(**json.loads(config_path.read_text()))
    events = read_events(events_path)
    if mode == "supplied" and any("SIMULATED" in event.source.upper() for event in events):
        raise ValueError("Simulation-marked inputs require simulation mode")
    cutoff = utc(cutoff_text)
    result = {"version": 1, "mode": mode, "created_utc": datetime.now(timezone.utc).isoformat(),
              "cutoff_utc": cutoff.isoformat(), "events_sha256": digest(events_path),
              "config": asdict(config), "code_sha256": code_hashes(),
              "partitions": partition(events, cutoff, config),
              "interpretation": "Retrospective descriptive evaluation; no proof of untouched data or prediction."}
    # Exclusive creation preserves the original record; revisions need a new path.
    with output.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    return result


def evaluate(events_path: Path, quotes_path: Path, protocol_path: Path, phase: str,
             output: Path, development_path=None):
    """Verify the freeze, then evaluate only the requested chronological partition.

    Holdout surprise scales expand using strictly earlier releases, including
    development events. This is an as-of convention, not full-sample scaling.
    """
    if phase not in ("development", "holdout"):
        raise ValueError("Phase must be development or holdout")
    if output.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    protocol = json.loads(protocol_path.read_text())
    required = {"version", "mode", "created_utc", "cutoff_utc", "events_sha256", "config",
                "code_sha256", "partitions", "interpretation"}
    if not isinstance(protocol, dict) or set(protocol) != required or protocol["version"] != 1:
        raise ValueError("Invalid holdout protocol")
    if protocol["events_sha256"] != digest(events_path):
        raise ValueError("Event input differs from the frozen calendar")
    if protocol["code_sha256"] != code_hashes():
        raise ValueError("Code differs from the frozen implementation; create a documented revision")
    config = Config(**protocol["config"])
    expected = partition(read_events(events_path), utc(protocol["cutoff_utc"]), config)
    if expected != protocol["partitions"]:
        raise ValueError("Partition membership does not match the frozen cutoff")
    protocol_hash, quote_hash = digest(protocol_path), digest(quotes_path)
    development_hash = None
    if phase == "holdout":
        if development_path is None:
            raise ValueError("Holdout evaluation requires a completed development run")
        record = Path(development_path) / "evaluation.json"
        development = json.loads(record.read_text())
        if any(development.get(key) != value for key, value in {
            "status": "complete", "phase": "development", "protocol_sha256": protocol_hash,
            "quotes_sha256": quote_hash}.items()):
            raise ValueError("Development run must use the same protocol and quote dataset")
        development_hash = digest(record)
    result = run(events_path, quotes_path, output, config, protocol["mode"], expected[phase])
    if digest(protocol_path) != protocol_hash or digest(quotes_path) != quote_hash or digest(events_path) != protocol["events_sha256"]:
        raise ValueError("Inputs changed during evaluation; output is not a completed evaluation")
    audit = {"status": "complete", "phase": phase, "mode": protocol["mode"],
             "protocol_sha256": protocol_hash, "quotes_sha256": quote_hash,
             "development_record_sha256": development_hash,
             "selected_event_ids": expected[phase], "counts": result["counts"],
             "summary": result["summary"], "created_utc": datetime.now(timezone.utc).isoformat()}
    (output / "evaluation.json").write_text(json.dumps(audit, indent=2, allow_nan=False) + "\n")
    return audit
