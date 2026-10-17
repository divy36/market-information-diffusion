"""Baseline estimation, retrospective normalization, and fit diagnostics."""

import math
from statistics import median, stdev

from .alignment import Config
from .models import fit_response, response


def analyze(event, symbol, observations, config: Config, overlap: bool) -> dict:
    """Analyze one event/instrument; keep explicit reasons for rejected windows."""
    pre_all = [point for point in observations if point.tau < 0]
    post_all = [point for point in observations if point.tau >= 0]
    end_all = [point for point in post_all
               if point.tau > config.post_seconds - config.endpoint_seconds]
    pre = [point for point in pre_all if point.mid is not None]
    post = [point for point in post_all if point.mid is not None]
    end = [point for point in end_all if point.mid is not None]
    coverage = {"pre": len(pre) / len(pre_all), "post": len(post) / len(post_all),
                "endpoint": len(end) / len(end_all)}
    result = {"event_id": event.event_id, "symbol": symbol, "coverage": coverage,
              "status": "excluded", "flags": [], "fit": None, "curve": []}
    if overlap:
        result["flags"].append("overlapping_event_windows")
    if min(coverage.values()) < config.min_coverage or len(pre) < 3 or len(post) < 10 or len(end) < 2:
        result["flags"].append("insufficient_fresh_quotes")
    if result["flags"]:
        return result

    # Log prices make response amplitudes comparable across instruments.
    baseline = median(math.log(point.mid) for point in pre)
    endpoint = median(math.log(point.mid) for point in end)
    amplitude = 10000 * (endpoint - baseline)
    noise = 10000 * stdev(math.log(point.mid) for point in pre)
    result.update({"baseline_mid": math.exp(baseline), "endpoint_mid": math.exp(endpoint),
                   "move_bps": amplitude, "pre_noise_bps": noise,
                   "pre_spread_bps": median(point.spread_bps for point in pre)})
    if abs(amplitude) < max(config.min_move_bps, config.noise_multiplier * noise):
        result["flags"].append("move_too_small_for_stable_normalization")
        return result

    # Do not clip D to [0,1]: reversals and overshoots are economically relevant.
    points = [(point.tau, 10000 * (math.log(point.mid) - baseline) / amplitude) for point in post]
    fitted = fit_response(points, config.post_seconds)
    if fitted["at_grid_boundary"]:
        result["flags"].append("fit_at_search_boundary")
    if fitted["rmse"] > 0.15:
        result["flags"].append("poor_monotone_fit")
    if fitted["rmse"] >= fitted["instant_rmse"]:
        result["flags"].append("no_improvement_over_instant_response")
    if fitted["t90_seconds"] > config.post_seconds:
        result["flags"].append("t90_beyond_observed_window")
    if max(value for _, value in points) > 1.2:
        result["flags"].append("overshoot")
    if min(value for _, value in points) < -0.2:
        result["flags"].append("opposite_direction_response")
    if fitted["t50_seconds"] < config.step_seconds:
        result["flags"].append("t50_below_sampling_resolution")
    result["fit"] = fitted
    # Sub-resolution t50 is retained as a warning; other diagnostics exclude the
    # event from aggregate speed estimates to avoid interpreting a failed model.
    serious = set(result["flags"]) - {"t50_below_sampling_resolution"}
    result["status"] = "review" if serious else "accepted"
    for point in observations:
        result["curve"].append({
            "tau": point.tau, "mid": point.mid, "quote_age_seconds": point.quote_age_seconds,
            "response": None if point.mid is None else 10000 * (math.log(point.mid) - baseline) / amplitude,
            "fitted": response(point.tau, fitted["jump"], fitted["scale_seconds"]) if point.tau >= 0 else None,
        })
    return result
