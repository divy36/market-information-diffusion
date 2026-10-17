"""Descriptive reconstruction checks against a flexible, non-monotone curve.

Lower in-sample error is not evidence of prediction: the flexible model has more
parameters, and all curves already use a retrospective price endpoint.
"""

import json
import math
from collections import Counter
from pathlib import Path
from statistics import median

from .pipeline import digest


# Dense early knots retain the rapid announcement response. Fractions are fixed
# before fitting; their locations are never selected from the observed prices.
KNOT_FRACTIONS = (0, .005, .01, .025, .05, .1, .2, .4, .6, 1)


def linear_basis(time, knots):
    """Hat-function weights: each observation touches two adjacent knot values."""
    if not knots[0] <= time <= knots[-1]:
        raise ValueError("Observation outside the knot domain")
    weights = [0.0] * len(knots)
    for index in range(len(knots) - 1):
        if time <= knots[index + 1]:
            fraction = (time - knots[index]) / (knots[index + 1] - knots[index])
            weights[index], weights[index + 1] = 1 - fraction, fraction
            return weights
    raise ValueError("Invalid knot domain")


def solve(matrix, vector):
    """Small pivoted linear solve; unsupported knot values remain unavailable.

    No artificial ridge penalty fills unidentified coefficients. Coarse sampling
    can make early knots unidentifiable, which should be visible in the report.
    """
    size = len(vector)
    augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
    scale = max(abs(value) for row in matrix for value in row)
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) <= max(1.0, scale) * 1e-12:
            raise ValueError("Flexible model is unidentified at this sampling/coverage")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(size):
            if row != col:
                factor = augmented[row][col]
                augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[col])]
    return [row[-1] for row in augmented]


def flexible_fit(points, horizon):
    """Least squares on ten fixed knot heights, allowing overshoot and reversal."""
    knots = [horizon * fraction for fraction in KNOT_FRACTIONS]
    design = [linear_basis(time, knots) for time, _ in points]
    size = len(knots)
    gram = [[sum(row[i] * row[j] for row in design) for j in range(size)] for i in range(size)]
    target = [sum(row[i] * point[1] for row, point in zip(design, points)) for i in range(size)]
    heights = solve(gram, target)
    predictions = [sum(weight * height for weight, height in zip(row, heights)) for row in design]
    return knots, heights, predictions


def errors(points, predictions):
    """Score all observations and the first 60 seconds separately.

    Long flat tails can dilute whole-window RMSE while the initial fit is poor.
    RMSE is measured in normalized response units, not seconds or basis points.
    """
    def score(indices):
        return math.sqrt(sum((points[i][1] - predictions[i]) ** 2 for i in indices) / len(indices)) if indices else None
    early = [i for i, (time, _) in enumerate(points) if time <= 60]
    return {"rmse": score(list(range(len(points)))), "early_rmse": score(early), "early_n": len(early)}


def check_record(record, horizon):
    result = {key: record[key] for key in ("event_id", "symbol", "status", "flags")}
    result["models"] = {}
    if record["fit"] is None:
        result["unavailable_reason"] = "No normalized fitted curve; original exclusion retained"
        return result
    rows = [row for row in record["curve"] if row["tau"] >= 0 and row["response"] is not None]
    if not rows or any(not all(isinstance(row[key], (float, int)) and math.isfinite(row[key])
                               for key in ("tau", "response", "fitted")) for row in rows):
        raise ValueError("Comparison requires finite post-event times, responses and fitted values")
    times = [row["tau"] for row in rows]
    if times != sorted(set(times)) or times[-1] > horizon:
        raise ValueError("Curve times must be unique, sorted and inside the horizon")
    points = [(row["tau"], row["response"]) for row in rows]
    result["n"] = len(points)
    result["models"]["instant"] = {"parameters": 0, **errors(points, [1.0] * len(points))}
    result["models"]["exponential"] = {"parameters": 2, **errors(points, [row["fitted"] for row in rows])}
    try:
        knots, heights, predictions = flexible_fit(points, horizon)
    except ValueError as error:
        result["flexible_unavailable_reason"] = str(error)
    else:
        result["models"]["piecewise_linear"] = {"parameters": len(knots), "knots_seconds": knots,
            "knot_heights": heights, **errors(points, predictions)}
    # Do not change acceptance or produce a flexible-model t50: reversals can
    # cross 50% repeatedly, making a single diffusion time misleading.
    return result


def compare_models(analysis_path: Path, output: Path):
    """Audit an existing run, including review/excluded events, without refitting it."""
    if output.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    payload = json.loads(analysis_path.read_text())
    if payload.get("mode") not in ("simulation", "supplied"):
        raise ValueError("Analysis must declare simulation or supplied mode")
    horizon = payload["config"]["post_seconds"]
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("Positive integer horizon required")
    results = [check_record(row, horizon) for row in payload["results"]]
    summary = {"mode": payload["mode"], "horizon_seconds": horizon,
               "counts": dict(Counter(row["status"] for row in results)), "results": results}
    lines = ["# Model reconstruction checks — " + payload["mode"].upper(), "",
             "SIMULATED data only." if payload["mode"] == "simulation" else "Supplied data; retrospective descriptive diagnostics.", "",
             "All errors are in-sample RMSE in normalized response units. The flexible curve has "
             "ten fitted heights versus two exponential parameters; lower error does not establish "
             "better prediction. Early RMSE covers observations from 0 through 60 seconds. "
             "Acceptance labels are inherited unchanged. No model is automatically selected.", "",
             "| Original status | Model | Curves scored | Median RMSE | Median early RMSE |",
             "|---|---|---:|---:|---:|"]
    for status in ("accepted", "review", "excluded"):
        for model in ("instant", "exponential", "piecewise_linear"):
            scores = [row["models"][model] for row in results if row["status"] == status and model in row["models"]]
            if scores:
                early = [row["early_rmse"] for row in scores if row["early_rmse"] is not None]
                lines.append("| %s | %s | %d | %.5f | %s |" % (status, model, len(scores),
                    median(row["rmse"] for row in scores), "%.5f" % median(early) if early else "unavailable"))
    lines += ["", "Original status counts: " + json.dumps(summary["counts"], sort_keys=True), "",
              "Per-event errors, knot heights and reasons for unavailable comparisons are in comparison.json. "
              "A coarse grid can leave knot heights unidentified. Models with different scored-event counts "
              "must not be compared through these aggregate medians. These are diagnostic checks, not "
              "a formal model-selection test or an independent holdout evaluation.", ""]
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    (output / "report.md").write_text("\n".join(lines))
    (output / "manifest.json").write_text(json.dumps({"input_sha256": digest(analysis_path),
        "input_path": str(analysis_path.resolve()), "mode": payload["mode"],
        "code_sha256": digest(Path(__file__)), "knot_fractions": KNOT_FRACTIONS,
        "note": "Saved exponential predictions reused; original analysis records their provenance."}, indent=2) + "\n")
    return {"mode": payload["mode"], "counts": summary["counts"], "report": str((output / "report.md").resolve())}
