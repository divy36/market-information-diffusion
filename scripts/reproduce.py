"""Run the complete labelled simulation in a new output folder, without network access."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from market_diffusion.alignment import Config
from market_diffusion.demo import generate
from market_diffusion.holdout import evaluate, freeze
from market_diffusion.model_checks import compare_models
from market_diffusion.pipeline import digest, run
from market_diffusion.sensitivity import run_sensitivity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260921)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"mode": "simulation", "status": "running", "seed": args.seed,
                "created_utc": datetime.now(timezone.utc).isoformat(), "driver_sha256": digest(Path(__file__))}
    path = args.output / "reproduction.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        inputs = args.output / "simulated_inputs"
        generate(inputs, seed=args.seed, post_seconds=1800)
        events, quotes = inputs / "events.csv", inputs / "quotes.csv"
        protocol = args.output / "protocol.json"
        # Freeze before fitting either partition. Full-sample diagnostics later
        # are exploratory and do not create another untouched holdout.
        freeze(events, project / "config/default.json", "2021-01-01T00:00:00Z", protocol, "simulation")
        evaluate(events, quotes, protocol, "development", args.output / "development")
        evaluate(events, quotes, protocol, "holdout", args.output / "holdout", args.output / "development")
        print("Chronological simulation complete; running exploratory sensitivity checks.", flush=True)
        run(events, quotes, args.output / "baseline", Config(), "simulation")
        run_sensitivity(events, quotes, project / "config/sensitivity-v1.json", args.output / "sensitivity", "simulation")
        compare_models(args.output / "baseline/analysis.json", args.output / "model_checks")
        manifest["status"] = "complete"
        (args.output / "README.md").write_text(
            "# Completed simulated reproduction\n\nNo real prices or empirical conclusions.\n\n"
            "- [Baseline report](baseline/report.html)\n- [Development report](development/report.html)\n"
            "- [Holdout report](holdout/report.html)\n- [Sensitivity report](sensitivity/report.md)\n"
            "- [Model checks](model_checks/report.md)\n\n"
            "protocol.json records settings and chronology. Individual manifests preserve input/code hashes.\n")
    except Exception as error:
        manifest.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    print("Completed simulation: " + str(args.output.resolve()))


if __name__ == "__main__":
    main()
