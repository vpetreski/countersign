"""The dataset agrees with the analyzer before any model is graded against either, and is well formed."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from countersign.data.estate import ESTATE, price_fn
from countersign.data.pricing import PriceCatalog
from countersign.evals import dataset
from countersign.evals.dataset import Case
from countersign.store.db import Database
from tests.conftest import FIXTURES

pytestmark = pytest.mark.integration
CASES = dataset.load()


def _expected_from_analyzer(case: Case, ingested: Database) -> tuple[set[str], set[str]]:
    with ingested.session() as s:
        estate = ESTATE.view(s, None)
    down = ["all"] if "pricing_unavailable" in case.faults else []
    findings = estate.findings(price_fn(PriceCatalog(FIXTURES), live=False, unavailable=down))
    sc = case.scope
    eligible, blocked = set(), set()
    for f in findings:
        r = estate.by_ref[f.ref]
        if sc.refs and f.ref not in sc.refs:
            continue
        if sc.environments and r.environment.value not in sc.environments:
            continue
        if sc.providers and r.provider.value not in sc.providers:
            continue
        if sc.teams and (r.team or "") not in sc.teams:
            continue
        if f.category.value == "state_conflict":
            if {"idle_vm", "oversized_vm"} & set(sc.categories):
                blocked.add(f.ref)
            continue
        if f.category.value not in sc.categories:
            continue
        if f.operation is not None and (f.est_monthly_savings_usd or 0) < sc.min_savings:
            continue
        (eligible if f.eligible else blocked).add(f.ref)
    return eligible, blocked


def test_the_dataset_is_large_enough_and_well_formed() -> None:
    assert len(CASES) >= 24
    assert len({c.id for c in CASES}) == len(CASES)
    assert all(c.request.strip() and c.expected.final_status for c in CASES)
    assert sum(bool(c.faults) for c in CASES) >= 6
    assert sum(not c.expected.propose for c in CASES) >= 6  # cases where the right answer is no change


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_expected_changes_match_the_analyzer(case: Case, ingested: Database) -> None:
    eligible, blocked = _expected_from_analyzer(case, ingested)
    assert set(case.expected.propose) == eligible
    assert set(case.expected.explain) <= blocked
    assert not set(case.expected.never) & eligible


def test_every_referenced_resource_exists(ingested: Database) -> None:
    with ingested.session() as s:
        refs = set(ESTATE.view(s, None).by_ref)
    for c in CASES:
        named = (
            set(c.expected.propose)
            | set(c.expected.never)
            | set(c.expected.explain)
            | set(c.scope.refs or [])
        )
        assert named <= refs, (c.id, named - refs)


def test_case_files_are_named_after_their_ids_and_unknown_ids_are_refused(tmp_path: Path) -> None:
    src = next(dataset.DATASET_DIR.glob("*.yaml"))
    (tmp_path / "wrong-name.yaml").write_text(src.read_text())
    with pytest.raises(ValueError, match="file name"):
        dataset.load(tmp_path)
    with pytest.raises(ValueError, match="unknown case ids"):
        dataset.load(only=["c99-nope"])
    assert [c.id for c in dataset.load(only=[CASES[0].id])] == [CASES[0].id]


def test_failure_mode_ids_are_well_formed_and_documented() -> None:
    with pytest.raises(ValueError):
        Case.model_validate({**CASES[0].model_dump(), "covers": ["FM-1"]})
    doc = dataset.BACKEND_ROOT.parent / "docs" / "failure-modes.md"
    if doc.exists():
        documented = set(re.findall(r"\bFM-\d{2}\b", doc.read_text()))
        used = {fm for c in CASES for fm in c.covers}
        assert used <= documented, used - documented
