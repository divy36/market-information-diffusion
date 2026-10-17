"""Tests target research failure modes, not just function execution."""

import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_diffusion.alignment import Config, Observation, QuoteIndex
from market_diffusion.analysis import analyze
from market_diffusion.data import Event, Quote, read_events, read_quotes, standardized_surprises, utc
from market_diffusion.demo import generate, write_csv
from market_diffusion.models import bootstrap_median, fit_response, response, time_to_fraction
from market_diffusion.pipeline import run


T0 = datetime(2020, 1, 1, 13, 30, tzinfo=timezone.utc)


def event(index=0, surprise=0.1):
    release = T0 + timedelta(days=index)
    return Event(str(index), "CPI_MOM", release, surprise, 0.0,
                 release - timedelta(hours=1), "percentage_points", "test fixture")


class DataTests(unittest.TestCase):
    def test_naive_time_rejected_and_offsets_normalized(self):
        with self.assertRaises(ValueError):
            utc("2020-01-01T08:30:00")
        self.assertEqual(utc("2020-01-01T08:30:00-05:00"), T0)
        self.assertEqual(utc("2020-07-01T08:30:00-04:00").hour, 12)

    def test_surprise_scaling_cannot_see_future_or_equal_time_events(self):
        events = [event(0, 1), event(1, 3), event(2, 2)]
        expected = 2 / math.sqrt(2)
        before = standardized_surprises(events, 2)
        extra = replace(event(2, 999), event_id="same-time")
        after = standardized_surprises(events + [extra, event(3, 10000)], 2)
        self.assertAlmostEqual(before["2"]["standardized_surprise"], expected)
        self.assertEqual(before["2"], after["2"])
        self.assertIsNone(before["1"]["standardized_surprise"])

    def test_zero_variance_history_is_missing_not_infinity(self):
        result = standardized_surprises([event(0, 1), event(1, 1), event(2, 2)], 2)
        self.assertIsNone(result["2"]["standardized_surprise"])

    def test_late_consensus_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            write_csv(path, [{"event_id": "x", "series": "CPI", "release_utc": T0.isoformat(),
                              "actual": 1, "consensus": 0, "consensus_asof_utc": T0.isoformat(),
                              "unit": "pp", "source": "test"}])
            with self.assertRaisesRegex(ValueError, "before release"):
                read_events(path)

    def test_crossed_nonfinite_and_duplicate_quotes_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.csv"
            good = {"symbol": "X", "timestamp_utc": T0.isoformat(), "bid": 100, "ask": 101}
            for records in [[dict(good, ask=99)], [dict(good, bid="nan")], [good, good]]:
                write_csv(path, records)
                with self.assertRaises(ValueError):
                    read_quotes(path)


class AlignmentTests(unittest.TestCase):
    def test_no_future_quote_and_stale_quote_not_carried_forever(self):
        quotes = [Quote("X", T0 - timedelta(seconds=1), 99, 101),
                  Quote("X", T0 + timedelta(milliseconds=500), 199, 201)]
        aligned = {row.tau: row for row in QuoteIndex(quotes).align(event(), Config())}
        self.assertEqual(aligned[0].mid, 100)
        self.assertEqual(aligned[1].mid, 200)
        self.assertIsNone(aligned[3].mid)
        self.assertIsNone(aligned[-2].mid)

    def test_invalid_config_rejected(self):
        for kwargs in [{"step_seconds": 0}, {"min_coverage": 0}, {"min_move_bps": float("nan")},
                       {"pre_seconds": 1}, {"endpoint_seconds": 400}, {"seed": 1.2}]:
            with self.assertRaises(ValueError):
                Config(**kwargs)


class EstimationTests(unittest.TestCase):
    def test_known_curve_recovery(self):
        points = [(tau, response(tau, 0.2, 20)) for tau in range(301)]
        fitted = fit_response(points, 300)
        self.assertAlmostEqual(fitted["jump"], 0.2, delta=0.05)
        self.assertAlmostEqual(fitted["t50_seconds"], 20 * math.log(1.6), delta=1.0)
        self.assertLess(fitted["rmse"], 0.01)
        self.assertEqual(time_to_fraction(0.5, 0.7, 20), 0)

    def observations(self, amplitude=20):
        return [Observation(tau, 100 * math.exp((0 if tau < 0 else amplitude * response(tau, 0.2, 20)) / 10000), 0.1, 0)
                for tau in range(-60, 301)]

    def test_negative_move_preserves_fraction_and_recovers_t50(self):
        result = analyze(event(), "X", self.observations(-20), Config(), False)
        self.assertEqual(result["status"], "accepted")
        self.assertLess(result["move_bps"], 0)
        self.assertAlmostEqual(result["fit"]["t50_seconds"], 20 * math.log(1.6), delta=1)

    def test_tiny_move_overlap_and_missing_data_are_excluded(self):
        self.assertIn("move_too_small_for_stable_normalization",
                      analyze(event(), "X", self.observations(0.1), Config(), False)["flags"])
        self.assertIn("overlapping_event_windows",
                      analyze(event(), "X", self.observations(), Config(), True)["flags"])
        missing = [replace(point, mid=None) if point.tau >= 240 else point for point in self.observations()]
        self.assertEqual(analyze(event(), "X", missing, Config(), False)["status"], "excluded")

    def test_overshoot_is_not_clipped_and_not_accepted(self):
        points = self.observations()
        points = [replace(point, mid=100 * math.exp(40 / 10000)) if 30 < point.tau < 60 else point for point in points]
        result = analyze(event(), "X", points, Config(), False)
        self.assertIn("overshoot", result["flags"])
        self.assertEqual(result["status"], "review")
        self.assertGreater(max(point["response"] for point in result["curve"]), 1.9)

    def test_bootstrap_reproducible_and_small_samples_not_overstated(self):
        self.assertEqual(bootstrap_median([1, 2, 3, 8], 100, 42), bootstrap_median([1, 2, 3, 8], 100, 42))
        self.assertIsNone(bootstrap_median([1], 100, 42)["ci_low"])


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        generate(cls.root / "inputs")
        cls.payload = run(cls.root / "inputs/events.csv", cls.root / "inputs/quotes.csv",
                          cls.root / "output", Config(bootstrap_samples=100), "simulation")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_pipeline_detects_speed_order_and_misspecification(self):
        self.assertLess(self.payload["summary"]["SIM_FAST"]["median"], self.payload["summary"]["SIM_SLOW"]["median"])
        self.assertTrue(all(item["status"] == "review" for item in self.payload["results"] if item["event_id"] == "SIM-023"))
        self.assertEqual(sum(self.payload["counts"].values()), 48)
        self.assertLess(next(iter(self.payload["paired_comparisons"].values()))["ci_high"], 0)

    def test_report_and_audit_manifest_exist(self):
        report = (self.root / "output/report.html").read_text()
        self.assertIn("SIMULATED DEMONSTRATION", report)
        self.assertIn("<svg", report)
        manifest = json.loads((self.root / "output/manifest.json").read_text())
        self.assertEqual(len(manifest["inputs"]), 2)
        self.assertIn("analysis.py", manifest["code_sha256"])

    def test_inputs_not_overwritten_or_mislabelled(self):
        with self.assertRaises(ValueError):
            run(self.root / "inputs/events.csv", self.root / "inputs/quotes.csv",
                self.root / "output", Config(), "simulation")
        with self.assertRaisesRegex(ValueError, "Simulation-marked"):
            run(self.root / "inputs/events.csv", self.root / "inputs/quotes.csv",
                self.root / "wrong-mode", Config(), "supplied")


if __name__ == "__main__":
    unittest.main()
