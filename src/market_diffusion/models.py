"""Transparent grid-search response model and event-level uncertainty.

The fit is descriptive: its normalized input includes a future endpoint.
It must never be represented as a tradable real-time forecast.
"""

import math
import random
from statistics import median


def response(tau: float, jump: float, scale: float) -> float:
    """Fractional response with an immediate jump then exponential adjustment."""
    return 1 - (1 - jump) * math.exp(-tau / scale)


def time_to_fraction(fraction: float, jump: float, scale: float) -> float:
    """Solve fraction = 1 - (1-jump) exp(-t/scale); times are in seconds."""
    return 0.0 if jump >= fraction else scale * math.log((1 - jump) / (1 - fraction))


def fit_response(points: list, horizon: int) -> dict:
    """Fit j in [0, .95] and k on a log-spaced grid by squared-error loss.

    Grid search is deliberately explicit and reproducible. Boundary solutions
    are flagged because the apparent estimate may reflect the chosen bounds.
    """
    scales = [0.25 * (8 * horizon) ** (index / 100) for index in range(101)]
    best = None
    # Cache each exponential basis once rather than evaluating it for each jump.
    for index, scale in enumerate(scales):
        decay = [math.exp(-tau / scale) for tau, _ in points]
        # Quadratic sufficient statistics give the same grid objective without
        # revisiting every tick for every jump. This makes fine grids affordable.
        yy = math.fsum((value - 1) ** 2 for _, value in points)
        yd = math.fsum((value - 1) * basis for (_, value), basis in zip(points, decay))
        dd = math.fsum(basis * basis for basis in decay)
        for jump_index in range(20):
            jump = jump_index / 20
            loss = max(0.0, (yy + 2 * (1 - jump) * yd + (1 - jump) ** 2 * dd) / len(points))
            if best is None or loss < best[0]:
                best = (loss, jump, scale, index, jump_index)
    loss, jump, scale, scale_index, jump_index = best
    instant_rmse = math.sqrt(sum((value - 1) ** 2 for _, value in points) / len(points))
    return {
        "jump": jump, "scale_seconds": scale,
        "t50_seconds": time_to_fraction(0.5, jump, scale),
        "t90_seconds": time_to_fraction(0.9, jump, scale),
        "rmse": math.sqrt(loss), "instant_rmse": instant_rmse,
        "at_grid_boundary": scale_index in (0, 100) or jump_index == 19,
    }


def bootstrap_median(values: list, samples: int, seed: int) -> dict:
    """Resample events, not dependent quote observations within an event.

    Percentile intervals describe between-event variability conditional on
    accepted fits. They do not estimate uncertainty of any individual curve.
    """
    if not values:
        return {"n": 0, "median": None, "ci_low": None, "ci_high": None}
    if len(values) < 3:
        return {"n": len(values), "median": median(values), "ci_low": None, "ci_high": None}
    rng = random.Random(seed)
    estimates = sorted(median(rng.choices(values, k=len(values))) for _ in range(samples))
    return {"n": len(values), "median": median(values),
            "ci_low": estimates[int(0.025 * (samples - 1))],
            "ci_high": estimates[int(0.975 * (samples - 1))]}
