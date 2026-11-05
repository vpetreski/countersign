"""The tools the model may call. All of them read; none of them changes anything.

Text that came from cloud metadata and was flagged at ingest as instruction-like is withheld from the model
(it sees the flag, not the text), so an injection planted in a tag never reaches the prompt at all.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from countersign.agent.faults import Fault, FaultPlan
from countersign.agent.state import RunState
from countersign.data.estate import Estate, price_fn
from countersign.data.pricing import PriceCatalog, PriceUnavailableError
from countersign.domain.analysis import Category, Finding, summarize
from countersign.domain.resources import Environment, Kind, PriceQuote, Provider, Resource
from countersign.policy.engine import NotProposed, Policy, PolicyVerdict, ProposedAction
from countersign.tools.registry import Tool, ToolFailureError, ToolRegistry, ToolTimeoutError

WITHHELD = "[withheld: instruction-like text detected at ingest; shown to human reviewers only]"


@dataclass
class ToolContext:
    state: RunState
    estate: Estate
    catalog: PriceCatalog
    live_prices: bool
    policy: Policy
    faults: FaultPlan
    turn: int
    timeout_s: float = 4.0
    newly_degraded: list[str] = field(default_factory=list)
    _findings: list[Finding] | None = None

    def price(self) -> Callable[[Resource, str | None], PriceQuote]:
        down = ["all"] if self.faults.is_active(Fault.PRICING_UNAVAILABLE) else []
        return price_fn(self.catalog, live=self.live_prices, unavailable=down)

    def findings(self) -> list[Finding]:
        if self._findings is None:
            self._findings = self.estate.findings(self.price())
        return self._findings

    def finding_map(self) -> dict[str, Finding]:
        return {f.ref: f for f in self.findings()}

    def evidence(self, tool: str, ref: str, kind: str, summary: str) -> str:
        from countersign.agent.state import EvidenceItem  # noqa: PLC0415 - state imports nothing from tools

        ev = EvidenceItem(
            id=self.state.next_evidence_id(), tool=tool, ref=ref, kind=kind, summary=summary, turn=self.turn
        )
        self.state.evidence[ev.id] = ev
        return ev.id

    def degrade(self, reason: str) -> None:
        if self.state.degrade(reason):
            self.newly_degraded.append(reason)

    def resource(self, ref: str) -> Resource:
        r = self.estate.resolve(ref.strip())
        if r is None:
            raise ToolFailureError(f"no resource {ref!r} in the estate; use a ref returned by find_waste")
        return r


def _name(r: Resource) -> str:
    return r.ref if "suspicious_content" in r.flags else r.name


def _tags(r: Resource) -> dict[str, str]:
    return {"withheld": WITHHELD} if "suspicious_content" in r.flags else dict(r.tags)


def _tag_text(r: Resource, value: str | None) -> str | None:
    """A field taken from a tag (team, owner): withheld from a flagged resource like its name and tags."""
    return WITHHELD if value is not None and "suspicious_content" in r.flags else value


# ---------------------------------------------------------------- inputs


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EstateSummaryIn(_In):
    pass


class FindWasteIn(_In):
    categories: list[Category] | None = Field(
        default=None, description="Limit to these categories; omit for all"
    )
    environments: list[Environment] | None = Field(
        default=None, description="Limit to these environments; omit for all"
    )
    providers: list[Provider] | None = Field(default=None, description="Limit to these clouds; omit for both")
    teams: list[str] | None = Field(default=None, description="Limit to these owning teams")
    min_monthly_savings_usd: float = Field(default=0, ge=0, description="Skip findings saving less than this")
    include_blocked: bool = Field(
        default=True, description="Also return findings that policy or data quality blocks"
    )
    limit: int = Field(default=50, ge=1, le=100)


class RefIn(_In):
    ref: str = Field(
        min_length=1, max_length=200, description="Resource ref, e.g. i-0c4f... or vm-dev-etl-01"
    )


class UtilizationIn(RefIn):
    window_days: int = Field(default=31, ge=7, le=31, description="Days of daily CPU statistics to summarize")


class PriceIn(RefIn):
    sku: str | None = Field(
        default=None, max_length=64, description="Price this SKU instead of the current one"
    )


class CheckPolicyIn(_In):
    actions: list[ProposedAction] = Field(min_length=1, max_length=25)


class SubmitPlanIn(_In):
    summary: str = Field(
        min_length=1,
        max_length=4000,
        description="What the change request does and why, citing evidence ids like [ev-03]",
    )
    actions: list[ProposedAction] = Field(max_length=25)
    not_proposed: list[NotProposed] = Field(
        default_factory=list,
        max_length=60,
        description="Every in-scope finding you reviewed and did not propose, each with its reason (blocked, conflict, untrusted metadata, out of scope)",
    )
    caveats: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Known limitations: degraded sources, failed tools, estimates that are not exact. Empty only if there are none.",
    )


class AskOperatorIn(_In):
    question: str = Field(min_length=1, max_length=400)
    options: list[str] = Field(default_factory=list, max_length=6)


# ---------------------------------------------------------------- handlers


def estate_summary(ctx: ToolContext, _args: EstateSummaryIn) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in ctx.estate.resources:
        counts[r.provider][r.kind] += 1
    envs = Counter(r.environment.value for r in ctx.estate.resources)
    findings = ctx.findings()
    waste = sum(f.est_monthly_savings_usd or 0 for f in findings if f.operation)
    eligible = sum(f.est_monthly_savings_usd or 0 for f in findings if f.eligible)
    spend = ctx.estate.monthly_spend()
    snapshots = ctx.estate.base.snapshots
    ev = ctx.evidence(
        "get_estate_summary",
        "",
        "estate",
        f"{len(ctx.estate.resources)} resources, ${round(sum(spend.values()))} billed in October",
    )
    return {
        "evidence_id": ev,
        "data_as_of": ctx.estate.as_of.isoformat(),
        "resources": {p: dict(k) for p, k in sorted(counts.items())},
        "environments": dict(sorted(envs.items())),
        "october_billed_usd": {**spend, "total": round(sum(spend.values()), 2)},
        "waste_monthly_usd": round(waste, 2),
        "eligible_savings_monthly_usd": round(eligible, 2),
        "sources": [
            {
                "source": s.source,
                "records": s.records_read,
                "quarantined": s.quarantined,
                "warnings": s.warnings,
            }
            for s in snapshots
        ],
        "price_source_degraded": ctx.faults.is_active(Fault.PRICING_UNAVAILABLE),
    }


def find_waste(ctx: ToolContext, args: FindWasteIn) -> dict[str, Any]:
    if ctx.faults.is_active(Fault.PRICING_UNAVAILABLE):
        ctx.degrade("pricing_unavailable: price lists unreachable; savings estimated from billing history")
    out = []
    for f in ctx.findings():
        r = ctx.estate.by_ref[f.ref]
        if args.categories and f.category not in args.categories and not _conflict_for(f, args.categories):
            continue
        if args.environments and r.environment not in args.environments:
            continue
        if args.providers and r.provider not in args.providers:
            continue
        if args.teams and (r.team or "") not in args.teams:
            continue
        if (f.est_monthly_savings_usd or 0) < args.min_monthly_savings_usd and f.operation is not None:
            continue
        if not args.include_blocked and not f.eligible:
            continue
        ev = ctx.evidence(
            "find_waste",
            f.ref,
            "finding",
            f"{f.category} {f.ref}: ${f.est_monthly_savings_usd or 0}/mo, blockers {list(f.blockers) or 'none'}",
        )
        u = f.utilization
        out.append(
            {
                "evidence_id": ev,
                "ref": f.ref,
                "name": _name(r),
                "provider": f.provider,
                "kind": f.kind,
                "environment": f.environment,
                "team": _tag_text(r, f.team),
                "owner": _tag_text(r, f.owner),
                "category": f.category,
                "recommended_operation": f.operation,
                "eligible": f.eligible,
                "blockers": list(f.blockers),
                "warnings": list(f.warnings),
                "current_sku": f.current_sku,
                "target_sku": f.target_sku,
                "monthly_cost_usd": f.monthly_cost_usd,
                "est_monthly_savings_usd": f.est_monthly_savings_usd,
                "price_source": f.price_source,
                "utilization": None
                if u is None
                else {
                    "p95_daily_avg_cpu": u.p95_daily_avg,
                    "max_cpu": u.max,
                    "coverage": u.coverage,
                    "days": u.datapoints,
                },
                "unattached_days": f.unattached_days,
                "untrusted_text_withheld": "suspicious_content" in r.flags,
            }
        )
        if len(out) >= args.limit:
            break
    return {
        "count": len(out),
        "findings": out,
        "thresholds": "idle: p95 daily CPU <= 5% and max <= 20%; oversized: p95 <= 15% and max <= 40%; 14+ days, 80%+ coverage; disks unattached 7+ days",
    }


def _conflict_for(f: Finding, categories: list[Category]) -> bool:
    """A source conflict on a VM blocks every VM category, so it is returned whenever VMs are in scope."""
    return f.category is Category.STATE_CONFLICT and bool(
        {Category.IDLE_VM, Category.OVERSIZED_VM} & set(categories)
    )


def get_resource(ctx: ToolContext, args: RefIn) -> dict[str, Any]:
    r = ctx.resource(args.ref)
    ev = ctx.evidence(
        "get_resource", r.ref, "resource", f"{r.kind} {r.ref} {r.environment} {r.state} {r.sku}"
    )
    return {
        "evidence_id": ev,
        "ref": r.ref,
        "id": r.id,
        "name": _name(r),
        "provider": r.provider,
        "kind": r.kind,
        "account": r.account,
        "region": r.region,
        "environment": r.environment,
        "team": _tag_text(r, r.team),
        "owner": _tag_text(r, r.owner),
        "state": r.state,
        "native_state": r.native_state,
        "sku": r.sku,
        "size_gb": r.size_gb,
        "created_at": r.created_at,
        "detached_at": r.detached_at,
        "attached_to": r.attached_to,
        "tags": _tags(r),
        "flags": list(r.flags),
        "source_ref": r.source_ref,
    }


def get_utilization(ctx: ToolContext, args: UtilizationIn) -> dict[str, Any]:
    if ctx.faults.fire(Fault.TOOL_TIMEOUT):
        raise ToolTimeoutError(f"metrics query timed out after {ctx.timeout_s:g} s (retryable)")
    r = ctx.resource(args.ref)
    if r.kind is not Kind.VM:
        raise ToolFailureError(f"{r.ref} is a {r.kind}; utilization exists for VMs only")
    end = (ctx.estate.as_of - dt.timedelta(days=1)).date()
    start = end - dt.timedelta(days=args.window_days - 1)
    u = summarize(r, ctx.estate.cpu_for(r), start, end)
    verdict = "no data"
    if u.p95_daily_avg is not None and u.max is not None:
        verdict = (
            "idle"
            if u.p95_daily_avg <= 5 and u.max <= 20
            else ("low" if u.p95_daily_avg <= 15 and u.max <= 40 else "normal")
        )
    ev = ctx.evidence(
        "get_utilization",
        r.ref,
        "utilization",
        f"{r.ref} p95 {u.p95_daily_avg}% max {u.max}% over {u.datapoints}/{u.expected_days} days",
    )
    return {
        "evidence_id": ev,
        "ref": r.ref,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "p95_daily_avg_cpu": u.p95_daily_avg,
        "avg_cpu": u.avg,
        "max_cpu": u.max,
        "coverage": u.coverage,
        "datapoints": u.datapoints,
        "expected_days": u.expected_days,
        "last_datapoint": u.last_datapoint,
        "verdict": verdict,
    }


def get_cost_history(ctx: ToolContext, args: RefIn) -> dict[str, Any]:
    r = ctx.resource(args.ref)
    lines = ctx.estate.costs_for(r)
    total = round(sum(c.billed_cost for c in lines), 2)
    by_service: dict[str, float] = defaultdict(float)
    for c in lines:
        by_service[c.service_category] += c.billed_cost
    last_day = max((c.day for c in lines), default=None)
    last = round(sum(c.billed_cost for c in lines if c.day == last_day), 2) if last_day else 0.0
    days = len({c.day for c in lines})
    ev = ctx.evidence(
        "get_cost_history", r.ref, "cost", f"{r.ref} billed ${total} in October over {days} days"
    )
    return {
        "evidence_id": ev,
        "ref": r.ref,
        "period": "2026-10",
        "billed_usd": total,
        "days_billed": days,
        "daily_average_usd": round(total / days, 2) if days else 0.0,
        "by_service_category": {k: round(v, 2) for k, v in sorted(by_service.items())},
        "last_day": last_day,
        "last_day_billed_usd": last,
    }


def quote_price(ctx: ToolContext, args: PriceIn) -> dict[str, Any]:
    r = ctx.resource(args.ref)
    try:
        q: PriceQuote = ctx.price()(r, args.sku)
    except PriceUnavailableError as exc:
        if ctx.faults.is_active(Fault.PRICING_UNAVAILABLE):
            ctx.degrade(
                "pricing_unavailable: price lists unreachable; savings estimated from billing history"
            )
        raise ToolFailureError(
            f"{exc}; find_waste falls back to billing history (price_source=billing_history)"
        ) from exc
    ev = ctx.evidence("quote_price", r.ref, "price", f"{q.sku} {q.region}: ${q.monthly_usd}/mo ({q.source})")
    return {
        "evidence_id": ev,
        "ref": r.ref,
        "sku": q.sku,
        "region": q.region,
        "unit": q.unit,
        "unit_price_usd": q.unit_price_usd,
        "monthly_usd": q.monthly_usd,
        "source": q.source,
        "as_of": q.as_of,
        "reference": q.reference,
    }


def check_policy(ctx: ToolContext, args: CheckPolicyIn) -> dict[str, Any]:
    verdict = evaluate(ctx, args.actions)
    return {
        "allowed": verdict.allowed,
        "risk": verdict.risk,
        "approvals_required": verdict.approvals_required,
        "actions": [
            {
                "ref": v.ref,
                "allowed": v.allowed,
                "risk": v.risk,
                "violations": [{"rule": c.rule, "name": c.name, "detail": c.detail} for c in v.violations],
            }
            for v in verdict.actions
        ],
        "plan_violations": [
            {"rule": c.rule, "detail": c.detail} for c in verdict.plan_checks if not c.passed
        ],
    }


def _routing_saving(f: Finding) -> float:
    return math.inf if f.est_monthly_savings_usd is None else f.est_monthly_savings_usd


def evaluate(ctx: ToolContext, actions: list[ProposedAction]) -> PolicyVerdict:
    """The policy verdict for `actions` against this run's estate, findings and retrieved evidence."""
    findings = ctx.finding_map()
    resolve = {a.ref: r for a in actions if (r := ctx.estate.resolve(a.ref)) is not None}
    # A saving that cannot be estimated is routed as if it reached the two-approver threshold: an unknown
    # impact never lowers the number of countersignatures.
    savings = {ref: _routing_saving(findings[r.ref]) for ref, r in resolve.items() if r.ref in findings}
    evidence_refs = {k: v.ref for k, v in ctx.state.evidence.items()}
    return ctx.policy.evaluate(
        actions, resolve=resolve, findings=findings, evidence_refs=evidence_refs, savings=savings
    )


def registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                "get_estate_summary",
                "Totals for the whole estate: resources by cloud and kind, October spend, identified waste, data health. Call first.",
                EstateSummaryIn,
                estate_summary,
            ),
            Tool(
                "find_waste",
                "Findings from the deterministic analyzer (idle VMs, oversized VMs, unattached disks, unused public IPs), "
                "each with blockers, warnings, estimated monthly savings and an evidence id. Filter to the request's scope.",
                FindWasteIn,
                find_waste,
            ),
            Tool(
                "get_resource",
                "One resource's inventory record: state, size, owner, environment, tags (instruction-like text withheld).",
                RefIn,
                get_resource,
            ),
            Tool(
                "get_utilization",
                "Daily CPU statistics for one VM over the window: p95 of daily averages, maximum, coverage.",
                UtilizationIn,
                get_utilization,
            ),
            Tool(
                "get_cost_history",
                "The resource's billed cost in the October FOCUS export, by service category, and its last billed day.",
                RefIn,
                get_cost_history,
            ),
            Tool(
                "quote_price",
                "Monthly run-rate of a resource at its current SKU or another one, with the price source (live, cache, snapshot) and reference.",
                PriceIn,
                quote_price,
            ),
            Tool(
                "check_policy",
                "Dry-run the change policy on proposed actions before submitting them. Returns violations per action and the approvals the plan would need.",
                CheckPolicyIn,
                check_policy,
            ),
            Tool(
                "submit_change_plan",
                "Submit the change request for human approval. Nothing changes until a human countersigns. "
                "Cite evidence ids in the summary and in each action. Submit an empty action list if nothing should change.",
                SubmitPlanIn,
                None,
                control=True,
            ),
            Tool(
                "ask_operator",
                "Ask the operator one question when the request is ambiguous. The run pauses until they answer.",
                AskOperatorIn,
                None,
                control=True,
            ),
        ]
    )


__all__ = ["AskOperatorIn", "CheckPolicyIn", "SubmitPlanIn", "ToolContext", "evaluate", "registry"]
