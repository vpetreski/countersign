"""Runs the dataset through the real runtime and builds the report.

Every trial is a real run: the same runtime, tools, policy engine, capability tokens, sandbox and audit chain as
the live app, on an in-memory SQLite database with the fixture estate, a fresh workspace per trial, a fixed clock
and no sleeping. The scripted operator answers questions with the case's answer and the scripted approvers decide
as the case says. Seeds are derived from (case, trial), so a report reruns to identical outcomes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from countersign.agent.runtime import Principal, Role, Runtime
from countersign.agent.state import RunState
from countersign.data.estate import ESTATE
from countersign.data.ingest import ingest
from countersign.data.pricing import PriceCatalog
from countersign.evals import stats
from countersign.evals.dataset import Case
from countersign.evals.graders import CRITERIA, Grade, World, grade
from countersign.evals.mutants import MUTANTS
from countersign.evals.record import TrialRecord, extract
from countersign.models.base import ChatModel
from countersign.models.offline import ReferencePolicy
from countersign.policy.engine import default_policy
from countersign.settings import BACKEND_ROOT, Settings
from countersign.store.db import Database
from countersign.store.schema import RunRow, WorkspaceRow

FIXTURES = BACKEND_ROOT / "fixtures"
INGESTED_AT = dt.datetime(2026, 11, 1, 6, 0, tzinfo=dt.UTC)
CLOCK_START = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.UTC)
HONESTY = (
    "Offline results come from the reference policy, a deterministic test double that is not a language model: "
    "they validate the harness, the graders and the statistics, not LLM quality. The Claude adapter is "
    "contract-tested against the SDK's types and strict-schema rules, not run against the API, because this "
    "public project never spends tokens; set ANTHROPIC_API_KEY and run `countersign-eval run --model claude` to "
    "grade Claude on the same cases."
)
Factory = Callable[[RunState], ChatModel]


def eval_settings() -> Settings:
    return Settings(_env_file=None, env="test", database_url="sqlite://", live_prices=False)


class _Clock:
    def __init__(self) -> None:
        self.now = CLOCK_START

    def __call__(self) -> dt.datetime:
        self.now += dt.timedelta(milliseconds=10)
        return self.now


def trial_seed(case_id: str, trial: int, base: int) -> int:
    return int(hashlib.sha256(f"{base}:{case_id}:{trial}".encode()).hexdigest()[:8], 16) % 2**31


class Bench:
    """The fixture estate ingested once into an in-memory database; one workspace per trial."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or eval_settings()
        self.db = Database("sqlite://")
        self.db.create_all()
        ESTATE.clear()
        ingest(self.db, self.settings, now=INGESTED_AT)
        self.catalog = PriceCatalog(FIXTURES)
        self.policy = default_policy()
        self.clock = _Clock()
        with self.db.session() as s:
            self.world = World.from_estate(ESTATE.view(s, None))
        self._n = 0

    def close(self) -> None:
        self.db.engine.dispose()
        ESTATE.clear()

    def trial(self, case: Case, factory: Factory, trial: int, seed: int) -> TrialRecord:
        self._n += 1
        ws = f"ws_eval_{self._n:05d}"
        now = self.clock()
        with self.db.session() as s:
            s.add(WorkspaceRow(id=ws, created_at=now, expires_at=now + dt.timedelta(days=3)))
        runtime = Runtime(
            self.db,
            self.settings,
            model_factory=lambda state, _s: factory(state),
            catalog=self.catalog,
            policy=self.policy,
            clock=self.clock,
            sleep=lambda _x: None,
        )
        runtime.executor._sleep = lambda _x: None  # the evaluation never sleeps
        who = {
            "operator": Principal(f"{ws}:operator", ws, "operator", Role.OPERATOR, "Sam"),
            "approver": Principal(f"{ws}:approver", ws, "approver", Role.APPROVER, "Alex"),
            "approver2": Principal(f"{ws}:approver2", ws, "approver2", Role.APPROVER, "Jordan"),
        }
        started = time.perf_counter()
        run_id = runtime.create(who["operator"], case.request, [f.value for f in case.faults], seed=seed)
        answered = False
        for _ in range(120):
            with self.db.session() as s:
                row = s.get(RunRow, run_id)
                assert row is not None
                status, state = row.status, dict(row.state)
            if status in (
                "completed",
                "completed_with_failures",
                "failed",
                "rejected",
                "cancelled",
                "budget_exhausted",
                "expired",
            ):
                break
            if status == "awaiting_input":
                if answered:
                    break  # a second question: leave it unanswered; the graders see a run stuck waiting
                runtime.answer(who["operator"], run_id, case.operator_answer or "Use the request as written.")
                answered = True
            elif status == "awaiting_approval":
                if case.approvals.decision == "none":
                    break
                plan = state["plan"]
                if case.approvals.decision == "reject":
                    decisions = {a["id"]: "reject" for a in plan["actions"]}
                else:
                    decisions = {
                        a["id"]: "reject" if a["operation"] in case.approvals.reject_operations else "approve"
                        for a in plan["actions"]
                    }
                for name in ("approver", "approver2"):
                    with self.db.session() as s:
                        current = s.get(RunRow, run_id)
                        assert current is not None
                        if current.status != "awaiting_approval":
                            break
                    runtime.decide(who[name], run_id, plan["hash"], decisions, None)
            else:
                runtime.advance(who["operator"], run_id)
        elapsed = (time.perf_counter() - started) * 1000
        return extract(self.db, run_id, case_id=case.id, trial=trial, seed=seed, duration_ms=elapsed)


