"""Databento historical API workflow: plan -> estimate -> explicit download.

The optional official SDK handles authentication and DBN decoding. Core research
and offline tests remain dependency-free. No key is saved in plans or manifests.
This connector has offline contract tests; live access still requires an account.
"""

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bls import sha256, write_table
from .data import finite, rows, utc

DATASET = "GLBX.MDP3"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def client_from_environment():
    """Read a locally configured key; never ask callers to put it in source code."""
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise ValueError("Set DATABENTO_API_KEY locally; do not paste the key into chat or commit it")
    try:
        import databento
    except ImportError:
        raise ValueError("The optional databento SDK is not installed; see docs/api-access.md")
    return databento.Historical(key)


def make_plan(actuals: Path, output: Path) -> dict:
    """Limit requests to CPI windows, one product per request, preserving identity.

    Volume-ranked continuous symbols use the provider's previous-day roll rule.
    The import checks that each event/product window contains one instrument ID.
    """
    if output.exists():
        raise ValueError("Plan already exists")
    events = rows(actuals, ["event_id", "release_utc"])
    requests = []
    ids = set()
    for event in events:
        event_id = event["event_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", event_id) or event_id in ids:
            raise ValueError("Unsafe or duplicate event_id")
        ids.add(event_id)
        release = utc(event["release_utc"])
        for root in ("ES", "ZN"):
            requests.append({"id": event_id + "-" + root, "event_id": event_id, "root": root,
                             "dataset": DATASET, "schema": "mbp-1", "stype_in": "continuous",
                             "symbols": [root + ".v.0"],
                             "start": (release - timedelta(seconds=62)).isoformat(),
                             "end": (release + timedelta(seconds=1801)).isoformat()})
    plan = {"provider": "Databento", "status": "planned_not_downloaded", "actuals_sha256": sha256(actuals),
            "roll_rule": "previous_day_volume_rank_zero", "requests": requests}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    return {"requests": len(requests), "plan": str(output.resolve())}


def read_plan(path: Path) -> list:
    requests = json.loads(path.read_text())["requests"]
    if not requests:
        raise ValueError("Empty request plan")
    seen = set()
    for request in requests:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", request["id"]) or request["id"] in seen:
            raise ValueError("Unsafe or duplicate request ID")
        seen.add(request["id"])
        if request["root"] not in ("ES", "ZN") or request["symbols"] != [request["root"] + ".v.0"]:
            raise ValueError("Only the configured ES/ZN volume-ranked symbols are supported")
        if (request["dataset"], request["schema"], request["stype_in"]) != (DATASET, "mbp-1", "continuous"):
            raise ValueError("Unsupported dataset/schema/symbology")
        duration = (utc(request["end"]) - utc(request["start"])).total_seconds()
        if not 0 < duration <= 3600:
            raise ValueError("Each pilot request must cover at most one hour")
    return requests


def parameters(request: dict) -> dict:
    return {key: request[key] for key in ("dataset", "schema", "stype_in", "symbols", "start", "end")}


def estimate(plan_path: Path, client=None) -> dict:
    """Query metadata costs only; this function never requests price records."""
    requests = read_plan(plan_path)
    client = client if client is not None else client_from_environment()
    quotes = []
    for request in requests:
        try:
            cost = float(client.metadata.get_cost(**parameters(request)))
        except Exception:
            raise ValueError("Databento cost query failed; check local credentials, entitlements and connectivity") from None
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("Provider returned an invalid cost estimate")
        quotes.append({"request_id": request["id"], "estimated_usd": cost})
    return {"plan_sha256": sha256(plan_path), "created_utc": datetime.now(timezone.utc).isoformat(),
            "estimated_usd": sum(quote["estimated_usd"] for quote in quotes), "requests": quotes,
            "warning": "Provider estimate, not a guaranteed billing cap; credits and entitlements require account verification"}


