"""Recover reported market consensus from pre-release Scotiabank calendars.

The Consensus column is distinct from the bank's BNS forecast. PDFs are retrieved
later and their printed publication stamps are evidence, not archival proof that
the files were never amended. Missing and ambiguous cases stay in an audit table.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .bls import SERIES, sha256, write_table
from .data import rows, utc

BASE = "https://www.scotiabank.com/content/dam/scotiabank/sub-brands/scotiabank-economics/english/documents/daily-points/"


def parse_calendar(text: str, actual: dict) -> tuple:
    """Extract two MoM forecasts and conservatively bound publication time.

    Some summer PDFs say EST even though Eastern civil time is EDT. Rather than
    silently correcting that label, evaluate both interpretations and use the
    later bound. If that bound is not pre-release, withhold the observations.
    """
    stamp = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})\s*@\s*(\d{1,2}:\d{2})\s+(EST|EDT)", text)
    if not stamp:
        raise ValueError("Missing printed publication timestamp")
    date, clock, label = stamp.groups()
    local = datetime.strptime(date + " " + clock, "%B %d, %Y %H:%M")
    release = utc(actual["release_utc"])
    if local.date() != release.astimezone(ZoneInfo("America/New_York")).date():
        raise ValueError("Report date does not match release date")
    literal = local.replace(tzinfo=timezone(timedelta(hours=-5 if label == "EST" else -4))).astimezone(timezone.utc)
    civil = local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    upper = max(literal, civil)
    status = "usable_with_printed_timestamp_evidence" if upper < release else "withheld_ambiguous_or_late_timestamp"
    if not re.search(r"Period\s+BNS\s+Consensus\s+Latest", text):
        raise ValueError("Unexpected calendar column order")
    reference = datetime.strptime(actual["reference_month"], "%Y-%m").strftime("%b")
    forecasts = {}
    for component, indicator in [("headline", r"CPI \(m/m\)"), ("core", r"CPI ex\. Food & Energy \(m/m\)")]:
        pattern = (r"US\s+(\d{2}[-/]\d{2})\s+(\d{2}:\d{2})\s+" + indicator
                   + r"\s+([A-Za-z]{3})\s+(--|-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(--|-?\d+(?:\.\d+)?)")
        found = re.findall(pattern, text)
        if len(found) != 1:
            raise ValueError("Missing/ambiguous monthly consensus row: " + component)
        event_date, event_time, month, bank, consensus, latest = found[0]
        local_release = release.astimezone(ZoneInfo("America/New_York"))
        if event_date.replace("/", "-") != local_release.strftime("%m-%d") or event_time != local_release.strftime("%H:%M"):
            raise ValueError("Calendar event clock/date mismatch")
        if month != reference:
            raise ValueError("Consensus reference month mismatch")
        forecasts[component] = float(consensus)
    audit = {"event_id": actual["event_id"], "status": status, "printed_timestamp": stamp.group(0),
             "literal_label_utc": literal.isoformat(), "eastern_civil_utc": civil.isoformat(),
             "availability_upper_bound_utc": upper.isoformat(),
             "timezone_ambiguity": literal != civil, **forecasts}
    return forecasts, audit


def import_consensus(actuals_path: Path, pdf_directory: Path, output: Path) -> dict:
    """Read local originals only; network retrieval is a separate recorded step."""
    if output.exists():
        raise ValueError("Output exists; choose a new consensus import directory")
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("Optional pdfplumber dependency required for PDF import")
    actuals = rows(actuals_path, ["event_id", "release_utc", "reference_month", "unit", "source"])
    consensus, covered, audit = [], [], []
    for actual in actuals:
        filename = "dailypoints" + actual["release_utc"][:10].replace("-", "") + ".pdf"
        pdf = pdf_directory / filename
        if not pdf.exists():
            historical = "dailypoints_" + actual["release_utc"][:10] + ".pdf"
            if (pdf_directory / historical).exists():
                filename, pdf = historical, pdf_directory / historical
        if not pdf.exists():
            audit.append({"event_id": actual["event_id"], "status": "missing_pdf", "expected_url": BASE + filename})
            continue
        with pdfplumber.open(pdf) as document:
            text = document.pages[0].extract_text()
        try:
            forecasts, record = parse_calendar(text or "", actual)
        except ValueError as error:
            audit.append({"event_id": actual["event_id"], "status": "withheld_unparsed_pdf",
                          "reason": str(error), "source_url": BASE + filename,
                          "source_sha256": sha256(pdf), "page": 1})
            continue
        record.update({"source_url": BASE + filename, "source_sha256": sha256(pdf), "page": 1})
        audit.append(record)
        if not record["status"].startswith("usable"):
            continue
        covered.append(actual)
        for component, value in forecasts.items():
            consensus.append({"event_id": actual["event_id"], "series": SERIES[component],
                              "reference_month": actual["reference_month"], "unit": "percentage_points",
                              "consensus": value, "consensus_asof_utc": record["availability_upper_bound_utc"],
                              "source": BASE + filename + " page 1 Consensus column; asof is conservative printed-time upper bound",
                              "evidence_file": str(pdf.resolve())})
    if not covered:
        raise ValueError("No consensus reports passed the timestamp checks")
    output.mkdir(parents=True, exist_ok=False)
    write_table(output / "consensus.csv", consensus)
    write_table(output / "actuals_covered.csv", covered)
    manifest = {"actuals_sha256": sha256(actuals_path), "code_sha256": sha256(Path(__file__)),
                "created_utc": datetime.now(timezone.utc).isoformat(), "events_considered": len(actuals),
                "events_covered": len(covered), "consensus_rows": len(consensus), "audit": audit,
                "limitation": "Printed pre-release timestamp evidence, not independently verified historical file capture"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lines = ["# Consensus evidence audit", "", "| Event | Status | Headline | Core | Printed time |", "|---|---|---:|---:|---|"]
    for item in audit:
        lines.append("| {event_id} | {status} | {headline} | {core} | {printed_timestamp} |".format(
            **{**dict(headline="", core="", printed_timestamp=""), **item}))
    lines += ["", "When EST is printed during daylight-saving time, both fixed EST and Eastern civil time are considered. The later bound must still predate the release.",
              "", "Only covered events are exported for the existing strict consensus join. This selection is based on source availability before any market-response inspection; it may still create sample bias."]
    (output / "readiness.md").write_text("\n".join(lines) + "\n")
    return {"events_covered": len(covered), "events_considered": len(actuals), "output": str(output.resolve())}
