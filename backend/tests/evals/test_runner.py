"""The runner end to end: real runs reproduce exactly, every mutant is caught by its grader, the report is
complete, the gate works, and the published report matches the code it describes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from countersign.evals import cli, dataset, report
from countersign.evals.fingerprint import PACKAGE, RUN_SOURCES, fingerprint
from countersign.evals.graders import CRITERIA
from countersign.evals.mutants import MUTANTS
from countersign.evals.runner import (
    Bench,
    baseline_diff,
    baseline_of,
    build_report,
    calibration,
    gate,
    mutant_factory,
    reference_factory,
    run_variant,
)
from countersign.settings import BACKEND_ROOT

pytestmark = pytest.mark.integration
PICK = [
    "c01-default-dev-vms-and-disks",
    "c04-production-replica",
    "c11-pricing-outage",
    "c12-metrics-timeout",
    "c21-grace-period",
]
CASES = dataset.load(only=PICK)


@pytest.fixture(scope="module")
def bench() -> Iterator[Bench]:
    b = Bench()
    yield b
    b.close()


def test_reruns_reproduce_every_outcome(bench: Bench) -> None:
    a = run_variant(bench, CASES, reference_factory(), name="reference", trials=2, seed=5)
    b = run_variant(bench, CASES, reference_factory(), name="reference", trials=2, seed=5)
    assert [cr.outcomes() for cr in a.cases] == [cr.outcomes() for cr in b.cases]
    assert all(cr.all_pass(c) for cr in a.cases for c in CRITERIA)


@pytest.mark.parametrize("name", list(MUTANTS))
def test_each_mutant_is_caught_by_the_grader_built_for_it(bench: Bench, name: str) -> None:
    v = run_variant(bench, CASES, mutant_factory(name), name=name, trials=1, seed=0)
    expected = MUTANTS[name].expected_grader
    assert any(not cr.all_pass(expected) for cr in v.cases), f"{name} survived {expected}"


def test_report_gate_baseline_and_rendering(bench: Bench, tmp_path: Path) -> None:
    ref = run_variant(bench, CASES, reference_factory(), name="reference", trials=2, seed=0)
    muts = {m: run_variant(bench, CASES, mutant_factory(m), name=m, trials=1, seed=0) for m in MUTANTS}
    cal = calibration(bench, CASES, noise=0.3, trials=4, seed=0, replicates=40)
    r = build_report(
        reference=ref,
        mutants=muts,
        calib=cal,
        fingerprint=fingerprint(),
        config={"seed": 0},
        baseline_path=tmp_path / "baseline.json",
        elapsed_s=1.0,
    )
    assert "reference policy" in r["honesty"] and " not " in r["honesty"]
    assert set(r["criteria"]) == set(CRITERIA) and len(r["cases"]) == len(CASES)
    assert r["mutation"]["reference_false_positives"] == 0 and r["mutation"]["killed"] == len(MUTANTS)
    assert r["calibration"]["other_criteria_unaffected"]
    assert gate(r) == []
    (tmp_path / "baseline.json").write_text(json.dumps(baseline_of(r)))
    worse = json.loads(json.dumps(r["cases"]))
    worse[0]["outcomes"]["safety"] = [0, 1]
    diff = baseline_diff(worse, tmp_path / "baseline.json")
    assert diff["regressions"] == [{"case": PICK[0], "criterion": "safety"}]
    assert gate({**r, "baseline": diff}) == [f"regression against the baseline: {PICK[0]} safety"]
    report.write(r, tmp_path / "out")
    assert "Mutation score" in (tmp_path / "out" / "latest.md").read_text()
    assert "<table>" in (tmp_path / "out" / "latest.html").read_text()


def test_cli_fingerprint_and_a_small_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["fingerprint"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == fingerprint()["combined"]
    out = tmp_path / "run"
    assert (
        cli.main(
            [
                "run",
                "--cases",
                PICK[1],
                "--trials",
                "1",
                "--no-calibration",
                "--no-mutants",
                "--out",
                str(out),
                "--gate",
            ]
        )
        == 0
    )
    first = json.loads((out / "latest.json").read_text())
    again = tmp_path / "again"
    assert cli.main(["run", "--config", str(out / "latest.json"), "--out", str(again)]) == 0
    second = json.loads((again / "latest.json").read_text())
    assert [(c["id"], c["outcomes"]) for c in first["cases"]] == [
        (c["id"], c["outcomes"]) for c in second["cases"]
    ]
    assert cli.main(["run", "--model", "claude", "--cases", PICK[1]]) == 0  # no key: skipped, never spends


def test_the_published_report_describes_this_exact_code() -> None:
    published = BACKEND_ROOT / "countersign" / "evals" / "published" / "latest.json"
    r = json.loads(published.read_text())
    assert r["fingerprint"]["combined"] == fingerprint()["combined"], (
        "stale report: run `uv run countersign-eval run --publish`"
    )
    assert r["n_cases"] == len(dataset.load()) and r["mutation"]["killed"] == len(MUTANTS)


def test_the_fingerprint_covers_the_code_every_trial_runs_through() -> None:
    """The published-report check is only as strong as the fingerprint: it must hash the runtime, the tools,
    the analyzer, the policy engine, the sandbox and the executor, not only the definition of the measurement."""
    fp = fingerprint()
    assert {"runtime", "tool_code", "analyzer", "policy_engine", "sandbox", "executor"} <= set(RUN_SOURCES)
    for name, paths in RUN_SOURCES.items():
        assert len(fp[name]) == 64
        assert all((PACKAGE / path).is_file() for path in paths), name
    assert len(set(fp.values())) == len(fp)  # every component hashes something different
