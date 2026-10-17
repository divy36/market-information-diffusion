"""Generate reproducible SIMULATED test inputs, never empirical market evidence."""

import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def write_csv(path: Path, records: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def generate(directory: Path, seed: int = 42, post_seconds: int = 300) -> None:
    """Create toy markets with known response speeds and one misspecified event.

    These are fictional instruments and dates. We include a reversal to verify
    that diagnostics catch a curve that an exponential cannot explain well.
    """
    if type(post_seconds) is not int or post_seconds < 300:
        raise ValueError("Simulation post_seconds must be an integer >= 300")
    directory.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    events, quotes, truth = [], [], []
    for index in range(24):
        release = datetime(2020, 1, 15, 13, 30, tzinfo=timezone.utc) + timedelta(days=30 * index)
        surprise = rng.choice([-0.3, -0.2, -0.1, 0.1, 0.2, 0.3])
        event_id = "SIM-%03d" % index
        events.append({"event_id": event_id, "series": "SIMULATED_CPI_MOM",
                       "release_utc": release.isoformat(), "actual": 0.2 + surprise,
                       "consensus": 0.2, "consensus_asof_utc": (release - timedelta(hours=1)).isoformat(),
                       "unit": "percentage_points", "source": "SIMULATED; not a historical release"})
        for symbol, base, scale, jump in [("SIM_FAST", 100, 8, 0.2), ("SIM_SLOW", 200, 35, 0.1)]:
            event_scale = scale * rng.uniform(0.8, 1.2)
            amplitude = -surprise * 100
            truth.append({"event_id": event_id, "symbol": symbol, "scale_seconds": event_scale,
                          "jump": jump, "t50_seconds": event_scale * math.log((1 - jump) / 0.5),
                          "misspecified": index == 23})
            # Longer fixtures support horizon checks; the default preserves the
            # original seeded demonstration exactly. Changing length changes RNG draws.
            for tau in range(-65, post_seconds + 1):
                fraction = 0 if tau < 0 else 1 - (1 - jump) * math.exp(-tau / event_scale)
                if index == 23 and tau >= 0:
                    fraction += 0.8 * math.sin(tau / 18) * math.exp(-tau / 150)
                mid = base * math.exp((amplitude * fraction + rng.gauss(0, 0.08)) / 10000)
                quotes.append({"symbol": symbol, "timestamp_utc": (release + timedelta(seconds=tau)).isoformat(),
                               "bid": mid * (1 - 0.000005), "ask": mid * (1 + 0.000005)})
    write_csv(directory / "events.csv", events)
    write_csv(directory / "quotes.csv", quotes)
    (directory / "simulation_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    (directory / "README.txt").write_text("SIMULATED fixtures only. Not real prices, releases, or trading results.\n")
