"""Check chronology, freeze integrity and prior-history preservation."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from market_diffusion.alignment import Config
from market_diffusion.demo import generate
from market_diffusion.holdout import evaluate, freeze, partition
from test_research import event


class SplitTests(unittest.TestCase):
    def test_equal_time_releases_stay_together(self):
        first, second = event(0), event(1)
        simultaneous = replace(second, event_id="same")
        result = partition([first, second, simultaneous], second.release, Config())
        self.assertEqual(result, {"development": ["0"], "holdout": ["1", "same"]})

    def test_cross_boundary_overlap_rejected(self):
        first = event(0)
        second = replace(event(1), release=first.release + timedelta(seconds=360))
        with self.assertRaisesRegex(ValueError, "overlap"):
            partition([first, second], second.release, Config())

    def test_empty_partition_rejected(self):
        with self.assertRaisesRegex(ValueError, "both"):
            partition([event(0)], event(0).release, Config())


class FrozenEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        generate(cls.root / "inputs", seed=73)
        cls.events = cls.root / "inputs/events.csv"
        cls.quotes = cls.root / "inputs/quotes.csv"
        cls.config = cls.root / "config.json"
        cls.config.write_text(json.dumps({"bootstrap_samples": 100}))
        cls.protocol = cls.root / "protocol.json"
        cls.frozen = freeze(cls.events, cls.config, "2021-01-01T00:00:00Z", cls.protocol, "simulation")
        cls.dev = evaluate(cls.events, cls.quotes, cls.protocol, "development", cls.root / "development")
        cls.held = evaluate(cls.events, cls.quotes, cls.protocol, "holdout", cls.root / "holdout", cls.root / "development")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_disjoint_chronological_runs_retain_prior_history(self):
        self.assertEqual(len(self.dev["selected_event_ids"]), 12)
        self.assertEqual(len(self.held["selected_event_ids"]), 12)
        self.assertFalse(set(self.dev["selected_event_ids"]) & set(self.held["selected_event_ids"]))
        results = json.loads((self.root / "holdout/analysis.json").read_text())["results"]
        first = next(row for row in results if row["event_id"] == "SIM-012")
        self.assertEqual(first["prior_count"], 12)
        self.assertIsNotNone(first["standardized_surprise"])
        self.assertTrue(all(row["event_id"] in self.held["selected_event_ids"] for row in results))

    def test_modified_events_and_code_rejected(self):
        changed = self.root / "changed.csv"
        changed.write_text(self.events.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "calendar"):
            evaluate(changed, self.quotes, self.protocol, "development", self.root / "bad-events")
        with patch("market_diffusion.holdout.code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "Code differs"):
                evaluate(self.events, self.quotes, self.protocol, "development", self.root / "bad-code")

    def test_holdout_requires_matching_development_run(self):
        with self.assertRaisesRegex(ValueError, "completed development"):
            evaluate(self.events, self.quotes, self.protocol, "holdout", self.root / "missing-dev")
        changed = self.root / "changed-quotes.csv"
        changed.write_text(self.quotes.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "same protocol"):
            evaluate(self.events, changed, self.protocol, "holdout", self.root / "bad-quotes", self.root / "development")

    def test_partition_tampering_and_protocol_revision_detected(self):
        changed = json.loads(self.protocol.read_text())
        changed["partitions"]["holdout"] = []
        path = self.root / "tampered.json"
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "membership"):
            evaluate(self.events, self.quotes, path, "development", self.root / "bad-membership")
        changed = json.loads(self.protocol.read_text())
        changed["config"]["endpoint_seconds"] = 30
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "same protocol"):
            evaluate(self.events, self.quotes, path, "holdout", self.root / "changed-plan", self.root / "development")

    def test_freeze_and_run_cannot_overwrite_or_relabel(self):
        with self.assertRaises(FileExistsError):
            freeze(self.events, self.config, "2021-01-01T00:00:00Z", self.protocol, "simulation")
        with self.assertRaisesRegex(ValueError, "Simulation-marked"):
            freeze(self.events, self.config, "2021-01-01T00:00:00Z", self.root / "wrong-mode.json", "supplied")
        with self.assertRaisesRegex(ValueError, "already exists"):
            evaluate(self.events, self.quotes, self.protocol, "development", self.root / "development")


if __name__ == "__main__":
    unittest.main()
