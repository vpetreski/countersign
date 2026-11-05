"""The analyzer against every planted scenario (fixtures/README.md): the arithmetic the agent relies on."""

from __future__ import annotations

import pytest

from countersign.data.estate import ESTATE, price_fn
from countersign.data.pricing import PriceCatalog
from countersign.domain.analysis import Category, Finding, Operation
from countersign.store.db import Database
from tests.conftest import FIXTURES, GROUND_TRUTH

pytestmark = pytest.mark.integration
SCEN = GROUND_TRUTH["scenarios"]


@pytest.fixture
def findings(ingested: Database) -> dict[str, Finding]:
    with ingested.session() as s:
        estate = ESTATE.view(s, None)
    return {f.ref: f for f in estate.findings(price_fn(PriceCatalog(FIXTURES), live=False))}


def test_idle_non_production_vms_are_eligible_stops(findings: dict[str, Finding]) -> None:
    for ref in SCEN["idle-dev-vm"] + SCEN["idle-staging-vm"]:
        f = findings[ref]
        assert (f.category, f.operation, f.blockers) == (Category.IDLE_VM, Operation.STOP_VM, ())
        assert f.est_monthly_savings_usd and f.est_monthly_savings_usd > 0
        assert f.utilization and f.utilization.p95_daily_avg is not None and f.utilization.p95_daily_avg <= 5


@pytest.mark.parametrize(
    ("scenario", "blocker"),
    [
        ("idle-prod-vm", "production"),
        ("protected-vm", "protected:"),
        ("injection-vm", "suspicious_content"),
        ("new-vm", "insufficient_history"),
        ("gappy-metrics-vm", "insufficient_coverage"),
        ("unattached-prod-disk", "production"),
        ("protected-disk", "protected:"),
        ("recent-unattached-disk", "grace_period"),
        ("unused-prod-ip", "production"),
    ],
)
def test_blocked_scenarios_are_reported_but_never_eligible(
    findings: dict[str, Finding], scenario: str, blocker: str
) -> None:
    for ref in SCEN[scenario]:
        f = findings[ref]
        assert not f.eligible
        assert any(b.startswith(blocker) for b in f.blockers), (ref, f.blockers)


def test_stopped_but_billed_vm_is_a_conflict_with_no_operation(findings: dict[str, Finding]) -> None:
    f = findings[SCEN["conflict-stopped-billed"][0]]
    assert (f.category, f.operation, f.blockers) == (Category.STATE_CONFLICT, None, ("conflicting_sources",))


def test_oversized_vms_get_a_cheaper_target(findings: dict[str, Finding]) -> None:
    for ref in SCEN["oversized-vm"]:
        f = findings[ref]
        assert f.category is Category.OVERSIZED_VM and f.eligible
        assert (
            f.target_sku
            and f.est_monthly_savings_usd
            and 0 < f.est_monthly_savings_usd < (f.monthly_cost_usd or 0)
        )


def test_unattached_disks_and_unused_ips_are_eligible(findings: dict[str, Finding]) -> None:
    for ref in SCEN["unattached-disk"]:
        assert (findings[ref].category, findings[ref].eligible) == (Category.UNATTACHED_DISK, True)
    for ref in SCEN["unused-ip"]:
        assert (findings[ref].category, findings[ref].eligible) == (Category.UNUSED_IP, True)


def test_unknown_owner_is_a_warning_not_a_blocker(findings: dict[str, Finding]) -> None:
    f = findings[SCEN["unknown-owner-vm"][0]]
    assert f.eligible and "no_owner_to_notify" in f.warnings


def test_deallocated_vm_has_no_finding(findings: dict[str, Finding]) -> None:
    assert SCEN["deallocated-vm"][0] not in findings


def test_no_finding_outside_the_planted_scenarios(findings: dict[str, Finding]) -> None:
    planted = {ref for refs in SCEN.values() for ref in refs}
    assert set(findings) <= planted, sorted(set(findings) - planted)


def test_price_outage_falls_back_to_billing_history(ingested: Database) -> None:
    with ingested.session() as s:
        estate = ESTATE.view(s, None)
    degraded = {
        f.ref: f for f in estate.findings(price_fn(PriceCatalog(FIXTURES), live=False, unavailable=["aws"]))
    }
    f = degraded[SCEN["idle-dev-vm"][0]]
    assert f.price_source == "billing_history" and f.est_monthly_savings_usd and f.est_monthly_savings_usd > 0
