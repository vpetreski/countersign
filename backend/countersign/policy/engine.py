"""Policy-as-code: rules, risk and approval routing for proposed changes. Deterministic and side-effect
free, so the same input always yields the same verdict, and the verdict is re-checked at every boundary."""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from countersign.domain.analysis import Finding, Operation
from countersign.domain.resources import Environment, Kind, Resource, State
from countersign.domain.rules import protected_by

POLICY_FILE = Path(__file__).with_name("policy.yaml")


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_ORDER = [Risk.LOW, Risk.MEDIUM, Risk.HIGH]
EXPECTED_STATE = {
    Operation.STOP_VM: (Kind.VM, State.RUNNING),
    Operation.RESIZE_VM: (Kind.VM, State.RUNNING),
    Operation.DELETE_DISK: (Kind.DISK, State.UNATTACHED),
    Operation.RELEASE_IP: (Kind.PUBLIC_IP, State.UNASSOCIATED),
}
REVERSIBLE = {
    Operation.STOP_VM: True,
    Operation.RESIZE_VM: True,
    Operation.DELETE_DISK: True,
    Operation.RELEASE_IP: False,
}
DATA_BLOCKERS = ("insufficient_history", "insufficient_coverage", "grace_period", "conflicting_sources")


class ProposedAction(BaseModel):
    """What the model may propose. Everything else about an action is derived by code."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(
        min_length=1,
        max_length=200,
        description="Resource ref from find_waste, e.g. i-0abc... or vm-dev-etl-01",
    )
    operation: Operation
    target_sku: str | None = Field(
        default=None, max_length=64, description="Required for resize_vm: the finding's target"
    )
    rationale: str = Field(
        min_length=1, max_length=600, description="One or two sentences, citing evidence ids"
    )
    evidence_ids: list[str] = Field(
        min_length=1, max_length=12, description="Evidence ids returned by tools in this run"
    )


class NotProposed(BaseModel):
    """A finding the agent reviewed and deliberately left out, with the reason an approver should see."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=200, description="Resource ref from find_waste")
    reason: str = Field(
        min_length=1,
        max_length=300,
        description="Why it is not proposed: a policy or data blocker, a source conflict, untrusted metadata, outside the request",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, max_length=6, description="Evidence ids for the reason"
    )


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    name: str
    passed: bool
    detail: str


class ActionVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    ref: str
    allowed: bool
    risk: Risk | None
    checks: list[Check]

    @property
    def violations(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


class PolicyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: int
    actions: list[ActionVerdict]
    plan_checks: list[Check]
    risk: Risk | None
    approvals_required: int

    @property
    def allowed(self) -> bool:
        return all(a.allowed for a in self.actions) and all(c.passed for c in self.plan_checks)


@dataclass(frozen=True)
class Policy:
    version: int
    max_actions: int
    rules: Mapping[str, Mapping[str, str]]
    operation_risk: Mapping[str, Risk]
    raise_in: frozenset[str]
    approvals: Mapping[Risk, int]
    two_approvers_at: float
    separation_of_duties: bool

    @classmethod
    def load(cls, path: Path = POLICY_FILE) -> Policy:
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        approvals = raw["approvals"]
        return cls(
            version=int(raw["version"]),
            max_actions=int(raw["max_actions_per_plan"]),
            rules={r["id"]: r for r in raw["rules"]},
            operation_risk={k: Risk(v) for k, v in raw["risk"]["operations"].items()},
            raise_in=frozenset(raw["risk"]["raise_one_level_in"]),
            approvals={Risk(k): int(approvals[k]) for k in ("low", "medium", "high")},
            two_approvers_at=float(approvals["two_approvers_at_monthly_usd"]),
            separation_of_duties=bool(approvals["separation_of_duties"]),
        )

    def _check(self, rule: str, passed: bool, detail: str) -> Check:
        return Check(rule=rule, name=self.rules[rule]["name"], passed=passed, detail=detail)

    def risk_of(self, operation: Operation, environment: Environment) -> Risk:
        base = self.operation_risk[operation.value]
        if environment.value in self.raise_in:
            return _ORDER[min(len(_ORDER) - 1, _ORDER.index(base) + 1)]
        return base

    def evaluate(
        self,
        actions: Sequence[ProposedAction],
        *,
        resolve: Mapping[str, Resource],
        findings: Mapping[str, Finding],
        evidence_refs: Mapping[str, str],
        savings: Mapping[str, float] | None = None,
    ) -> PolicyVerdict:
        """`resolve` maps refs to resources; `findings` maps refs to the analyzer's findings; `evidence_refs`
        maps each evidence id this run retrieved to the resource ref it is about ("" for estate-wide facts)."""
        verdicts: list[ActionVerdict] = []
        seen: set[str] = set()
        for index, action in enumerate(actions):
            checks: list[Check] = []
            r = resolve.get(action.ref)
            if r is None:
                checks.append(self._check("P-008", False, f"{action.ref} is not in the estate"))
                verdicts.append(
                    ActionVerdict(index=index, ref=action.ref, allowed=False, risk=None, checks=checks)
                )
                continue
            kind, state = EXPECTED_STATE[action.operation]
            fits = r.kind is kind and r.state is state
            checks.append(
                self._check(
                    "P-008",
                    fits,
                    f"{r.kind}/{r.state}" + ("" if fits else f"; {action.operation} needs {kind}/{state}"),
                )
            )
            checks.append(
                self._check("P-001", r.environment is not Environment.PROD, f"environment {r.environment}")
            )
            tag = protected_by(r.tags)
            checks.append(
                self._check("P-002", tag is None, f"protected by {tag}" if tag else "no protection tag")
            )
            flagged = "suspicious_content" in r.flags
            checks.append(
                self._check(
                    "P-003",
                    not flagged,
                    "metadata flagged as instruction-like" if flagged else "metadata clean",
                )
            )
            checks.append(
                self._check("P-006", r.environment is not Environment.UNKNOWN, f"environment {r.environment}")
            )
            f = findings.get(r.ref)
            data_blockers = [b for b in (f.blockers if f else ()) if b in DATA_BLOCKERS]
            checks.append(
                self._check("P-005", not data_blockers, ", ".join(data_blockers) or "sufficient data")
            )
            grounded, why = _grounded(action, r, f, evidence_refs)
            checks.append(self._check("P-004", grounded, why))
            dup = r.ref in seen
            seen.add(r.ref)
            checks.append(
                self._check(
                    "P-007",
                    not dup,
                    "second action on the same resource" if dup else "one action per resource",
                )
            )
            allowed = all(c.passed for c in checks)
            verdicts.append(
                ActionVerdict(
                    index=index,
                    ref=r.ref,
                    allowed=allowed,
                    risk=self.risk_of(action.operation, r.environment),
                    checks=checks,
                )
            )
        plan_checks = [
            self._check(
                "P-007",
                len(actions) <= self.max_actions,
                f"{len(actions)} of at most {self.max_actions} actions",
            )
        ]
        risks = [v.risk for v in verdicts if v.risk is not None and v.allowed]
        risk = max(risks, key=_ORDER.index) if risks else None
        total = sum(
            (savings or {}).get(a.ref, 0.0) for a, v in zip(actions, verdicts, strict=True) if v.allowed
        )
        approvals = self.approvals[risk] if risk else 0
        if risk and total >= self.two_approvers_at:
            approvals = max(approvals, 2)
        return PolicyVerdict(
            policy_version=self.version,
            actions=verdicts,
            plan_checks=plan_checks,
            risk=risk,
            approvals_required=approvals,
        )


def _grounded(
    action: ProposedAction, r: Resource, f: Finding | None, evidence_refs: Mapping[str, str]
) -> tuple[bool, str]:
    if f is None or f.operation is None:
        return False, "no finding supports a change to this resource"
    if f.operation is not action.operation:
        return False, f"the finding supports {f.operation}, not {action.operation}"
    if action.operation is Operation.RESIZE_VM and action.target_sku != f.target_sku:
        return False, f"the finding's target is {f.target_sku}, not {action.target_sku}"
    unknown = [e for e in action.evidence_ids if e not in evidence_refs]
    if unknown:
        return False, f"evidence not retrieved in this run: {', '.join(unknown)}"
    if not any(evidence_refs[e] == r.ref for e in action.evidence_ids):
        return False, "none of the cited evidence is about this resource"
    return True, f"finding {f.category} with {len(action.evidence_ids)} cited evidence item(s)"


@functools.cache
def default_policy() -> Policy:
    return Policy.load()
