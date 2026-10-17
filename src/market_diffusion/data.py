"""Strict CSV contracts and surprise estimates using only earlier releases.

Times must include an offset. Requiring this at the boundary prevents an ET
release from silently being matched to a UTC quote at the wrong hour.
"""

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import stdev
from typing import Dict, List, Optional


def utc(value: str) -> datetime:
    """Parse an ISO-8601 instant; reject ambiguous local wall-clock times."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp needs an explicit UTC offset: " + value)
    return result.astimezone(timezone.utc)


def finite(value: str, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(field + " must be finite")
    return result


@dataclass(frozen=True)
class Event:
    event_id: str
    series: str
    release: datetime
    actual: float
    consensus: float
    consensus_asof: datetime
    unit: str
    source: str

    @property
    def surprise(self) -> float:
        """Actual minus the expectation recorded before the announcement."""
        return self.actual - self.consensus


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


def rows(path: Path, required: List[str]) -> List[dict]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError("Missing columns: " + ", ".join(sorted(missing)))
        result = list(reader)
    if not result:
        raise ValueError("CSV contains no records: " + str(path))
    for index, row in enumerate(result, 2):
        if any(row.get(key) is None or not row[key].strip() for key in required):
            raise ValueError("Missing value at CSV line " + str(index))
    return result


def read_events(path: Path) -> List[Event]:
    records = rows(path, ["event_id", "series", "release_utc", "actual",
                          "consensus", "consensus_asof_utc", "unit", "source"])
    events, ids, units = [], set(), {}
    for row in records:
        event = Event(row["event_id"], row["series"], utc(row["release_utc"]),
                      finite(row["actual"], "actual"), finite(row["consensus"], "consensus"),
                      utc(row["consensus_asof_utc"]), row["unit"], row["source"])
        if event.event_id in ids:
            raise ValueError("Duplicate event_id: " + event.event_id)
        if event.consensus_asof >= event.release:
            raise ValueError("Consensus must be recorded before release: " + event.event_id)
        if event.series in units and units[event.series] != event.unit:
            raise ValueError("A series cannot mix units: " + event.series)
        ids.add(event.event_id)
        units[event.series] = event.unit
        events.append(event)
    return sorted(events, key=lambda event: (event.release, event.event_id))


def read_quotes(path: Path) -> Dict[str, List[Quote]]:
    grouped, keys = {}, set()
    for row in rows(path, ["symbol", "timestamp_utc", "bid", "ask"]):
        quote = Quote(row["symbol"], utc(row["timestamp_utc"]),
                      finite(row["bid"], "bid"), finite(row["ask"], "ask"))
        if not 0 < quote.bid <= quote.ask:
            raise ValueError("Require 0 < bid <= ask for " + quote.symbol)
        key = (quote.symbol, quote.timestamp)
        if key in keys:
            raise ValueError("Duplicate symbol/timestamp: " + str(key))
        keys.add(key)
        grouped.setdefault(quote.symbol, []).append(quote)
    return {symbol: sorted(values, key=lambda q: q.timestamp)
            for symbol, values in grouped.items()}


def standardized_surprises(events: List[Event], min_history: int = 6) -> dict:
    """Scale surprise by the sample SD of strictly earlier same-series surprises.

    This is U_e / sigma_past, not a full-sample z-score. Equal-time releases
    cannot inform each other. Warm-up or zero-variance histories return None.
    """
    if min_history < 2:
        raise ValueError("min_history must be at least 2")
    output = {}
    for event in events:
        past = [other.surprise for other in events
                if other.series == event.series and other.release < event.release]
        scale = stdev(past) if len(past) >= min_history else None
        output[event.event_id] = {
            "surprise": event.surprise, "prior_count": len(past),
            "prior_sd": scale,
            "standardized_surprise": event.surprise / scale if scale else None,
        }
    return output
