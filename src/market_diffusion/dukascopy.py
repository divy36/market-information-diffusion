"""Public hourly FX tick ingestion; no account, API key or paid S3 access.

The legacy hourly feed uses milliseconds from the named UTC hour. Do not reuse
the provider's daily-file time base here. Raw compressed bytes are preserved.
"""

import csv
import hashlib
import json
import lzma
import math
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data import read_events, utc
from .pipeline import digest


SYMBOLS = ("EURUSD", "GBPUSD")
BASE = "https://www.dukascopy.com/datafeed"
MAX_DOWNLOAD = 8_000_000
MAX_DECODED = 64_000_000


def hour_url(symbol, hour):
    if symbol not in SYMBOLS or hour.utcoffset() != timedelta(0) or any((hour.minute, hour.second, hour.microsecond)):
        raise ValueError("Require a supported FX symbol and an exact UTC hour")
    # Months are zero-based in this provider's URL, unlike Python datetimes.
    return "%s/%s/%04d/%02d/%02d/%02dh_ticks.bi5" % (BASE, symbol, hour.year, hour.month - 1, hour.day, hour.hour)


def decode_hour(compressed, symbol, hour):
    """Decode big-endian (offset_ms, ask, bid, ask_size, bid_size) records.

    Equal-millisecond records keep their final source-file state, with a count.
    Invalid prices or nonmonotone clocks fail loudly instead of being repaired.
    FX sizes are provider quote sizes, not consolidated exchange trading volume.
    """
    hour_url(symbol, hour)  # Validate symbol, timezone and hourly time base.
    decoder = lzma.LZMADecompressor()
    raw = decoder.decompress(compressed, max_length=MAX_DECODED + 1)
    if len(raw) > MAX_DECODED or not decoder.eof or decoder.unused_data:
        raise ValueError("Oversized, truncated or concatenated LZMA stream")
    if not raw or len(raw) % 20:
        raise ValueError("Empty or malformed 20-byte tick records")
    quotes, last_ms, collisions = {}, -1, 0
    for ms, ask, bid, ask_size, bid_size in struct.iter_unpack(">IIIff", raw):
        if not last_ms <= ms < 3_600_000:
            raise ValueError("Offsets must be ordered milliseconds within the named UTC hour")
        if not 0 < bid <= ask or not all(math.isfinite(size) and size >= 0 for size in (ask_size, bid_size)):
            raise ValueError("Invalid bid/ask prices or quote sizes")
        if ms in quotes:
            collisions += 1
        time = hour + timedelta(milliseconds=ms)
        quotes[ms] = {"symbol": symbol, "timestamp_utc": time.isoformat(),
                      "bid": bid / 100000, "ask": ask / 100000,
                      "bid_size": bid_size, "ask_size": ask_size}
        last_ms = ms
    return list(quotes.values()), {"records": len(raw) // 20, "same_millisecond_collisions": collisions,
                                  "unique_timestamps": len(quotes)}


def make_requests(events, pre_seconds=62, post_seconds=1800):
    if type(pre_seconds) is not int or type(post_seconds) is not int or not 0 < pre_seconds <= 3600 or not 0 < post_seconds <= 3600:
        raise ValueError("Pilot pre/post windows must be positive integer seconds up to one hour")
    requests, windows = {}, []
    for event in events:
        start = event.release - timedelta(seconds=pre_seconds)
        end = event.release + timedelta(seconds=post_seconds)
        windows.append((start, end))
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour <= end:
            for symbol in SYMBOLS:
                name = symbol + "-" + hour.strftime("%Y%m%dT%H") + ".bi5"
                requests[name] = {"file": name, "symbol": symbol, "hour_utc": hour.isoformat(), "url": hour_url(symbol, hour)}
            hour += timedelta(hours=1)
    return list(requests.values()), windows