# ---------------------------------------------------------------- one variant over the dataset


@dataclass
class CaseResult:
    case: Case
    records: list[TrialRecord]
    grades: list[dict[str, Grade]]

    def outcomes(self) -> dict[str, list[int]]:
        return {c: [int(g[c].passed) for g in self.grades] for c in CRITERIA}

    def passes(self, criterion: str) -> int:
        return sum(g[criterion].passed for g in self.grades)

    def all_pass(self, criterion: str) -> bool:
        return all(g[criterion].passed for g in self.grades)


@dataclass
class VariantResult:
    name: str
    model: str
    trials: int
    cases: list[CaseResult] = field(default_factory=list)


def run_variant(
    bench: Bench,
    cases: Sequence[Case],
    factory: Factory,
    *,
    name: str,
    trials: int,
    seed: int,
    progress: Callable[[str], None] | None = None,
) -> VariantResult:
    result = VariantResult(name, "", trials)
    for case in cases:
        records = [bench.trial(case, factory, t, trial_seed(case.id, t, seed)) for t in range(trials)]
        result.model = records[0].model if records else result.model
        result.cases.append(CaseResult(case, records, [grade(case, r, bench.world) for r in records]))
        if progress:
            failed = [c for c in CRITERIA if not all(g[c].passed for g in result.cases[-1].grades)]
            progress(f"{name:14} {case.id:36} {'ok' if not failed else 'FAIL ' + ','.join(failed)}")
    return result


def reference_factory(noise: float = 0.0) -> Factory:
    return lambda state: ReferencePolicy(seed=state.seed, noise=noise)


def mutant_factory(name: str) -> Factory:
    cls = MUTANTS[name].cls
    return lambda state: cls(seed=state.seed)


# ---------------------------------------------------------------- statistics over a variant


def criteria_stats(v: VariantResult, *, resamples: int = 2000, seed: int = 0) -> dict[str, dict[str, Any]]:
    out = {}
    n = len(v.cases)
    for c in CRITERIA:
        means = [cr.passes(c) / v.trials for cr in v.cases]
        ci, method = stats.interval(means, resamples=resamples, seed=seed)
        all_pass = sum(cr.all_pass(c) for cr in v.cases)
        out[c] = {
            "mean": round(stats.mean(means), 6),
            "ci95": list(ci),
            "ci_method": method,
            "cases_all_pass": all_pass,
            "cases_all_pass_rate": round(all_pass / n, 6) if n else 0.0,
            "cases_all_pass_wilson": list(stats.wilson(all_pass, n)),
            "n_cases": n,
        }
    return out


def overall_passes(v: VariantResult) -> list[int]:
    """Per case, the trials in which every criterion passed (the strict, joint success)."""
    return [sum(all(g[c].passed for c in CRITERIA) for g in cr.grades) for cr in v.cases]


def pass_hat(v: VariantResult) -> dict[str, float]:
    passes = overall_passes(v)
    return {str(k): round(stats.pass_hat_k(passes, v.trials, k), 6) for k in range(1, v.trials + 1)}


# ---------------------------------------------------------------- mutation analysis


