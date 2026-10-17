"""Guard against sample-selection artifacts and false robustness claims."""

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_diffusion.demo import generate, write_csv
from market_diffusion.sensitivity import compare, read_plan, run_sensitivity


def payload(values):
    return {"config": {"bootstrap_samples": 100, "seed": 42}, "results": [
        {"event_id": key, "symbol": "X", "status": "accepted" if value is not None else "excluded",
         "fit": {"t50_seconds": value} if value is not None else None, "flags": []}
        for key, value in values.items()]}


class CohortTests(unittest.TestCase):
    def test_selection_change_is_not_reported_as_timing_change(self):
        result = compare({"base": payload({"a": 1, "b": 100}),
                          "alt": payload({"a": 1, "b": None})}, "base")["X"]
        self.assertEqual(result["common_event_ids"], ["a"])
        self.assertEqual(result["scenarios"]["base"]["all_accepted_t50"]["median"], 50.5)
        self.assertEqual(result["scenarios"]["alt"]["common_delta_vs_baseline"]["median"], 0)

    def test_deltas_are_paired_before_aggregation(self):
        result = compare({"base": payload({"a": 1, "b": 10, "c": 100}),
                          "alt": payload({"a": 6, "b": 30, "c": 101})}, "base")["X"]
        delta = result["scenarios"]["alt"]["pairwise_delta_vs_baseline"]
        self.assertEqual(delta["median"], 5)  # median(5,20,1), not 30-10.
        self.assertEqual(delta["n"], 3)

    def test_empty_global_cohort_does_not_erase_pairwise_evidence(self):
        result = compare({"base": payload({"a": 1, "b": 10}),
                          "alt": payload({"a": 2, "b": None}),
                          "third": payload({"a": None, "b": 12})}, "base")["X"]
        self.assertEqual(result["common_event_ids"], [])
        self.assertIsNone(result["scenarios"]["alt"]["common_t50"]["median"])
        self.assertEqual(result["scenarios"]["alt"]["pairwise_delta_vs_baseline"]["median"], 1)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = self.root / "plan.json"
        self.spec = {"baseline": "base", "base_config": {"bootstrap_samples": 100}, "scenarios": [
            {"name": "base", "overrides": {}}, {"name": "long", "overrides": {"post_seconds": 900}}]}
        self.plan.write_text(json.dumps(self.spec))

    def fixtures(self):
        release = datetime(2020, 1, 1, 13, 30, tzinfo=timezone.utc)
        write_csv(self.root / "events.csv", [{"event_id": "SIM-1", "series": "SIMULATED_CPI",
            "release_utc": release.isoformat(), "actual": .3, "consensus": .2,
            "consensus_asof_utc": (release - timedelta(hours=1)).isoformat(), "unit": "pp", "source": "SIMULATED"}])
        quotes = []
        for tau in range(-65, 301):
            fraction = 0 if tau < 0 else 1 - .8 * math.exp(-tau / 20)
            mid = 100 * math.exp(20 * fraction / 10000)
            quotes.append({"symbol": "X", "timestamp_utc": (release + timedelta(seconds=tau)).isoformat(),
                           "bid": mid - .0001, "ask": mid + .0001})
        write_csv(self.root / "quotes.csv", quotes)

    def test_invalid_plans_rejected_before_running(self):
        for name in ["../escape", "base", "Upper", "", "a/b"]:
            self.spec["scenarios"][1]["name"] = name
            self.plan.write_text(json.dumps(self.spec))
            with self.assertRaises(ValueError):
                read_plan(self.plan)
        self.spec["scenarios"][1] = {"name": "long", "overrides": {"step_seconds": 0}}
        self.plan.write_text(json.dumps(self.spec))
        with self.assertRaises(ValueError):
            read_plan(self.plan)

    def test_missing_long_horizon_is_excluded_and_audited(self):
        self.fixtures()
        result = run_sensitivity(self.root / "events.csv", self.root / "quotes.csv", self.plan,
                                 self.root / "output", "simulation")
        rows = result["symbols"]["X"]["scenarios"]
        self.assertEqual(rows["base"]["counts"]["accepted"], 1)
        self.assertEqual(rows["long"]["counts"]["excluded"], 1)
        self.assertIsNone(rows["long"]["common_t50"]["median"])
        manifest = json.loads((self.root / "output/manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(len(manifest["inputs"]), 3)
        self.assertEqual(manifest["resolved_configs"]["long"]["post_seconds"], 900)
        self.assertIn("SIMULATION ONLY", (self.root / "output/report.md").read_text())
        with self.assertRaisesRegex(ValueError, "already exists"):
            run_sensitivity(self.root / "events.csv", self.root / "quotes.csv", self.plan,
                            self.root / "output", "simulation")

    def test_simulation_cannot_be_relabelled_and_failure_is_recorded(self):
        self.fixtures()
        with self.assertRaisesRegex(ValueError, "Simulation-marked"):
            run_sensitivity(self.root / "events.csv", self.root / "quotes.csv", self.plan,
                            self.root / "output", "supplied")
        manifest = json.loads((self.root / "output/manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse((self.root / "output/summary.json").exists())

    def test_long_fixture_is_explicit_and_invalid_length_does_not_write(self):
        with self.assertRaises(ValueError):
            generate(self.root / "invalid", post_seconds=299)
        self.assertFalse((self.root / "invalid").exists())
        generate(self.root / "long", post_seconds=1800)
        with (self.root / "long/quotes.csv").open() as stream:
            self.assertEqual(sum(1 for _ in stream), 1 + 24 * 2 * 1866)
        self.assertIn("SIMULATED", (self.root / "long/README.txt").read_text())


if __name__ == "__main__":
    unittest.main()
