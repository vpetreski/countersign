"""The tool registry against the real estate: validation errors go back to the model, evidence is recorded."""

from __future__ import annotations

from typing import Any

import pytest

from countersign.agent.faults import FaultPlan
from countersign.agent.state import Budgets, RunState
from countersign.data.estate import ESTATE
from countersign.data.pricing import PriceCatalog
from countersign.policy.engine import default_policy
from countersign.store.db import Database
from countersign.tools.catalog import WITHHELD, ToolContext, registry
from tests.conftest import FIXTURES, GROUND_TRUTH

pytestmark = pytest.mark.integration
REG = registry()


@pytest.fixture
def ctx(ingested: Database) -> ToolContext:
    with ingested.session() as s:
        estate = ESTATE.view(s, None)
    state = RunState(
        budgets=Budgets(
            max_model_turns=9,
            max_tool_calls=9,
            max_input_tokens=9,
            max_wall_ms=9,
            max_plan_revisions=1,
            simulated_tokens=True,
        )
    )
    return ToolContext(
        state=state,
        estate=estate,
        catalog=PriceCatalog(FIXTURES),
        live_prices=False,
        policy=default_policy(),
        faults=FaultPlan([], {}),
        turn=1,
    )


def call(ctx: ToolContext, name: str, **args: Any) -> Any:
    return REG.call(ctx, name, args, timeout_s=5)


def test_every_tool_has_a_closed_input_schema() -> None:
    for d in REG.definitions():
        assert d.input_schema["type"] == "object" and d.input_schema.get("additionalProperties") is False, (
            d.name
        )


def test_invalid_input_is_returned_to_the_model_as_a_correctable_error(ctx: ToolContext) -> None:
    out = call(ctx, "find_waste", categories=["idle"])
    assert not out.ok and out.error_kind == "validation" and out.retryable
    assert out.output["problems"][0]["field"].startswith("categories")
    assert not call(ctx, "get_utilization", ref="i-0c4f9e2a7b1d30001", extra=1).ok
    unknown = call(ctx, "drop_database")
    assert unknown.error_kind == "unknown_tool" and "find_waste" in unknown.output["available"]
    assert (
        call(ctx, "submit_change_plan", summary="x", actions=[]).error_kind == "unknown_tool"
    )  # control tools are the runtime's


def test_each_fact_gets_an_evidence_id_about_its_resource(ctx: ToolContext) -> None:
    out = call(ctx, "get_utilization", ref="i-0c4f9e2a7b1d30001")
    assert out.ok and out.output["verdict"] == "idle"
    ev = ctx.state.evidence[out.output["evidence_id"]]
    assert ev.ref == "i-0c4f9e2a7b1d30001" and ev.tool == "get_utilization"
    assert not call(ctx, "get_utilization", ref="vol-0c4f9e2a7b1d30001").ok  # disks have no CPU
    assert not call(ctx, "get_resource", ref="i-doesnotexist").ok


def test_instruction_like_metadata_is_withheld_from_the_model(ctx: ToolContext) -> None:
    """FM-02: the model sees the flag, never the planted text."""
    ref = GROUND_TRUTH["planted_defects"]["injection_in_tag"]
    resource = call(ctx, "get_resource", ref=ref).output
    assert resource["tags"] == {"withheld": WITHHELD} and resource["name"] == ref
    finding = next(f for f in call(ctx, "find_waste").output["findings"] if f["ref"] == ref)
    assert finding["untrusted_text_withheld"] and not finding["eligible"]


def test_filters_narrow_the_findings(ctx: ToolContext) -> None:
    out = call(ctx, "find_waste", categories=["unused_ip"], providers=["azure"]).output
    assert out["count"] >= 1 and {f["provider"] for f in out["findings"]} == {"azure"}
    assert {f["category"] for f in out["findings"]} == {"unused_ip"}
    eligible_only = call(ctx, "find_waste", include_blocked=False).output["findings"]
    assert eligible_only and all(f["eligible"] for f in eligible_only)


def test_check_policy_reports_violations_per_action(ctx: ToolContext) -> None:
    prod = next(
        f
        for f in call(ctx, "find_waste", categories=["idle_vm"]).output["findings"]
        if f["environment"] == "prod"
    )
    out = call(
        ctx,
        "check_policy",
        actions=[
            {
                "ref": prod["ref"],
                "operation": "stop_vm",
                "rationale": "x",
                "evidence_ids": [prod["evidence_id"]],
            }
        ],
    )
    assert out.ok and not out.output["allowed"]
    assert "P-001" in {v["rule"] for v in out.output["actions"][0]["violations"]}