def mutation_analysis(reference: VariantResult, mutants: dict[str, VariantResult]) -> dict[str, Any]:
    ref_all = {cr.case.id: {c: cr.all_pass(c) for c in CRITERIA} for cr in reference.cases}
    false_positives = sum(not ok for row in ref_all.values() for ok in row.values())
    report: dict[str, Any] = {}
    killed = 0
    for name, v in mutants.items():
        info = MUTANTS[name]
        discordant = dict.fromkeys(CRITERIA, 0)
        examples: dict[str, str] = {}
        for cr in v.cases:
            for c in CRITERIA:
                if ref_all[cr.case.id][c] and not cr.all_pass(c):
                    discordant[c] += 1
                    if c not in examples:
                        reason = next((r for g in cr.grades for r in g[c].reasons), "")
                        examples[c] = f"{cr.case.id}: {reason}"
        caught = [c for c in CRITERIA if discordant[c] > 0]
        killed += info.expected_grader in caught
        report[name] = {
            "description": info.description,
            "expected_grader": info.expected_grader,
            "caught_by": caught,
            "caught_by_expected": info.expected_grader in caught,
            "scores": {
                c: round(stats.mean([cr.passes(c) / v.trials for cr in v.cases]), 6) for c in CRITERIA
            },
            "discordant_cases": discordant,
            "p_value": {
                c: float(f"{stats.mcnemar_exact(discordant[c], 0):.3g}") for c in CRITERIA
            },  # 3 significant digits
            "significant": {c: stats.mcnemar_exact(discordant[c], 0) < 0.05 for c in CRITERIA},
            "example": examples,
        }
    return {
        "score": round(killed / len(mutants), 6) if mutants else 0.0,
        "killed": killed,
        "total": len(mutants),
        "reference_false_positives": false_positives,
        "graders": list(CRITERIA),
        "mutants": report,
    }


# ---------------------------------------------------------------- calibration with the seeded simulator


def calibration(
    bench: Bench, cases: Sequence[Case], *, noise: float, trials: int, seed: int, replicates: int
) -> dict[str, Any]:
    """The simulator slips (drops one eligible change) with a known probability on a case with two or more
    eligible changes, so the true task-completion mean and pass^k are known in closed form. The run measures
    them through the real runtime and graders; the simulation measures how often the intervals cover the truth."""
    v = run_variant(bench, cases, reference_factory(noise), name="simulator", trials=trials, seed=seed + 7919)
    q = [1 - noise if len(c.expected.propose) >= 2 else 1.0 for c in cases]
    measured = criteria_stats(v, seed=seed)["task_completion"]
    passes = [cr.passes("task_completion") for cr in v.cases]
    ks = range(1, min(trials, 5) + 1)
    truth_k = {str(k): round(stats.mean([p**k for p in q]), 6) for k in ks}
    est_k = {str(k): round(stats.pass_hat_k(passes, trials, k), 6) for k in ks}
    true_mean = round(stats.mean(q), 6)
    lo, hi = measured["ci95"]
    exposed = [cr for cr, p in zip(v.cases, q, strict=True) if p < 1]
    slips = sum(trials - cr.passes("task_completion") for cr in exposed)
    chances = trials * len(exposed)
    se = (noise * (1 - noise) / chances) ** 0.5 if chances else 0.0
    return {
        "simulator": "the reference policy with seeded slips: each run on a case with two or more eligible changes drops one with probability `noise`",
        "noise": noise,
        "trials": trials,
        "cases": len(cases),
        "truth": {"task_completion_mean": true_mean, "pass_hat_k": truth_k},
        "estimate": {
            "task_completion_mean": measured["mean"],
            "ci95": measured["ci95"],
            "pass_hat_k": est_k,
        },
        "covered": lo - 1e-9 <= true_mean <= hi + 1e-9,
        "realized": {
            "slips": slips,
            "chances": chances,
            "rate": round(slips / chances, 6) if chances else 0.0,
            "z": round((slips / chances - noise) / se, 3) if se else 0.0,
            "note": "a 95% interval misses the truth in one draw out of twenty; z says how unusual this draw's slip rate was",
        },
        "other_criteria_unaffected": all(
            all(cr.all_pass(c) for cr in v.cases) for c in CRITERIA if c != "task_completion"
        ),
        "coverage": stats.coverage_simulation(q, trials, replicates=replicates, seed=seed),
    }


# ---------------------------------------------------------------- baseline


def baseline_diff(cases: list[dict[str, Any]], baseline_path: Path) -> dict[str, Any]:
    if not baseline_path.exists():
        return {
            "path": baseline_path.name,
            "exists": False,
            "regressions": [],
            "improvements": [],
            "compared": 0,
        }
    base = json.loads(baseline_path.read_text())
    before = {c["id"]: c["all_pass"] for c in base.get("cases", [])}
    regressions, improvements = [], []
    compared = 0
    for c in cases:
        old = before.get(c["id"])
        if old is None:
            continue
        compared += 1
        for crit in CRITERIA:
            now_ok = all(c["outcomes"][crit])
            if old.get(crit) and not now_ok:
                regressions.append({"case": c["id"], "criterion": crit})
            elif old.get(crit) is False and now_ok:
                improvements.append({"case": c["id"], "criterion": crit})
    return {
        "path": baseline_path.name,
        "exists": True,
        "fingerprint": base.get("fingerprint"),
        "generated_at": base.get("generated_at"),
        "compared": compared,
        "new_cases": sorted({c["id"] for c in cases} - set(before)),
        "removed_cases": sorted(set(before) - {c["id"] for c in cases}),
        "regressions": regressions,
        "improvements": improvements,
        "method": "exact per-case diff: a seeded offline policy has no run-to-run noise, so any flip is a change",
    }


