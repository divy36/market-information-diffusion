"""Offline fixtures verify binary order, clocks and raw-file provenance."""

import io
import json
import lzma
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from market_diffusion.data import read_events, read_quotes
from market_diffusion.demo import write_csv
from market_diffusion.dukascopy import decode_hour, fetch, hour_url, import_hours, make_requests


HOUR = datetime(2024, 1, 11, 13, tzinfo=timezone.utc)


def encoded(records):
    return lzma.compress(b"".join(struct.pack(">IIIff", *row) for row in records))


class DecoderTests(unittest.TestCase):
    def test_url_month_is_zero_based_and_clock_is_explicit(self):
        self.assertTrue(hour_url("EURUSD", HOUR).endswith("EURUSD/2024/00/11/13h_ticks.bi5"))
        for symbol, hour in [("USDJPY", HOUR), ("EURUSD", HOUR.replace(tzinfo=None)),
                              ("EURUSD", HOUR + timedelta(minutes=1))]:
            with self.assertRaises(ValueError):
                hour_url(symbol, hour)

    def test_ask_bid_order_scale_and_millisecond_precision(self):
        rows, audit = decode_hour(encoded([(1234, 109841, 109838, 2, 3)]), "EURUSD", HOUR)
        self.assertEqual(rows[0]["ask"], 1.09841)
        self.assertEqual(rows[0]["bid"], 1.09838)
        self.assertEqual(rows[0]["timestamp_utc"], "2024-01-11T13:00:01.234000+00:00")
        self.assertEqual(audit["records"], 1)

    def test_same_timestamp_retains_last_source_state_and_counts(self):
        rows, audit = decode_hour(encoded([(1, 100003, 100001, 1, 1), (1, 100004, 100002, 1, 1)]), "EURUSD", HOUR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bid"], 1.00002)
        self.assertEqual(audit["same_millisecond_collisions"], 1)

    def test_out_of_hour_unsorted_and_invalid_quotes_rejected(self):
        cases = [[(3600000, 100003, 100001, 1, 1)], [(2, 100003, 100001, 1, 1), (1, 100003, 100001, 1, 1)],
                 [(1, 100001, 100003, 1, 1)], [(1, 100003, 0, 1, 1)], [(1, 100003, 100001, float("nan"), 1)]]
        for values in cases:
            with self.assertRaises(ValueError):
                decode_hour(encoded(values), "EURUSD", HOUR)

    def test_malformed_or_truncated_stream_fails(self):
        for content in [lzma.compress(b"x"), lzma.compress(b""), encoded([(1, 100003, 100001, 1, 1)])[:-8]]:
            with self.assertRaises(ValueError):
                decode_hour(content, "EURUSD", HOUR)


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = self.root / "events.csv"
        write_csv(self.events, [{"event_id": "SIM-1", "series": "SIMULATED", "release_utc": (HOUR + timedelta(minutes=30)).isoformat(),
            "actual": .3, "consensus": .2, "consensus_asof_utc": HOUR.isoformat(), "unit": "pp", "source": "SIMULATED"}])

    def test_next_hour_included_for_exact_30_minute_endpoint(self):
        requests, _ = make_requests(read_events(self.events))
        self.assertEqual(len(requests), 4)
        self.assertTrue(any(row["hour_utc"].startswith("2024-01-11T14:") for row in requests))

    def test_raw_preservation_reimport_and_tampering_detection(self):
        data = encoded([(1800000, 109841, 109838, 2, 3)])
        with patch("market_diffusion.dukascopy.urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(data)):
            result = fetch(self.events, self.root / "raw", self.root / "out")
        self.assertEqual(result["quotes"], 2)
        self.assertEqual(set(read_quotes(self.root / "out/quotes.csv")), {"EURUSD", "GBPUSD"})
        import_hours(self.events, self.root / "raw", self.root / "replay")
        self.assertEqual((self.root / "out/quotes.csv").read_bytes(), (self.root / "replay/quotes.csv").read_bytes())
        first = next((self.root / "raw").glob("*.bi5"))
        first.write_bytes(data + b"changed")
        with self.assertRaisesRegex(ValueError, "hash"):
            import_hours(self.events, self.root / "raw", self.root / "bad")

    def test_missing_download_is_failure_not_zero_activity(self):
        with patch("market_diffusion.dukascopy.urllib.request.urlopen", side_effect=OSError("fixture failure")):
            with self.assertRaisesRegex(ValueError, "could not be downloaded"):
                fetch(self.events, self.root / "raw", self.root / "out")
        manifest = json.loads((self.root / "raw/manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(len(manifest["errors"]), 4)
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
