"""The evaluation dataset: one YAML file per case in `backend/evals/dataset/`.

A case is a request, the faults injected into its run, what the scripted operator and approvers do, the scope
the request asks for (written by hand: it is the relevance oracle and never comes from the model's own reading
of the request) and the expected outcome (written from the fixtures' planted scenarios: the recall and safety
oracles). `tests/evals/test_dataset.py` checks every expected set against the deterministic analyzer, so the
dataset and the analyzer must agree before any model is graded against either.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from countersign.agent.faults import Fault
from countersign.settings import BACKEND_ROOT

DATASET_DIR = BACKEND_ROOT / "evals" / "dataset"
Category = Literal["idle_vm", "oversized_vm", "unattached_disk", "unused_ip"]
CATEGORY_OF_OPERATION: dict[str, str] = {
    "stop_vm": "idle_vm",
    "resize_vm": "oversized_vm",
    "delete_disk": "unattached_disk",
    "release_ip": "unused_ip",
}
TERMINAL_STATUSES = (
    "completed",
    "completed_with_failures",
    "failed",
    "rejected",
    "cancelled",
    "budget_exhausted",
    "expired",
    "awaiting_approval",
    "awaiting_input",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(_Strict):
    """What the request asks for. A proposal outside it is irrelevant, however much it saves."""

    categories: list[Category]
    environments: list[Literal["prod", "staging", "dev", "test", "sandbox"]] | None = None
    providers: list[Literal["aws", "azure"]] | None = None
    teams: list[str] | None = None
    refs: list[str] | None = None
    min_savings: float = Field(default=0.0, ge=0)


class Approvals(_Strict):
    """What the scripted approvers do once a change request exists."""

    decision: Literal["approve", "reject", "none"] = "approve"
    reject_operations: list[Literal["stop_vm", "resize_vm", "delete_disk", "release_ip"]] = Field(
        default_factory=list, description="Rejected by the first approver; everything else is approved"
    )


class Expected(_Strict):
    final_status: list[Literal[TERMINAL_STATUSES]]  # type: ignore[valid-type]
    propose: list[str] = Field(default_factory=list, description="Exactly what a correct agent proposes")
    never: list[str] = Field(default_factory=list, description="Never in any submitted change request")
    explain: list[str] = Field(default_factory=list, description="Must be listed as reviewed, not proposed")
    ask: bool = Field(default=False, description="The request is ambiguous: the agent must ask first")
    execution_failures: int = Field(default=0, ge=0, description="Approved actions expected to fail (drift)")


class Case(_Strict):
    id: str = Field(pattern=r"^c\d{2}-[a-z0-9-]+$")
    title: str
    tags: list[str]
    covers: list[str] = Field(description="Failure-mode ids from docs/failure-modes.md")
    request: str = Field(min_length=1)
    faults: list[Fault] = Field(default_factory=list)
    operator_answer: str | None = None
    approvals: Approvals = Approvals()
    scope: Scope
    expected: Expected
    notes: str = ""

    @field_validator("covers")
    @classmethod
    def _fm_ids(cls, value: list[str]) -> list[str]:
        bad = [v for v in value if not re.fullmatch(r"FM-\d{2}", v)]
        if bad:
            raise ValueError(f"not failure-mode ids: {bad}")
        return value

    @property
    def plan_expected(self) -> bool:
        return bool(self.expected.propose)


def load(directory: Path = DATASET_DIR, only: list[str] | None = None) -> list[Case]:
    cases = []
    for path in sorted(directory.glob("*.y*ml")):
        case = Case.model_validate(yaml.safe_load(path.read_text("utf-8")))
        if path.stem != case.id:
            raise ValueError(f"{path.name}: the file name must be the case id ({case.id})")
        cases.append(case)
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    if only:
        missing = sorted(set(only) - set(ids))
        if missing:
            raise ValueError(f"unknown case ids: {missing}")
        cases = [c for c in cases if c.id in only]
    return cases


def digest(directory: Path = DATASET_DIR) -> str:
    h = hashlib.sha256()
    for path in sorted(directory.glob("*.y*ml")):
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()
