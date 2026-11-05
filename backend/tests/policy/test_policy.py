from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from countersign.data.estate import ESTATE, price_fn
from countersign.data.ingest import ingest
from countersign.data.pricing import PriceCatalog
from countersign.domain.analysis import Finding, Operation
from countersign.domain.resources import Environment, Resource
from countersign.policy.engine import Policy, ProposedAction, Risk, default_policy
from countersign.store.db import Database
from tests.conftest import FIXTURES, NOW, make_settings

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def world() -> tuple[dict[str, Resource], dict[str, Finding]]:
    db = Database("sqlite://")
    db.create_all()
    ESTATE.clear()
    ingest(db, make_settings(), now=NOW)
    with db.session() as s:
        estate = ESTATE.view(s, None)
    findings = {f.ref: f for f in estate.findings(price_fn(PriceCatalog(FIXTURES), live=False))}
    ESTATE.clear()
    return estate.by_ref, findings


def act(f: Finding, ev: str = "ev-01", op: Operation | None = None) -> ProposedAction:
    return ProposedAction(
        ref=f.ref,
        operation=op or f.operation or Operation.STOP_VM,
        target_sku=f.target_sku,
        rationale="test",
        evidence_ids=[ev],
    )


def judge(
    policy: Policy, world: tuple[dict[str, Resource], dict[str, Finding]], actions: list[ProposedAction]
) -> object:
    resources, findings = world
    evidence = {f"ev-{i:02d}": a.ref for i, a in enumerate(actions, start=1)}
    actions = [a.model_copy(update={"evidence_ids": [f"ev-{i:02d}"]}) for i, a in enumerate(actions, start=1)]
    return policy.evaluate(
        actions,
        resolve={a.ref: resources[a.ref] for a in actions if a.ref in resources},
        findings=findings,
        evidence_refs=evidence,
        savings={a.ref: findings[a.ref].est_monthly_savings_usd or 0 for a in actions if a.ref in findings},
    )


def test_eligible_findings_pass_every_rule(world: tuple[dict[str, Resource], dict[str, Finding]]) -> None:
    eligible = [f for f in world[1].values() if f.eligible]
    verdict = judge(default_policy(), world, [act(f) for f in eligible])
    assert verdict.allowed  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("ref", "rule"),
    [
        ("i-0c4f9e2a7b1d30004", "P-001"),
        ("i-0c4f9e2a7b1d30005", "P-002"),
        ("i-0c4f9e2a7b1d30006", "P-003"),
        ("i-0c4f9e2a7b1d30008", "P-005"),
        ("vol-0c4f9e2a7b1d30005", "P-005"),
        ("i-0c4f9e2a7b1d30007", "P-004"),
    ],
)
def test_each_blocked_scenario_names_its_rule(
    world: tuple[dict[str, Resource], dict[str, Finding]], ref: str, rule: str
) -> None:
    f = world[1][ref]
    verdict = judge(default_policy(), world, [act(f)])
    violations = {c.rule for c in verdict.actions[0].violations}  # type: ignore[attr-defined]
    assert rule in violations


def test_unknown_resources_wrong_operations_and_duplicates_are_refused(
    world: tuple[dict[str, Resource], dict[str, Finding]],
) -> None:
    f = next(x for x in world[1].values() if x.eligible and x.operation is Operation.STOP_VM)
    policy = default_policy()
    ghost = ProposedAction(
        ref="i-0000000000000dead", operation=Operation.STOP_VM, rationale="x", evidence_ids=["ev-01"]
    )
    assert [c.rule for c in judge(policy, world, [ghost]).actions[0].violations] == ["P-008"]  # type: ignore[attr-defined]
    wrong = judge(policy, world, [act(f, op=Operation.DELETE_DISK)])
    assert {"P-008", "P-004"} <= {c.rule for c in wrong.actions[0].violations}  # type: ignore[attr-defined]
    dup = judge(policy, world, [act(f), act(f)])
    assert dup.actions[0].allowed and not dup.actions[1].allowed  # type: ignore[attr-defined]


def test_risk_and_approval_routing(world: tuple[dict[str, Resource], dict[str, Finding]]) -> None:
    policy = default_policy()
    assert policy.risk_of(Operation.STOP_VM, Environment.DEV) is Risk.LOW
    assert policy.risk_of(Operation.STOP_VM, Environment.STAGING) is Risk.MEDIUM
    assert policy.risk_of(Operation.DELETE_DISK, Environment.STAGING) is Risk.HIGH
    stops = [
        f
        for f in world[1].values()
        if f.eligible and f.operation is Operation.STOP_VM and f.environment == "dev"
    ]
    one = judge(policy, world, [act(stops[0])])
    assert (one.risk, one.approvals_required) == (Risk.LOW, 1)  # type: ignore[attr-defined]
    disk = next(f for f in world[1].values() if f.eligible and f.operation is Operation.DELETE_DISK)
    assert judge(policy, world, [act(disk)]).approvals_required == 2  # type: ignore[attr-defined]


def test_large_impact_needs_two_approvers_whatever_the_risk(
    world: tuple[dict[str, Resource], dict[str, Finding]],
) -> None:
    low_bar = Policy(**{**default_policy().__dict__, "two_approvers_at": 100.0})
    stops = [
        f
        for f in world[1].values()
        if f.eligible and f.operation is Operation.STOP_VM and f.environment == "dev"
    ]
    assert judge(low_bar, world, [act(f) for f in stops]).approvals_required == 2  # type: ignore[attr-defined]


def test_plan_size_limit(world: tuple[dict[str, Resource], dict[str, Finding]]) -> None:
    tiny = Policy(**{**default_policy().__dict__, "max_actions": 1})
    eligible = [f for f in world[1].values() if f.eligible][:2]
    verdict = judge(tiny, world, [act(f) for f in eligible])
    assert not verdict.allowed and not verdict.plan_checks[0].passed  # type: ignore[attr-defined]


@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(data=st.data())
def test_no_combination_of_actions_lets_a_blocked_resource_through(
    world: tuple[dict[str, Resource], dict[str, Finding]], data: st.DataObject
) -> None:
    """Property: whatever the model proposes, an allowed action is never production, protected, flagged, unknown or ungrounded."""
    resources, findings = world
    refs = data.draw(st.lists(st.sampled_from(sorted(resources)), min_size=1, max_size=8))
    ops = data.draw(st.lists(st.sampled_from(list(Operation)), min_size=len(refs), max_size=len(refs)))
    actions = [
        ProposedAction(
            ref=r,
            operation=o,
            target_sku=(findings[r].target_sku if r in findings else None),
            rationale="p",
            evidence_ids=["ev-01"],
        )
        for r, o in zip(refs, ops, strict=True)
    ]
    verdict = judge(default_policy(), world, actions)
    for a, v in zip(actions, verdict.actions, strict=True):  # type: ignore[attr-defined]
        if not v.allowed:
            continue
        r, f = resources[a.ref], findings.get(a.ref)
        assert r.environment not in (Environment.PROD, Environment.UNKNOWN)
        assert "suspicious_content" not in r.flags
        assert f is not None and f.eligible and f.operation is a.operation
