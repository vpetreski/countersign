"""Data validation: every planted defect in the demo estate is caught, counted and explained.

The defects are listed in fixtures/README.md."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from countersign.data.estate import ESTATE, data_checks, quality_results, quarantined
from countersign.data.ingest import data_as_of, ingest, load_batches
from countersign.data.quality import lag_hours, run_checks
from countersign.domain.resources import Environment, Kind, State
from countersign.security.screening import REDACTED, contains_secret
from countersign.settings import Settings
from countersign.store.db import Database
from countersign.store.schema import CostLineRow, QuarantineRow, ResourceRow, SourceSnapshot
from tests.conftest import GROUND_TRUTH, NOW, Clock, make_api, make_settings

pytestmark = pytest.mark.data_validation
DEFECTS = GROUND_TRUTH["planted_defects"]
AWS_EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # AWS's documented example key pair, split so no scanner trips
AWS_EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"


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


def test_a_secret_in_a_quarantined_record_is_never_stored_or_shown(fixtures_copy: Path) -> None:
    """FM-03 for refused records: a record is quarantined before its tags are screened, so its raw text and its
    reason are masked before they are stored; the Data view, open to every persona, shows the mask."""
    page = fixtures_copy / "sources/aws/444455556666/ec2_describe_instances.page2.json"
    body = json.loads(page.read_text())
    broken = body["Reservations"][12]["Instances"][0]
    assert broken["InstanceId"] == DEFECTS["aws_invalid_launch_time"]
    broken["LaunchTime"] = AWS_EXAMPLE_KEY  # the reason quotes the bad value
    broken["Tags"].append({"Key": "Notes", "Value": f"aws_secret_access_key={AWS_EXAMPLE_SECRET}"})
    page.write_text(json.dumps(body))

    api = make_api(Clock(NOW), fixtures_dir=fixtures_copy)
    try:
        with api.services.db.session() as s:
            rows = list(s.scalars(select(QuarantineRow)))
        planted = next(r for r in rows if DEFECTS["aws_invalid_launch_time"] in r.raw)
        assert REDACTED in planted.raw and REDACTED in planted.reason
        stored = " ".join(f"{r.reason} {r.raw}" for r in rows)
        assert AWS_EXAMPLE_KEY not in stored and AWS_EXAMPLE_SECRET not in stored
        assert not contains_secret(stored)
        viewer = api.workspace()["viewer"]
        resp = api.client.get("/api/sources", headers=api.auth(viewer))
        assert resp.status_code == 200 and len(resp.json()["quarantine"]) == 3
        assert AWS_EXAMPLE_KEY not in resp.text and AWS_EXAMPLE_SECRET not in resp.text
    finally:
        ESTATE.clear()
        api.services.db.engine.dispose()


def test_attached_disks_inherit_their_vm_allocation(batches: list) -> None:
    disks = [r for r in _all(batches, "resources") if r.kind is Kind.DISK and r.state is State.ATTACHED]
    inherited = [d for d in disks if "inherited_tags" in d.flags]
    assert inherited
    assert all(d.environment is not Environment.UNKNOWN for d in inherited)


def checks_of(batches: list, metrics_hours: float = 54, billing_hours: float = 54) -> dict:
    as_of = data_as_of(make_settings())
    found = run_checks(batches, as_of, metrics_hours=metrics_hours, billing_hours=billing_hours)
    return {c.check_id: c for c in found}


def test_reconciliation_finds_the_stopped_but_billed_vm(batches: list) -> None:
    checks = checks_of(batches)
    conflict = checks["reconciliation.stopped_vms_unbilled"]
    assert conflict.status == "warn"
    assert DEFECTS["stopped_but_billed"] in conflict.detail
    assert checks["reconciliation.single_currency"].status == "pass"
    assert checks["uniqueness.resource_id"].status == "warn"  # duplicates were removed at the source
    assert checks["schema.billing.quarantine"].status == "warn"


def test_freshness_fails_past_its_service_level(batches: list) -> None:
    """FM-21: utilization or billing that trails the sources' as-of time by more than its level fails."""
    checks = checks_of(batches)
    assert checks["freshness.metrics"].status == checks["freshness.billing"].status == "pass"
    assert "newest day 2026-10-31, 0 h" in checks["freshness.metrics"].observed
    as_of = data_as_of(make_settings())
    assert lag_hours(as_of, dt.date(2026, 10, 29)) == 48 and lag_hours(as_of, dt.date(2026, 11, 3)) == 0
    strict = checks_of(batches, metrics_hours=-1, billing_hours=-1)
    assert strict["freshness.metrics"].status == strict["freshness.billing"].status == "fail"
    no_metrics = [dataclasses.replace(b, cpu={}) for b in batches]
    assert checks_of(no_metrics)["freshness.metrics"].observed == "no data"
    assert checks_of(no_metrics)["freshness.metrics"].status == "fail"


def test_a_sync_that_stopped_fails_freshness_when_the_checks_are_read(
    db: Database, settings: Settings
) -> None:
    """FM-21: the sync check is computed when read, so a stored pass cannot hide a cron that stopped."""
    ingest(db, settings, now=NOW)
    with db.session() as s:
        fresh = {c.check_id: c for c in data_checks(s, NOW + dt.timedelta(hours=29), 30)}
        stale = {c.check_id: c for c in data_checks(s, NOW + dt.timedelta(hours=31), 30)}
    assert fresh["freshness.sync"].status == "pass" and fresh["freshness.sync"].observed.startswith("29.0 h")
    assert stale["freshness.sync"].status == "fail"
    assert {k for k, c in stale.items() if c.status == "fail"} == {"freshness.sync"}
    empty = Database("sqlite://")
    empty.create_all()
    with empty.session() as s:
        assert [(c.check_id, c.status, c.observed) for c in data_checks(s, NOW, 30)] == [
            ("freshness.sync", "fail", "never synced")
        ]
    empty.engine.dispose()


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