def import_hours(events_path, raw_directory, output, pre_seconds=62, post_seconds=1800):
    """Rebuild the analysis CSV offline from preserved originals, without downloads."""
    if output.exists():
        raise ValueError("Output directory already exists")
    events = read_events(events_path)
    requests, windows = make_requests(events, pre_seconds, post_seconds)
    original = json.loads((raw_directory / "manifest.json").read_text())
    if original.get("status") != "complete" or original.get("events_sha256") != digest(events_path):
        raise ValueError("Raw acquisition must be complete and match the event calendar")
    expected = {row["file"]: row["sha256"] for row in original["files"]}
    output_rows, audit = [], []
    for request in requests:
        path = raw_directory / request["file"]
        if expected.get(request["file"]) != digest(path):
            raise ValueError("Raw file differs from its acquisition hash")
        decoded, counts = decode_hour(path.read_bytes(), request["symbol"], utc(request["hour_utc"]))
        retained = [row for row in decoded if any(start <= utc(row["timestamp_utc"]) <= end for start, end in windows)]
        output_rows.extend(retained)
        audit.append({**request, **counts, "retained_event_window_quotes": len(retained), "sha256": digest(path)})
    output_rows.sort(key=lambda row: (row["symbol"], row["timestamp_utc"]))
    if not output_rows:
        raise ValueError("No observations in requested event windows")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "quotes.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    manifest = {"status": "complete", "provider": "Dukascopy public hourly feed", "symbols": SYMBOLS,
                "events_sha256": digest(events_path), "raw_manifest_sha256": digest(raw_directory / "manifest.json"),
                "quotes_sha256": digest(output / "quotes.csv"), "code_sha256": digest(Path(__file__)),
                "pre_seconds": pre_seconds, "post_seconds": post_seconds, "quotes": len(output_rows), "files": audit,
                "timestamp_convention": "hour named in URL plus millisecond offset; UTC; last source record at equal timestamp",
                "limitations": "One provider feed; not a consolidated FX tape; source timestamps do not establish local receipt time."}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"quotes": len(output_rows), "files": len(audit), "output": str(output.resolve())}


def fetch(events_path, raw_directory, output):
    """Fetch only the bounded hourly files needed by the eight-date pilot.

    Four simultaneous public GETs limit load. Missing hours fail the acquisition;
    they are not assumed to contain zero activity. Partial originals are retained.
    """
    if raw_directory.exists() or output.exists():
        raise ValueError("Raw/output directories must be new")
    raw_path, out_path = raw_directory.resolve(), output.resolve()
    if raw_path == out_path or raw_path in out_path.parents or out_path in raw_path.parents:
        raise ValueError("Raw and converted output directories must be separate")
    requests, _ = make_requests(read_events(events_path))
    raw_directory.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "running", "events_sha256": digest(events_path), "files": [],
                "created_utc": datetime.now(timezone.utc).isoformat(), "errors": []}
    manifest_path = raw_directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    def download(request):
        with urllib.request.urlopen(request["url"], timeout=45) as response:
            content = response.read(MAX_DOWNLOAD + 1)
        if not content or len(content) > MAX_DOWNLOAD:
            raise ValueError("Empty or oversized public download")
        with (raw_directory / request["file"]).open("xb") as stream:
            stream.write(content)
        return {**request, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(download, request): request for request in requests}
        for future in as_completed(pending):
            request = pending[future]
            try:
                manifest["files"].append(future.result())
            except Exception as error:
                manifest["errors"].append({"file": request["file"], "error_type": type(error).__name__})
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            print("Public FX files finished: %d/%d" % (len(manifest["files"]) + len(manifest["errors"]), len(requests)), flush=True)
    manifest["files"].sort(key=lambda row: row["file"])
    manifest["status"] = "failed" if manifest["errors"] else "complete"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    if manifest["errors"]:
        raise ValueError("Some public hours could not be downloaded; inspect the preserved acquisition manifest")
    return import_hours(events_path, raw_directory, output)
