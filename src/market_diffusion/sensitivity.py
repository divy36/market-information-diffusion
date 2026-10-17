"""Compare prespecified settings without choosing a winner from the results.

Each scenario uses the ordinary pipeline. Matched-event summaries distinguish
changes in fitted timing from changes in which events pass quality screens.
"""

import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .alignment import Config
from .models import bootstrap_median
from .pipeline import digest, run


def read_plan(path: Path) -> tuple:
    """Resolve overrides against one base configuration before running any fits."""
    plan = json.loads(path.read_text())
    if not isinstance(plan, dict) or set(plan) != {"baseline", "base_config", "scenarios"}:
        raise ValueError("Plan requires baseline, base_config and scenarios only")
    base = Config(**plan["base_config"])
    if not isinstance(plan["scenarios"], list) or len(plan["scenarios"]) < 2:
        raise ValueError("Need at least two sensitivity scenarios")
    resolved = {}
    for entry in plan["scenarios"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "overrides"}:
            raise ValueError("Each scenario requires name and overrides only")
        name = entry["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("Scenario name must be a safe lowercase identifier")
        if name in resolved:
            raise ValueError("Duplicate scenario name")
        if not isinstance(entry["overrides"], dict):
            raise ValueError("Scenario overrides must be an object")
        resolved[name] = Config(**{**asdict(base), **entry["overrides"]})
    if not isinstance(plan["baseline"], str) or plan["baseline"] not in resolved:
        raise ValueError("Baseline must name a scenario")
    return plan["baseline"], resolved


def compare(payloads: dict, baseline: str) -> dict:
    """Summarize t50 on both variable and fixed accepted-event populations.

    Pairwise deltas are computed event by event, then bootstrapped over events;
    subtracting two aggregate medians would answer a different question.
    The all-scenario intersection is deliberately conservative and may be empty.
    """
    reference = payloads[baseline]
    samples, seed = reference["config"]["bootstrap_samples"], reference["config"]["seed"]
    symbols = sorted({row["symbol"] for payload in payloads.values() for row in payload["results"]})
    output = {}
    for symbol in symbols:
        accepted = {
            name: {row["event_id"]: row["fit"]["t50_seconds"] for row in payload["results"]
                   if row["symbol"] == symbol and row["status"] == "accepted"}
            for name, payload in payloads.items()
        }
        common = sorted(set.intersection(*(set(values) for values in accepted.values())))
        scenarios = {}
        for name, payload in payloads.items():
            records = [row for row in payload["results"] if row["symbol"] == symbol]
            matched = sorted(accepted[name].keys() & accepted[baseline].keys())
            scenarios[name] = {
                "total": len(records),
                "counts": {status: sum(row["status"] == status for row in records)
                           for status in ("accepted", "review", "excluded")},
                "flag_counts": dict(Counter(flag for row in records for flag in row["flags"])),
                "all_accepted_t50": bootstrap_median(list(accepted[name].values()), samples, seed),
                "common_t50": bootstrap_median([accepted[name][key] for key in common], samples, seed),
                "common_delta_vs_baseline": bootstrap_median(
                    [accepted[name][key] - accepted[baseline][key] for key in common], samples, seed),
                "pairwise_event_ids": matched,
                "pairwise_delta_vs_baseline": bootstrap_median(
                    [accepted[name][key] - accepted[baseline][key] for key in matched], samples, seed),
            }
        output[symbol] = {"common_event_ids": common, "scenarios": scenarios}
    return output


def write_report(path: Path, summary: dict) -> None:
    """Produce a portable Markdown table; missing cohorts remain visibly missing."""
    def number(value):
        return "unavailable" if value is None else "%.3f" % value

    lines = ["# Response sensitivity — " + summary["mode"].upper(), "",
             "SIMULATION ONLY; no empirical market findings." if summary["mode"] == "simulation"
             else "Supplied observations; retrospective descriptive analysis, not a causal or trading test.",
             "", "Baseline: `" + summary["baseline"] + "`. All timing values are seconds.", "",
             "Common-cohort columns use only events accepted in EVERY scenario for that symbol. "
             "Deltas are medians of within-event scenario minus baseline differences. "
             "An empty intersection is unavailable, never zero. Full intervals, event IDs and flag counts "
             "are in summary.json. Each scenario folder contains its own report and audit manifest.", ""]
    for symbol, group in summary["symbols"].items():
        # JSON escaping keeps user-supplied instrument text out of Markdown markup.
        label = json.dumps(symbol, ensure_ascii=True).replace("|", "\\|").replace("`", "\\`")
        lines += ["## " + label, "", "Common accepted events: " + str(len(group["common_event_ids"])), "",
                  "| Scenario | Accepted / total | Review | Excluded | All accepted median t50 | Common median t50 | Common median delta |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for name, row in group["scenarios"].items():
            counts = row["counts"]
            lines.append("| %s | %d / %d | %d | %d | %s | %s | %s |" % (
                name, counts["accepted"], row["total"], counts["review"], counts["excluded"],
                number(row["all_accepted_t50"]["median"]), number(row["common_t50"]["median"]),
                number(row["common_delta_vs_baseline"]["median"])))
        lines += [""]
    lines += ["These checks do not select an optimal setting. Conditional accepted-event intervals "
              "do not cover selection bias, endpoint uncertainty or multiple testing. See the prespecified "
              "plan for exact settings; horizon changes also change the existing model's scale grid.", ""]
    path.write_text("\n".join(lines))


def run_sensitivity(events: Path, quotes: Path, plan_path: Path, output: Path, mode: str) -> dict:
    """Write a new, auditable multi-scenario run; never overwrite an earlier run."""
    if mode not in ("simulation", "supplied"):
        raise ValueError("Mode must be simulation or supplied")
    if output.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    baseline, configs = read_plan(plan_path)
    inputs = {str(path.resolve()): digest(path) for path in (events, quotes, plan_path)}
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "running", "mode": mode, "baseline": baseline, "inputs": inputs,
                "resolved_configs": {name: asdict(config) for name, config in configs.items()}}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        payloads = {}
        for name, config in configs.items():
            payload = run(events, quotes, output / name, config, mode)
            # Retain metrics only; full curves are already saved by the pipeline.
            for row in payload["results"]:
                row.pop("curve", None)
            payloads[name] = payload
            if any(digest(Path(path)) != value for path, value in inputs.items()):
                raise ValueError("Inputs changed during sensitivity run; discard this comparison")
        summary = {"mode": mode, "baseline": baseline, "symbols": compare(payloads, baseline)}
        (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        write_report(output / "report.md", summary)
        manifest["status"] = "complete"
    except Exception as error:
        # Preserve completed scenarios for diagnosis, but never label the run complete.
        manifest.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return summary
