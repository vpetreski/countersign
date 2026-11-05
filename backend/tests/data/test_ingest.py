"""Data validation: every planted defect in the demo estate is caught, counted and explained.

The defects are listed in fixtures/README.md."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from countersign.data.estate import ESTATE, quality_results, quarantined
from countersign.data.ingest import ingest, load_batches
from countersign.data.quality import run_checks
from countersign.domain.resources import Environment, Kind, State
from countersign.security.screening import REDACTED, contains_secret
from countersign.settings import Settings
from countersign.store.db import Database
from countersign.store.schema import CostLineRow, ResourceRow, SourceSnapshot
from tests.conftest import GROUND_TRUTH, NOW, make_settings

pytestmark = pytest.mark.data_validation
DEFECTS = GROUND_TRUTH["planted_defects"]


@pytest.fixture(scope="module")
def batches() -> list:
    return load_batches(make_settings())


def _all(batches: list, attr: str) -> list:
    return [x for b in batches for x in getattr(b, attr)]


def test_schema_violations_are_quarantined_with_reasons(batches: list) -> None:
    """FM-01: malformed source records never reach the model; each is kept with its reason."""
    q = _all(batches, "quarantined")
    assert len(q) == 3
    reasons = " | ".join(f"{r.source_ref} {r.reason} {r.raw}" for r in q)
    assert DEFECTS["aws_invalid_launch_time"] in reasons
    assert DEFECTS["azure_vm_missing_size"] in reasons
    assert any(r.source == "billing" and "BilledCost" in r.reason for r in q)


def test_duplicates_are_removed_and_counted(batches: list) -> None:
    dupes = [w for w in _all(batches, "warnings") if w.rule == "uniqueness"]
    assert len(dupes) == 2  # one volume on two pages, one exact duplicate billing row
    ids = [r.id.lower() for r in _all(batches, "resources")]
    assert len(ids) == len(set(ids))


def test_transformations_normalize_and_report(batches: list) -> None:
    changed = [w for w in _all(batches, "warnings") if w.rule == "transformation"]
    details = " ".join(w.detail for w in changed)
    assert len(changed) == 2
    assert "'usd'" in details
    assert {c.currency for c in _all(batches, "costs")} == {"USD"}
    assert all(c.day.year == 2026 for c in _all(batches, "costs"))


def test_environment_tags_collapse_to_one_vocabulary(batches: list) -> None:
    resources = _all(batches, "resources")
    assert {r.environment for r in resources} <= set(Environment)
    assert all(set(r.tags) == {k.lower() for k in r.tags} for r in resources)
    assert sum(r.environment is Environment.PROD for r in resources) >= 6


def test_injection_is_flagged_and_secrets_never_stored(batches: list) -> None:
    """FM-02 and FM-03: untrusted text is screened at ingest, before any tool, model or screen sees it."""
    resources = {r.ref: r for r in _all(batches, "resources")}
    assert "suspicious_content" in resources[DEFECTS["injection_in_tag"]].flags
    secret_vm = resources[DEFECTS["secret_in_tag"]]
    assert "secret_redacted" in secret_vm.flags
    assert any(REDACTED in v for v in secret_vm.tags.values())
    assert not any(contains_secret(v) for r in resources.values() for v in r.tags.values())


def test_attached_disks_inherit_their_vm_allocation(batches: list) -> None:
    disks = [r for r in _all(batches, "resources") if r.kind is Kind.DISK and r.state is State.ATTACHED]
    inherited = [d for d in disks if "inherited_tags" in d.flags]
    assert inherited
    assert all(d.environment is not Environment.UNKNOWN for d in inherited)


def test_reconciliation_finds_the_stopped_but_billed_vm(batches: list) -> None:
    checks = {c.check_id: c for c in run_checks(batches, fetched_at=NOW, now=NOW, freshness_hours=30)}
    conflict = checks["reconciliation.stopped_vms_unbilled"]
    assert conflict.status == "warn"
    assert DEFECTS["stopped_but_billed"] in conflict.detail
    assert checks["reconciliation.single_currency"].status == "pass"
    assert checks["uniqueness.resource_id"].status == "warn"  # duplicates were removed at the source
    assert checks["schema.billing.quarantine"].status == "warn"


def test_freshness_fails_past_its_service_level(batches: list) -> None:
    """FM-21: data older than its service level fails the freshness check (the run would be told it is stale)."""
    stale = NOW + dt.timedelta(hours=31)
    checks = {c.check_id: c for c in run_checks(batches, fetched_at=NOW, now=stale, freshness_hours=30)}
    assert checks["freshness.inventory"].status == "fail"


def test_october_actuals_and_monthly_run_rate_differ_by_design(batches: list) -> None:
    """October has 744 hours; monthly estimates use the 730-hour convention (fixtures/README.md)."""
    resources = {r.ref: r for r in _all(batches, "resources")}
    vm = resources["i-0c4f9e2a7b1d30001"]
    lines = [
        c
        for c in _all(batches, "costs")
        if c.resource_key == vm.billing_key and c.service_category == "Compute"
    ]
    october = sum(c.billed_cost for c in lines)
    hourly = october / 744
    assert len({c.day for c in lines}) == 31
    assert hourly * 730 == pytest.approx(october * 730 / 744)
    assert october > hourly * 730


def test_ingest_is_idempotent(db: Database, settings: Settings) -> None:
    first = ingest(db, settings, now=NOW)
    second = ingest(db, settings, now=NOW + dt.timedelta(hours=1))
    assert first.changed and not second.changed
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ResourceRow)) == first.resources
        assert s.scalar(select(func.count()).select_from(CostLineRow)) == first.cost_lines
        fetched = {
            r.fetched_at.replace(tzinfo=dt.UTC)
            for r in s.scalars(select(SourceSnapshot).where(SourceSnapshot.current.is_(True)))
        }
        assert fetched == {NOW + dt.timedelta(hours=1)}  # only freshness moved
        assert len(quarantined(s)) == first.quarantined == 3
        assert {r.status for r in quality_results(s)} <= {"pass", "warn"}


def test_changed_source_replaces_the_estate_in_one_transaction(db: Database, fixtures_copy: Path) -> None:
    settings = make_settings(fixtures_dir=fixtures_copy)
    ingest(db, settings, now=NOW)
    page = fixtures_copy / "sources/aws/111122223333/ec2_describe_addresses.json"
    body = json.loads(page.read_text())
    body["Addresses"] = body["Addresses"][:-1]
    page.write_text(json.dumps(body))
    report = ingest(db, settings, now=NOW)
    assert report.changed
    with db.session() as s:
        snapshots = list(s.scalars(select(SourceSnapshot)))
        assert sum(r.current for r in snapshots) == 3
        assert s.scalar(select(func.count()).select_from(ResourceRow)) == report.resources
    ESTATE.clear()


def test_estate_view_reads_back_what_was_ingested(ingested: Database) -> None:
    with ingested.session() as s:
        estate = ESTATE.view(s, None)
    assert len(estate.resources) == 149
    assert estate.as_of == dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
    vm = estate.resolve("i-0c4f9e2a7b1d30001")
    assert vm is not None and len(estate.cpu_for(vm)) >= 28
    assert set(estate.monthly_spend()) == {"aws", "azure"}
