"""Import archived CPI release extracts without manufacturing expectations.

Input is the saved, line-numbered text returned by the research web reader.
It is NOT original HTML or a contemporaneously captured release. We retain
those exact extracts and hash them so the transformation can be audited offline.
"""

import csv
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .data import finite, rows, utc


SERIES = {"headline": "US_CPI_HEADLINE_MOM_SA", "core": "US_CPI_CORE_MOM_SA"}
CONSENSUS_FIELDS = ["event_id", "series", "reference_month", "unit", "consensus",
                    "consensus_asof_utc", "source", "evidence_file"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_table(path: Path, records: list, fields=None):
    """Use exclusive creation: research outputs must never overwrite inputs."""
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def parse_release(snapshot: str) -> dict:
    """Read the CURRENT month columns in Table A of a BLS release extract.

    Earlier monthly columns can contain revisions. Reading the second-last
    numeric column retrieves this release's monthly headline/core changes;
    the last column is the unadjusted year-on-year rate. Fail on layout changes
    instead of silently using a different column.
    """
    urls = set(re.findall(r"https://www\.bls\.gov/news\.release/archives/cpi_\d{8}\.htm", snapshot))
    if len(urls) != 1:
        raise ValueError("Expected exactly one official archived CPI URL")
    source = urls.pop()
    text = re.sub(r"^L\d+:\s*", "", snapshot, flags=re.M)
    normalized = " ".join(text.split())
    stamp = re.search(
        r"embargoed until\s+(\d{1,2}):(\d{2})\s+(a\.m\.|p\.m\.)\s+\(ET\)\s+"
        r"(?:([A-Za-z]+),\s+)?([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", normalized)
    if not stamp:
        raise ValueError("Missing release embargo timestamp")
    hour, minute, period, weekday, month, day, year = stamp.groups()
    date = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
    if weekday and date.strftime("%A") != weekday:
        raise ValueError("Release weekday does not match its date")
    hour = int(hour)
    if not 1 <= hour <= 12:
        raise ValueError("Invalid release hour")
    hour = hour % 12 + (12 if period == "p.m." else 0)
    # New York timezone rules select EST/EDT for the HISTORICAL release date.
    local = date.replace(hour=hour, minute=int(minute), tzinfo=ZoneInfo("America/New_York"))
    url_date = datetime.strptime(re.search(r"cpi_(\d{8})", source).group(1), "%m%d%Y")
    if local.date() != url_date.date():
        raise ValueError("Release timestamp disagrees with archive filename")
    reference = re.search(r"CONSUMER PRICE INDEX\s*[-–—�]\s*([A-Z]+)\s+(\d{4})", normalized)
    if not reference:
        raise ValueError("Missing CPI reference month")
    reference_date = datetime.strptime(" ".join(reference.groups()), "%B %Y")
    if (reference_date.year, reference_date.month) >= (local.year, local.month):
        raise ValueError("Reference month must precede the release")
    if "Table A. Percent changes in CPI for All Urban Consumers" not in text:
        raise ValueError("Missing Table A")
    table = text.split("Table A. Percent changes in CPI for All Urban Consumers", 1)[1]
    table = table.split("Footnotes", 1)[0]
    if "Seasonally adjusted changes from preceding month" not in " ".join(table.split()):
        raise ValueError("Missing monthly seasonal-adjustment header")

    def values(label):
        matches = re.findall(r"^" + re.escape(label) + r"\s*\|([^\n]+)", table, re.M)
        if not matches:
            # Older releases use a fixed-width table, with core split over two
            # lines. Restrict extraction to Table A and demand all eight columns.
            old_label = r"All items" if label == "All items" else r"All items less food and\s+energy"
            matches = re.findall(r"(?:^|\n)\s*" + old_label + r"\.+\s*([^\n]+)", table)
            if len(matches) != 1:
                raise ValueError("Missing or ambiguous Table A row: " + label)
            cells = matches[0].split()
        else:
            if len(matches) != 1:
                raise ValueError("Ambiguous Table A row: " + label)
            cells = matches[0].split("|")
        numbers = [finite(value.strip(), label) for value in cells]
        if len(numbers) != 8:
            raise ValueError("Table A column count changed: " + label)
        return numbers[-2], numbers[-1]

    headline_mom, headline_yoy = values("All items")
    core_mom, core_yoy = values("All items less food and energy")
    return {
        "event_id": "US-CPI-" + local.date().isoformat(),
        "release_utc": local.astimezone(timezone.utc).isoformat(),
        "release_local": local.isoformat(), "reference_month": reference_date.strftime("%Y-%m"),
        "headline_mom_sa": headline_mom, "core_mom_sa": core_mom,
        "headline_yoy_nsa": headline_yoy, "core_yoy_nsa": core_yoy,
        "unit": "percentage_points", "source": source,
        "timestamp_basis": "published_embargo_time_not_measured_dissemination",
        "vintage_basis": "archived_release_current_month_retrieved_later",
    }


def import_releases(raw_directory: Path, output: Path) -> dict:
    """Build an actuals table, empty consensus template, and quote request plan."""
    if output.exists():
        raise ValueError("Output directory exists; choose a new import directory")
    paths = sorted(raw_directory.glob("cpi_*.txt"))
    if not paths:
        raise ValueError("No cpi_*.txt source extracts found")
    records, source_files = [], []
    for path in paths:
        record = parse_release(path.read_text(encoding="utf-8"))
        record["snapshot_sha256"] = sha256(path)
        records.append(record)
        source_files.append({"file": path.name, "sha256": sha256(path), "source": record["source"]})
    records.sort(key=lambda record: record["release_utc"])
    if len({record["event_id"] for record in records}) != len(records):
        raise ValueError("Duplicate CPI release date")
    if len({record["reference_month"] for record in records}) != len(records):
        raise ValueError("Duplicate CPI reference month")
    output.mkdir(parents=True, exist_ok=False)
    write_table(output / "actuals.csv", records)
    templates = []
    windows = []
    for record in records:
        for series in SERIES.values():
            templates.append({"event_id": record["event_id"], "series": series,
                              "reference_month": record["reference_month"], "unit": record["unit"],
                              "consensus": "", "consensus_asof_utc": "", "source": "", "evidence_file": ""})
        release = utc(record["release_utc"])
        windows.append({"event_id": record["event_id"], "release_utc": record["release_utc"],
                        "start_inclusive_utc": (release - timedelta(seconds=62)).isoformat(),
                        "end_exclusive_utc": (release + timedelta(seconds=1801)).isoformat(),
                        "instrument_contracts": [], "contract_selection_status": "pending"})
    write_table(output / "consensus_template.csv", templates, CONSENSUS_FIELDS)
    plan = {"status": "request_plan_only_no_download_or_purchase", "proposed_markets": ["S&P 500 futures", "10-year Treasury futures"],
            "quote_type": "best_bid_and_ask", "timestamp_preference": "receive_time_with_documented_clock_basis",
            "purpose": "One-second sampling and prespecified 5/15/30-minute endpoint sensitivity",
            "windows": windows}
    (output / "quote_request_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "source_format": "web_reader_line_numbered_extract_not_original_html",
                "raw_directory": str(raw_directory.resolve()), "source_files": source_files,
                "parser_sha256": sha256(Path(__file__)), "actuals_sha256": sha256(output / "actuals.csv"),
                "release_count": len(records), "consensus_records": 0, "quote_records": 0}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lines = ["# CPI pilot data readiness", "", f"Parsed {len(records)} archived CPI releases.", "",
             "| Release (UTC) | Reference month | Headline MoM SA | Core MoM SA |", "|---|---|---:|---:|"]
    for record in records:
        lines.append(f"| {record['release_utc']} | {record['reference_month']} | {record['headline_mom_sa']:.1f}% | {record['core_mom_sa']:.1f}% |")
    lines += ["", "Actuals and published release clocks are available. Historical pre-release consensus and intraday quotes are still missing.",
              "", "The consensus template intentionally leaves unknown observations blank. It cannot be passed to the analysis pipeline until validated enrichment succeeds.",
              "", "Headline and core belong to the same information event. Keep both components for later multivariate work; do not count them as independent announcements.",
              "", "These are archived current-month release values retrieved later, not contemporaneous captures. Release clocks are published embargo times, not measured dissemination latency."]
    (output / "readiness.md").write_text("\n".join(lines) + "\n")
    return {"releases": len(records), "actuals": str((output / "actuals.csv").resolve()),
            "ready_for_market_analysis": False, "missing": ["pre-release consensus evidence", "intraday quotes"]}


def join_consensus(actuals_path: Path, consensus_path: Path, output: Path, component: str) -> dict:
    """Join audited expectations to real actuals, rejecting incomplete evidence.

    The evidence file supports a human audit. A user-supplied as-of timestamp is
    validated logically, not independently authenticated by this function.
    """
    if component not in SERIES:
        raise ValueError("Component must be headline or core")
    if output.exists():
        raise ValueError("Output directory exists; choose a new directory")
    actuals = rows(actuals_path, ["event_id", "release_utc", "reference_month", "unit", "source", component + "_mom_sa"])
    # Identity columns must be complete throughout the template, but users may
    # leave the unselected component blank without inventing its expectation.
    consensus = rows(consensus_path, CONSENSUS_FIELDS[:4])
    series = SERIES[component]
    by_key = {}
    actual_ids = [row["event_id"] for row in actuals]
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Duplicate actual event_id")
    evidence = {}
    for record in consensus:
        if record["series"] not in SERIES.values():
            raise ValueError("Unsupported consensus series: " + record["series"])
        key = (record["event_id"], record["series"])
        if key in by_key:
            raise ValueError("Duplicate consensus key: " + str(key))
        if record["event_id"] not in actual_ids:
            raise ValueError("Consensus event has no actual release: " + record["event_id"])
        by_key[key] = record
    events = []
    for actual in actuals:
        record = by_key.get((actual["event_id"], series))
        if record is None:
            raise ValueError("Missing consensus: " + actual["event_id"])
        if any(not (record.get(field) or "").strip() for field in CONSENSUS_FIELDS[4:]):
            raise ValueError("Missing consensus value, timestamp, source or evidence: " + actual["event_id"])
        if record["unit"] != "percentage_points" or actual["unit"] != record["unit"]:
            raise ValueError("Consensus and actual units must be percentage_points")
        if record["reference_month"] != actual["reference_month"]:
            raise ValueError("Consensus reference month mismatch")
        if utc(record["consensus_asof_utc"]) >= utc(actual["release_utc"]):
            raise ValueError("Consensus evidence must predate the release")
        value = finite(record["consensus"], "consensus")
        path = (consensus_path.parent / record["evidence_file"]).resolve()
        if not path.is_file():
            raise ValueError("Missing consensus evidence file: " + str(path))
        evidence[str(path)] = sha256(path)
        # Preserve simulator identifiers through the join; the main pipeline
        # will reject them if someone later attempts to label the run supplied.
        source = actual["source"] + " | consensus: " + record["source"]
        events.append({"event_id": actual["event_id"], "series": series, "release_utc": actual["release_utc"],
                       "actual": finite(actual[component + "_mom_sa"], "actual"), "consensus": value,
                       "consensus_asof_utc": record["consensus_asof_utc"], "unit": record["unit"], "source": source})
    output.mkdir(parents=True, exist_ok=False)
    write_table(output / "events.csv", events)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "component": component,
                "inputs": {str(path.resolve()): sha256(path) for path in (actuals_path, consensus_path)},
                "evidence_sha256": evidence, "code_sha256": sha256(Path(__file__)),
                "events_sha256": sha256(output / "events.csv"), "event_count": len(events),
                "limitation": "Evidence existence and timestamps checked; vintage content needs independent human audit"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"events": len(events), "component": component, "output": str(output.resolve())}
