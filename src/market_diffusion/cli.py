"""Small command-line interface; explicit modes keep demonstrations identifiable."""

import argparse
import json
from pathlib import Path

from .alignment import Config
from .demo import generate
from .pipeline import run
from .bls import import_releases, join_consensus
from .databento_api import make_plan, estimate, download
from .consensus_pdf import import_consensus
from .sensitivity import run_sensitivity
from .holdout import freeze, evaluate
from .model_checks import compare_models
from .readiness import check_readiness
from .dukascopy import fetch as fetch_fx, import_hours


def main():
    parser = argparse.ArgumentParser(description="Retrospective market response research")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("fetch-fx", "import-fx"):
        fx = sub.add_parser(command, help="Download public FX hours or reimport preserved originals")
        fx.add_argument("--events", type=Path, required=True)
        fx.add_argument("--raw-directory", type=Path, required=True)
        fx.add_argument("--output", type=Path, required=True)
    readiness = sub.add_parser("check-readiness", help="Audit local CPI inputs and coverage without API calls")
    readiness.add_argument("--events", type=Path, required=True)
    readiness.add_argument("--actuals", type=Path, required=True)
    readiness.add_argument("--plan", type=Path, required=True)
    readiness.add_argument("--config", type=Path, required=True)
    readiness.add_argument("--quotes", type=Path)
    readiness.add_argument("--output", type=Path, required=True)
    model_checks = sub.add_parser("compare-models", help="Check saved curves against descriptive alternatives")
    model_checks.add_argument("--analysis", type=Path, required=True)
    model_checks.add_argument("--output", type=Path, required=True)
    frozen = sub.add_parser("freeze-holdout", help="Record a chronological split without reading quotes")
    frozen.add_argument("--events", type=Path, required=True)
    frozen.add_argument("--config", type=Path, required=True)
    frozen.add_argument("--cutoff", required=True, help="ISO timestamp with explicit UTC offset")
    frozen.add_argument("--output", type=Path, required=True)
    frozen.add_argument("--mode", choices=["simulation", "supplied"], required=True)
    evaluation = sub.add_parser("evaluate-holdout", help="Evaluate one partition using frozen settings")
    evaluation.add_argument("--events", type=Path, required=True)
    evaluation.add_argument("--quotes", type=Path, required=True)
    evaluation.add_argument("--protocol", type=Path, required=True)
    evaluation.add_argument("--phase", choices=["development", "holdout"], required=True)
    evaluation.add_argument("--development-run", type=Path)
    evaluation.add_argument("--output", type=Path, required=True)
    demo = sub.add_parser("demo", help="Generate and analyze labelled simulated data")
    demo.add_argument("--output", type=Path, required=True, help="New run directory")
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--post-seconds", type=int, default=300,
                      help="Simulated quote horizon; baseline analysis remains 5 minutes")
    sensitivity = sub.add_parser("sensitivity", help="Compare a prespecified plan on identical inputs")
    sensitivity.add_argument("--events", type=Path, required=True)
    sensitivity.add_argument("--quotes", type=Path, required=True)
    sensitivity.add_argument("--plan", type=Path, required=True)
    sensitivity.add_argument("--output", type=Path, required=True)
    sensitivity.add_argument("--mode", choices=["simulation", "supplied"], required=True)
    study = sub.add_parser("analyze", help="Analyze supplied event and quote CSVs")
    study.add_argument("--events", type=Path, required=True)
    study.add_argument("--quotes", type=Path, required=True)
    study.add_argument("--output", type=Path, required=True)
    study.add_argument("--mode", choices=["simulation", "supplied"], required=True)
    study.add_argument("--config", type=Path)
    bls = sub.add_parser("import-bls", help="Parse saved BLS CPI web-reader extracts")
    bls.add_argument("--raw-directory", type=Path, required=True)
    bls.add_argument("--output", type=Path, required=True)
    enrich = sub.add_parser("join-consensus", help="Join pre-release consensus evidence to CPI actuals")
    enrich.add_argument("--actuals", type=Path, required=True)
    enrich.add_argument("--consensus", type=Path, required=True)
    enrich.add_argument("--component", choices=["headline", "core"], required=True)
    enrich.add_argument("--output", type=Path, required=True)
    plan = sub.add_parser("plan-databento", help="Prepare a futures API request plan without network access")
    plan.add_argument("--actuals", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    cost = sub.add_parser("estimate-databento", help="Query estimated cost; no price download")
    cost.add_argument("--plan", type=Path, required=True)
    fetch = sub.add_parser("fetch-databento", help="Download via API after a fresh cost estimate")
    fetch.add_argument("--plan", type=Path, required=True)
    fetch.add_argument("--raw-directory", type=Path, required=True)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--max-estimated-usd", type=float, required=True)
    consensus_pdf = sub.add_parser("import-consensus-pdf", help="Extract pre-release consensus from local Scotiabank PDFs")
    consensus_pdf.add_argument("--actuals", type=Path, required=True)
    consensus_pdf.add_argument("--pdf-directory", type=Path, required=True)
    consensus_pdf.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command in ("fetch-fx", "import-fx"):
            operation = fetch_fx if args.command == "fetch-fx" else import_hours
            print(json.dumps(operation(args.events, args.raw_directory, args.output), indent=2))
            return
        if args.command == "check-readiness":
            config = Config(**json.loads(args.config.read_text()))
            print(json.dumps(check_readiness(args.events, args.actuals, args.plan, args.output,
                                             config, args.quotes), indent=2))
            return
        if args.command == "compare-models":
            print(json.dumps(compare_models(args.analysis, args.output), indent=2))
            return
        if args.command == "freeze-holdout":
            result = freeze(args.events, args.config, args.cutoff, args.output, args.mode)
            print(json.dumps({"protocol": str(args.output.resolve()),
                              "event_counts": {key: len(value) for key, value in result["partitions"].items()}}, indent=2))
            return
        if args.command == "evaluate-holdout":
            print(json.dumps(evaluate(args.events, args.quotes, args.protocol, args.phase,
                                      args.output, args.development_run), indent=2))
            return
        if args.command == "sensitivity":
            payload = run_sensitivity(args.events, args.quotes, args.plan, args.output, args.mode)
            print(json.dumps({"mode": payload["mode"], "report": str((args.output / "report.md").resolve())}, indent=2))
            return
        if args.command == "import-consensus-pdf":
            print(json.dumps(import_consensus(args.actuals, args.pdf_directory, args.output), indent=2))
            return
        if args.command == "plan-databento":
            print(json.dumps(make_plan(args.actuals, args.output), indent=2))
            return
        if args.command == "estimate-databento":
            print(json.dumps(estimate(args.plan), indent=2))
            return
        if args.command == "fetch-databento":
            print(json.dumps(download(args.plan, args.raw_directory, args.output, args.max_estimated_usd), indent=2))
            return
        if args.command == "import-bls":
            print(json.dumps(import_releases(args.raw_directory, args.output), indent=2))
            return
        if args.command == "join-consensus":
            print(json.dumps(join_consensus(args.actuals, args.consensus, args.output, args.component), indent=2))
            return
        if args.command == "demo":
            if args.output.exists():
                raise ValueError("Demo output already exists; choose a new directory")
            inputs = args.output / "simulated_inputs"
            generate(inputs, args.seed, args.post_seconds)
            payload = run(inputs / "events.csv", inputs / "quotes.csv", args.output / "analysis",
                          Config(seed=args.seed), "simulation")
            report = args.output / "analysis" / "report.html"
        else:
            config = Config(**json.loads(args.config.read_text())) if args.config else Config()
            payload = run(args.events, args.quotes, args.output, config, args.mode)
            report = args.output / "report.html"
    except (ValueError, TypeError, OSError) as error:
        parser.exit(2, "Error: " + str(error) + "\n")
    print(json.dumps({"mode": payload["mode"], "counts": payload["counts"],
                      "report": str(report.resolve())}, indent=2))


if __name__ == "__main__":
    main()
