"""Ensure plans or API-key presence are never mistaken for observed quote coverage."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from market_diffusion.alignment import Config
from market_diffusion.demo import write_csv
from market_diffusion.readiness import check_readiness


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events, self.actuals, self.plan = (self.root / name for name in ("events.csv", "actuals.csv", "plan.json"))
        self.release = datetime(2024, 1, 1, 13, 30, tzinfo=timezone.utc)
        self.rows, self.requests = [], []
        for index in range(8):
            time = self.release + timedelta(days=30 * index)
            event_id = "SIM-%d" % index
            self.rows.append({"event_id": event_id, "release_utc": time.isoformat(), "series": "SIMULATED",
                "actual": index % 3 / 10, "consensus": .1, "consensus_asof_utc": (time - timedelta(hours=1)).isoformat(),
                "unit": "pp", "source": "SIMULATED"})
            for symbol in ("ES", "ZN"):
                self.requests.append({"id": event_id + "-" + symbol, "event_id": event_id, "root": symbol,
                    "dataset": "GLBX.MDP3", "schema": "mbp-1", "stype_in": "continuous", "symbols": [symbol + ".v.0"],
                    "start": (time - timedelta(seconds=62)).isoformat(), "end": (time + timedelta(seconds=301)).isoformat()})
        write_csv(self.events, self.rows)
        write_csv(self.actuals, [{"event_id": row["event_id"], "release_utc": row["release_utc"]} for row in self.rows])
        self.plan.write_text(json.dumps({"requests": self.requests}))

    def run_check(self, quotes=None):
        check_readiness(self.events, self.actuals, self.plan, self.root / "output", Config(), quotes)
        return json.loads((self.root / "output/readiness.json").read_text())

    def test_full_plan_and_key_do_not_substitute_for_quotes(self):
        with patch.dict("os.environ", {"DATABENTO_API_KEY": "synthetic-test-not-a-real-key"}):
            result = self.run_check()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["api_key_present"])
        self.assertFalse(result["quotes_supplied"])
        self.assertEqual(result["standardized_surprise_available"], 2)
        self.assertNotIn("synthetic-test-not-a-real-key", (self.root / "output/readiness.json").read_text())

    def test_short_plan_window_is_flagged(self):
        self.requests[0]["end"] = (self.release + timedelta(seconds=300)).isoformat()
        self.plan.write_text(json.dumps({"requests": self.requests}))
        result = self.run_check()
        self.assertFalse(result["event_windows"][0]["plan_window_valid"])

    def test_missing_and_duplicate_market_requests_are_flagged(self):
        self.requests = self.requests[1:]
        duplicate = dict(self.requests[0], id="duplicate")
        self.plan.write_text(json.dumps({"requests": self.requests + [duplicate]}))
        result = self.run_check()
        self.assertFalse(result["event_windows"][0]["plan_window_valid"])
        self.assertFalse(result["event_windows"][1]["plan_window_valid"])

    def test_sparse_local_quotes_do_not_pass(self):
        path = self.root / "quotes.csv"
        write_csv(path, [{"symbol": "ES", "timestamp_utc": self.release.isoformat(), "bid": 100, "ask": 101}])
        result = self.run_check(path)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(any(row["coverage_pass"] for row in result["event_windows"]))

    def test_full_coverage_means_ready_for_checks_not_empirical_success(self):
        quotes = []
        for index in range(8):
            for symbol in ("ES", "ZN"):
                for tau in range(-60, 301):
                    quotes.append({"symbol": symbol, "timestamp_utc": (self.release + timedelta(days=30 * index, seconds=tau)).isoformat(),
                                   "bid": 100, "ask": 101})
        path = self.root / "quotes.csv"
        write_csv(path, quotes)
        result = self.run_check(path)
        # Constant synthetic prices cover the grid but would fail the move screen.
        self.assertEqual(result["status"], "ready_for_quality_analysis")
        self.assertIn("not source authentication", result["limits"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_check(path)

    def test_calendar_timestamp_mismatch_rejected(self):
        self.rows[0]["release_utc"] = (self.release + timedelta(seconds=1)).isoformat()
        write_csv(self.events, self.rows)
        with self.assertRaisesRegex(ValueError, "calendar"):
            self.run_check()


if __name__ == "__main__":
    unittest.main()
