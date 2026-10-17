"""Construct causal event-time grids without backfilling from future quotes."""

from bisect import bisect_right
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

from .data import Event, Quote


@dataclass(frozen=True)
class Config:
    pre_seconds: int = 60
    post_seconds: int = 300
    step_seconds: float = 1
    max_staleness_seconds: int = 2
    endpoint_seconds: int = 60
    min_coverage: float = 0.95
    min_move_bps: float = 1.0
    noise_multiplier: float = 3.0
    min_history: int = 6
    bootstrap_samples: int = 500
    seed: int = 42

    def __post_init__(self):
        for name in ["pre_seconds", "post_seconds", "endpoint_seconds",
                     "min_history", "bootstrap_samples", "seed", "max_staleness_seconds"]:
            if type(getattr(self, name)) is not int:
                raise ValueError(name + " must be an integer")
        if type(self.step_seconds) not in (int, float) or not math.isfinite(self.step_seconds):
            raise ValueError("Sampling step must be finite numeric seconds")
        milliseconds = self.step_seconds * 1000
        if milliseconds < 1 or milliseconds != round(milliseconds):
            raise ValueError("Sampling step must be a positive whole number of milliseconds")
        if min(self.pre_seconds, self.post_seconds, self.step_seconds) <= 0:
            raise ValueError("Window lengths and sampling step must be positive")
        if self.pre_seconds * 1000 % round(milliseconds) or self.post_seconds * 1000 % round(milliseconds):
            raise ValueError("Windows must be divisible by the sampling step")
        if not self.step_seconds <= self.endpoint_seconds <= self.post_seconds:
            raise ValueError("Endpoint length must lie between one step and the post window")
        if self.pre_seconds // self.step_seconds < 3 or self.post_seconds // self.step_seconds < 10:
            raise ValueError("Need at least 3 pre-event and 10 post-event grid points")
        if self.max_staleness_seconds < 0 or not 0 < self.min_coverage <= 1:
            raise ValueError("Invalid staleness or coverage limit")
        if not 0 < self.min_move_bps < float("inf") or not 0 <= self.noise_multiplier < float("inf"):
            raise ValueError("Move/noise thresholds must be finite and nonnegative")
        if self.min_history < 2 or self.bootstrap_samples < 100:
            raise ValueError("Need history >= 2 and bootstrap samples >= 100")


@dataclass(frozen=True)
class Observation:
    tau: float
    mid: Optional[float]
    spread_bps: Optional[float]
    quote_age_seconds: Optional[float]


class QuoteIndex:
    """Index each instrument once so event matching uses binary search."""

    def __init__(self, quotes: List[Quote]):
        self.quotes = quotes
        self.times = [quote.timestamp for quote in quotes]

    def align(self, event: Event, config: Config) -> List[Observation]:
        output = []
        # Integer milliseconds prevent floating-point drift and retain exact endpoints.
        for ms in range(-config.pre_seconds * 1000, config.post_seconds * 1000 + 1,
                        round(config.step_seconds * 1000)):
            tau = ms / 1000 if ms % 1000 else ms // 1000
            target = event.release + timedelta(seconds=tau)
            index = bisect_right(self.times, target) - 1
            quote = self.quotes[index] if index >= 0 else None
            age = (target - quote.timestamp).total_seconds() if quote else None
            if quote is None or age > config.max_staleness_seconds:
                output.append(Observation(tau, None, None, age))
            else:
                output.append(Observation(tau, quote.mid,
                                          10000 * (quote.ask - quote.bid) / quote.mid, age))
        return output
