"""`countersign-eval`: run the evaluation, gate on it, or print its fingerprint.

countersign-eval run                       # 3 trials, mutants, calibration; writes reports/evals/
countersign-eval run --publish             # also refresh the copy the API serves
countersign-eval run --gate                # exit 1 on a false positive, a surviving mutant or a regression
countersign-eval run --config REPORT.json  # rerun exactly what a report ran, to check it reproduces
countersign-eval run --model claude        # grade Claude on the same cases (needs ANTHROPIC_API_KEY; spends tokens)
countersign-eval fingerprint               # components, then the combined hash on the last line
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from countersign.evals import dataset, report
from countersign.evals.fingerprint import fingerprint
from countersign.evals.mutants import MUTANTS
from countersign.evals.runner import (
    Bench,
    Factory,
    baseline_of,
    build_report,
    calibration,
    gate,
    mutant_factory,
    reference_factory,
    run_variant,
)
from countersign.settings import BACKEND_ROOT

REPORTS = BACKEND_ROOT.parent / "reports" / "evals"
PUBLISHED = BACKEND_ROOT / "countersign" / "evals" / "published"


def _config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config:
        return dict(json.loads(Path(args.config).read_text())["config"])
    return {
        "model": args.model,
        "trials": args.trials,
        "seed": args.seed,
        "mutants": [] if args.no_mutants else list(MUTANTS),
        "mutant_trials": 1,
        "calibration": None
        if args.no_calibration
        else {"noise": args.noise, "trials": args.calibration_trials, "replicates": args.replicates},
        "cases": args.cases.split(",") if args.cases else None,
    }


def _run(args: argparse.Namespace) -> int:
    config = _config(args)
    if config["model"] == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "skipped: --model claude needs ANTHROPIC_API_KEY (this project never spends tokens by default)"
            )
            return 0
        from countersign.models.claude import ClaudeModel  # noqa: PLC0415 - only when asked for

        settings_model = ClaudeModel.from_settings
    cases = dataset.load(only=config["cases"])
    bench = Bench()
    say = (lambda line: print(line, file=sys.stderr)) if args.verbose else None
    started = time.perf_counter()
    try:
        factory: Factory
        if config["model"] == "claude":
            factory, name = (lambda _state: settings_model(bench.settings)), "claude"
        else:
            factory, name = reference_factory(), "reference"
        ref = run_variant(
            bench,
            cases,
            factory,
            name=name,
            trials=int(config["trials"]),
            seed=int(config["seed"]),
            progress=say,
        )
        mutants = {
            m: run_variant(
                bench,
                cases,
                mutant_factory(m),
                name=m,
                trials=int(config["mutant_trials"]),
                seed=int(config["seed"]),
                progress=say,
            )
            for m in config["mutants"]
        }
        cal = config.get("calibration")
        calib = (
            calibration(
                bench,
                cases,
                noise=float(cal["noise"]),
                trials=int(cal["trials"]),
                seed=int(config["seed"]),
                replicates=int(cal["replicates"]),
            )
            if cal
            else None
        )
    finally:
        bench.close()
    out = Path(args.out) if args.out else REPORTS
    result = build_report(
        reference=ref,
        mutants=mutants,
        calib=calib,
        fingerprint=fingerprint(),
        config=config,
        baseline_path=REPORTS / "baseline.json",
        elapsed_s=time.perf_counter() - started,
    )
    report.write(result, out)
    if args.update_baseline:
        (REPORTS / "baseline.json").write_text(json.dumps(baseline_of(result), indent=2) + "\n")
    if args.publish:
        PUBLISHED.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out / "latest.json", PUBLISHED / "latest.json")
    crit = result["criteria"]
    print(
        f"{result['n_cases']} cases x {result['trials']} trials: "
        + ", ".join(f"{c} {crit[c]['cases_all_pass']}/{result['n_cases']}" for c in crit)
    )
    if result.get("mutation"):
        m = result["mutation"]
        print(
            f"mutation score {m['killed']}/{m['total']}, reference false positives {m['reference_false_positives']}"
        )
    if calib:
        print(
            f"calibration covered: {calib['covered']} (coverage {calib['coverage']['mean_interval_coverage']})"
        )
    print(f"wrote {out / 'latest.json'}")
    if args.gate:
        problems = gate(result)
        for p in problems:
            print("GATE:", p, file=sys.stderr)
        return 1 if problems else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="countersign-eval", description="Evaluate the agent and the evaluator."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the dataset and write the report")
    run.add_argument("--model", choices=["reference", "claude"], default="reference")
    run.add_argument("--trials", type=int, default=3)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--cases", help="comma-separated case ids (default: all)")
    run.add_argument("--no-mutants", action="store_true")
    run.add_argument("--no-calibration", action="store_true")
    run.add_argument("--noise", type=float, default=0.2, help="the simulator's slip probability")
    run.add_argument("--calibration-trials", type=int, default=10)
    run.add_argument("--replicates", type=int, default=300)
    run.add_argument("--config", help="rerun the configuration recorded in this report")
    run.add_argument("--out", help=f"output directory (default {REPORTS})")
    run.add_argument("--publish", action="store_true", help="also update the report the API serves")
    run.add_argument("--update-baseline", action="store_true")
    run.add_argument("--gate", action="store_true")
    run.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("fingerprint", help="print the fingerprint components and the combined hash")
    args = parser.parse_args(argv)
    if args.command == "fingerprint":
        fp = fingerprint()
        for k, v in fp.items():
            if k != "combined":
                print(f"{k:18} {v}")
        print(fp["combined"])
        return 0
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