def ceil_microsecond(nanoseconds: int) -> datetime:
    """Round availability UP, so sub-microsecond future quotes stay in the future."""
    if nanoseconds < 0 or nanoseconds >= 2 ** 63:
        raise ValueError("Invalid nanosecond timestamp")
    return EPOCH + timedelta(microseconds=(nanoseconds + 999) // 1000)


def convert_csv(path: Path, root: str, start: str, end: str) -> tuple:
    """Read SDK CSV exported with pretty_ts=False and pretty_px=True.

    Reject bad books/clocks and contract changes rather than carrying an earlier
    valid quote through an invalid state. Keep only completed exchange events.
    Several records can round to one microsecond: retain the last receive-time
    record, with file order breaking exact ties, and record the collision count.
    """
    if root not in ("ES", "ZN"):
        raise ValueError("Unsupported product")
    lower, upper = utc(start), utc(end)
    def epoch_ns(value):
        delta = value - EPOCH
        return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000
    lower_ns, upper_ns = epoch_ns(lower), epoch_ns(upper)
    required = ["ts_recv", "rtype", "instrument_id", "bid_px_00", "ask_px_00", "bid_sz_00", "ask_sz_00", "flags"]
    result, ids = {}, set()
    last_ns, count, nonfinal, collisions = -1, 0, 0, 0
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(required) <= set(reader.fieldnames or []):
            raise ValueError("Unexpected Databento CSV columns")
        for row in reader:
            count += 1
            ns = int(row["ts_recv"])
            stamp = ceil_microsecond(ns)
            if ns < last_ns:
                raise ValueError("Receive timestamps are out of order")
            last_ns = ns
            if not lower_ns <= ns < upper_ns:
                raise ValueError("Quote falls outside its request window")
            if int(row["rtype"]) != 1:
                raise ValueError("Expected MBP-1 record type")
            ids.add(int(row["instrument_id"]))
            if len(ids) > 1:
                raise ValueError("Contract changed inside event window")
            flags = int(row["flags"])
            if not 0 <= flags <= 255 or flags & (8 | 4 | 2):
                raise ValueError("Bad clock/book or unsupported publisher-specific flag; inspect original data")
            if not flags & 128:
                nonfinal += 1
                continue
            bid, ask = finite(row["bid_px_00"], "bid"), finite(row["ask_px_00"], "ask")
            if not 0 < bid <= ask or min(int(row["bid_sz_00"]), int(row["ask_sz_00"])) <= 0:
                raise ValueError("Invalid or empty two-sided book; inspect original data")
            timestamp = stamp.isoformat()
            collisions += timestamp in result
            result[timestamp] = {"symbol": root, "timestamp_utc": timestamp, "bid": bid, "ask": ask,
                                 "instrument_id": row["instrument_id"], "ts_recv_ns": str(ns)}
    if not result:
        raise ValueError("No usable completed-event quotes returned")
    audit = {"source_rows": count, "output_rows": len(result), "nonfinal_rows": nonfinal,
             "microsecond_collisions": collisions, "instrument_ids": sorted(ids)}
    return list(result.values()), audit


def download(plan_path: Path, raw: Path, output: Path, max_estimated_usd: float, client=None) -> dict:
    """Download only after a fresh estimate passes the caller's explicit ceiling.

    This is an ESTIMATE ceiling, not a provider-enforced billing guarantee. The
    application does not retry billable requests automatically after failures.
    """
    if raw.exists() or output.exists():
        raise ValueError("Use new raw and output directories; partial runs are preserved")
    if raw.resolve() == output.resolve():
        raise ValueError("Raw and derived outputs need distinct directories")
    if not math.isfinite(max_estimated_usd) or max_estimated_usd < 0:
        raise ValueError("A finite nonnegative estimate ceiling is required")
    requests = read_plan(plan_path)
    client = client if client is not None else client_from_environment()
    costs = estimate(plan_path, client)
    if costs["estimated_usd"] > max_estimated_usd:
        raise ValueError("Estimated download cost exceeds the provided ceiling; no prices requested")
    raw.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    (output / "estimate.json").write_text(json.dumps(costs, indent=2) + "\n")
    audit = {"status": "in_progress", "requests": [], "plan_sha256": sha256(plan_path),
             "code_sha256": sha256(Path(__file__)), "created_utc": datetime.now(timezone.utc).isoformat()}
    quotes = []
    try:
        for request in requests:
            binary = raw / (request["id"] + ".dbn.zst")
            csv_path = output / (request["id"] + ".csv")
            data = client.timeseries.get_range(**parameters(request), path=binary)
            data.to_csv(csv_path, pretty_ts=False, pretty_px=True, map_symbols=True, mode="x")
            converted, stats = convert_csv(csv_path, request["root"], request["start"], request["end"])
            quotes.extend(converted)
            audit["requests"].append({"request": request, "raw_sha256": sha256(binary),
                                      "csv_sha256": sha256(csv_path), "conversion": stats})
            (output / "manifest.json").write_text(json.dumps(audit, indent=2) + "\n")
        quotes.sort(key=lambda row: (row["symbol"], row["timestamp_utc"]))
        keys = [(row["symbol"], row["timestamp_utc"]) for row in quotes]
        if len(keys) != len(set(keys)):
            raise ValueError("Request windows overlap; duplicate output quotes")
        write_table(output / "quotes.csv", quotes)
        audit["status"] = "complete"
        audit["quotes_sha256"] = sha256(output / "quotes.csv")
        audit["quote_rows"] = len(quotes)
    except Exception as error:
        # Log only the exception type, never SDK error text that could contain a
        # credential or request header. Partial downloads remain for inspection.
        audit["status"] = "failed"
        audit["error_type"] = type(error).__name__
        raise ValueError("Download/import failed; original files and manifest preserved. No automatic retry.") from None
    finally:
        (output / "manifest.json").write_text(json.dumps(audit, indent=2) + "\n")
    return {"status": "complete", "quote_rows": len(quotes), "output": str(output.resolve())}