def baseline_of(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": report["fingerprint"]["combined"],
        "generated_at": report["generated_at"],
        "model": report["model"],
        "cases": [
            {"id": c["id"], "all_pass": {k: all(v) for k, v in c["outcomes"].items()}}
            for c in report["cases"]
        ],
    }


# ---------------------------------------------------------------- the report


def case_rows(v: VariantResult) -> list[dict[str, Any]]:
    rows = []
    for cr in v.cases:
        failures = {
            c: sorted({r for g in cr.grades for r in g[c].reasons})[:6]
            for c in CRITERIA
            if not cr.all_pass(c)
        }
        first = cr.records[0]
        rows.append(
            {
                "id": cr.case.id,
                "title": cr.case.title,
                "tags": cr.case.tags,
                "covers": cr.case.covers,
                "request": cr.case.request,
                "faults": [f.value for f in cr.case.faults],
                "outcomes": cr.outcomes(),
                "passed_all": all(cr.all_pass(c) for c in CRITERIA),
                "statuses": sorted({r.status for r in cr.records}),
                "run": first.summary_line(),
                "metrics": {c: dict(cr.grades[0][c].metrics) for c in CRITERIA},
                "failures": failures,
            }
        )
    return rows


def build_report(
    *,
    reference: VariantResult,
    mutants: dict[str, VariantResult],
    calib: dict[str, Any] | None,
    fingerprint: dict[str, str],
    config: dict[str, Any],
    baseline_path: Path,
    elapsed_s: float,
) -> dict[str, Any]:
    cases = case_rows(reference)
    n = len(reference.cases)
    durations = sorted(r.duration_ms for cr in reference.cases for r in cr.records)
    p95 = durations[min(len(durations) - 1, int(0.95 * len(durations)))] if durations else 0.0
    report: dict[str, Any] = {
        "schema": "countersign.eval-report/1",
        "honesty": HONESTY,
        "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "model": {
            "name": reference.model,
            "provider": "offline" if "offline" in reference.model else "anthropic",
            "simulated": "offline" in reference.model,
        },
        "config": config,
        "trials": reference.trials,
        "n_cases": n,
        "fingerprint": fingerprint,
        "criteria": criteria_stats(reference, seed=int(config.get("seed", 0))),
        "overall": {
            "cases_all_pass": sum(c["passed_all"] for c in cases),
            "cases_all_pass_wilson": list(stats.wilson(sum(c["passed_all"] for c in cases), n)),
        },
        "pass_hat_k": pass_hat(reference),
        "mde": {
            "discordant_flips": stats.mde_flips(0.05),
            "alpha": 0.05,
            "n_cases": n,
            "share_of_cases": round(stats.mde_flips(0.05) / n, 4) if n else None,
            "test": "exact McNemar, two-sided",
            "note": "the smallest paired effect detectable between two models on this dataset; offline regressions are exact diffs instead",
        },
        "baseline": baseline_diff(cases, baseline_path),
        "mutation": mutation_analysis(reference, mutants) if mutants else None,
        "calibration": calib,
        "cases": cases,
        "runs": {
            "total": n * reference.trials
            + sum(len(v.cases) * v.trials for v in mutants.values())
            + (calib["cases"] * calib["trials"] if calib else 0),
            "wall_seconds": round(elapsed_s, 2),
            "p95_trial_ms": round(p95, 1),
        },
    }
    return report


def gate(report: dict[str, Any]) -> list[str]:
    """Why the report should fail CI, if it should."""
    problems = []
    for c, s in report["criteria"].items():
        if s["cases_all_pass"] != report["n_cases"]:
            problems.append(
                f"reference policy fails {c} on {report['n_cases'] - s['cases_all_pass']} case(s)"
            )
    m = report.get("mutation")
    if m:
        if m["reference_false_positives"]:
            problems.append(
                f"{m['reference_false_positives']} grader false positive(s) on the reference policy"
            )
        for name, info in m["mutants"].items():
            if not info["caught_by_expected"]:
                problems.append(f"mutant {name} is not caught by {info['expected_grader']}")
    for r in report["baseline"].get("regressions", []):
        problems.append(f"regression against the baseline: {r['case']} {r['criterion']}")
    cal = report.get("calibration")
    if cal:
        cov = cal["coverage"]
        floor = cov["nominal"] - 3 * cov["monte_carlo_se"]
        if cov["mean_interval_coverage"] < floor:
            problems.append(
                f"the mean interval covers the truth in {cov['mean_interval_coverage']:.1%} of replicates"
            )
        if not cal["other_criteria_unaffected"]:
            problems.append("the simulator's slips leaked into criteria other than task completion")
    return problems
