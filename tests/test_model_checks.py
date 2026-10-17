"""Exercise flexible reconstruction without treating fit as predictive evidence."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from market_diffusion.model_checks import KNOT_FRACTIONS, check_record, compare_models, errors, flexible_fit, linear_basis
from market_diffusion.models import response


def record(values, fitted=None):
    fitted = fitted if fitted is not None else values
    return {"event_id": "SIM-1", "symbol": "SIM", "status": "review", "flags": ["overshoot"],
            "fit": {"jump": .2, "scale_seconds": 20},
            "curve": [{"tau": time, "response": value, "fitted": fitted[time]} for time, value in enumerate(values)]}


class ModelChecksTests(unittest.TestCase):
    def test_linear_curve_recovered_without_monotonic_constraint(self):
        points = [(time, 1.5 - time / 300) for time in range(301)]
        knots, heights, predictions = flexible_fit(points, 300)
        self.assertLess(errors(points, predictions)["rmse"], 1e-10)
        self.assertAlmostEqual(heights[0], 1.5)
        self.assertAlmostEqual(heights[-1], .5)

    def test_known_reversal_reconstructed_and_status_preserved(self):
        knots = [300 * fraction for fraction in KNOT_FRACTIONS]
        heights = [.2, .3, .4, .8, 1.5, .6, 1.1, 1, 1, 1]
        values = [sum(a * b for a, b in zip(linear_basis(time, knots), heights)) for time in range(301)]
        result = check_record(record(values, [response(time, .2, 20) for time in range(301)]), 300)
        self.assertLess(result["models"]["piecewise_linear"]["rmse"], 1e-10)
        self.assertGreater(result["models"]["exponential"]["rmse"], .05)
        self.assertEqual(result["status"], "review")
        self.assertNotIn("t50_seconds", result["models"]["piecewise_linear"])

    def test_correct_exponential_does_not_need_flexible_replacement(self):
        values = [response(time, .2, 20) for time in range(301)]
        result = check_record(record(values), 300)
        self.assertEqual(result["models"]["exponential"]["rmse"], 0)
        self.assertGreater(result["models"]["piecewise_linear"]["rmse"], 0)

    def test_early_error_is_not_diluted_by_long_flat_tail(self):
        points = [(time, 0 if time <= 60 else 1) for time in range(1801)]
        score = errors(points, [1] * len(points))
        self.assertEqual(score["early_rmse"], 1)
        self.assertAlmostEqual(score["rmse"], math.sqrt(61 / 1801))

    def test_coarse_sampling_is_unidentified_not_imputed(self):
        row = record([1] * 301)
        row["curve"] = row["curve"][::5]
        result = check_record(row, 300)
        self.assertNotIn("piecewise_linear", result["models"])
        self.assertIn("unidentified", result["flexible_unavailable_reason"])
        self.assertIn("exponential", result["models"])

    def test_nonfinite_values_rejected_and_exclusions_retained(self):
        row = record([1] * 301)
        row["curve"][10]["response"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            check_record(row, 300)
        row.update(fit=None, curve=[], status="excluded")
        result = check_record(row, 300)
        self.assertEqual(result["models"], {})
        self.assertIn("unavailable_reason", result)

    def test_audited_report_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "analysis.json"
            source.write_text(json.dumps({"mode": "simulation", "config": {"post_seconds": 300},
                                          "results": [record([1] * 301)]}))
            compare_models(source, root / "output")
            manifest = json.loads((root / "output/manifest.json").read_text())
            self.assertEqual(len(manifest["input_sha256"]), 64)
            self.assertIn("SIMULATED", (root / "output/report.md").read_text())
            with self.assertRaisesRegex(ValueError, "already exists"):
                compare_models(source, root / "output")


if __name__ == "__main__":
    unittest.main()
