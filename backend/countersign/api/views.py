"""Response shapes. Views are built from stored state only; nothing here calls a model or changes data."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from countersign.agent.runtime import Principal, Role
from countersign.agent.state import TERMINAL, RunState, RunStatus
from countersign.data.estate import Estate
from countersign.domain.analysis import Finding
from countersign.store.schema import RunRow


def _utc(value: dt.datetime) -> str:
    value = value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)
    return value.isoformat()


def run_view(row: RunRow, principal: Principal, *, detail: bool = True) -> dict[str, Any]:
    st = RunState.model_validate(row.state)
    status = RunStatus(row.status)
    plan = st.plan
    tally: dict[str, dict[str, int]] = defaultdict(lambda: {"approve": 0, "reject": 0})
    for d in st.approvals:
        for action_id, verdict in d.decisions.items():
            tally[action_id][verdict] += 1
    outcomes = {o.action_id: o for o in st.outcomes}
    approvers = sorted({d.approver for d in st.approvals})
    view: dict[str, Any] = {
        "id": row.id,
        "status": status,
        "terminal": status in TERMINAL,
        "version": row.version,
        "request": row.request,
        "created_by": row.created_by,
        "model": row.model,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
        "faults": st.faults,
        "degraded": st.degraded,
        "error": st.error,
        "report": st.report,
        "question": st.question.model_dump() if st.question else None,
        "budgets": st.budgets.model_dump(),
        "not_proposed": [n.model_dump() for n in st.not_proposed],
        "waiting_for": "operator"
        if status is RunStatus.AWAITING_INPUT
        else ("approver" if status is RunStatus.AWAITING_APPROVAL else None),
        "approvals": [d.model_dump(mode="json") for d in st.approvals],
        "can": {
            "advance": principal.role in (Role.OPERATOR, Role.APPROVER) and status not in TERMINAL,
            "answer": principal.role is Role.OPERATOR and status is RunStatus.AWAITING_INPUT,
            "decide": principal.role is Role.APPROVER
            and status is RunStatus.AWAITING_APPROVAL
            and principal.sub != row.created_by
            and principal.sub not in approvers,
            "cancel": principal.role is Role.OPERATOR and status not in TERMINAL,
        },
        "plan": None,
    }
    if plan is not None:
        view["plan"] = {
            "hash": plan.hash,
            "summary": plan.summary,
            "caveats": plan.caveats,
            "not_proposed": [n.model_dump() for n in plan.not_proposed],
            "total_monthly_savings": plan.total_monthly_savings,
            "risk": plan.risk,
            "approvals_required": plan.approvals_required,
            "approvals_received": len(approvers),
            "revision": plan.revision,
            "policy_version": plan.policy_version,
            "actions": [
                {
                    **a.model_dump(mode="json"),
                    "approvals": tally[a.id]["approve"],
                    "rejections": tally[a.id]["reject"],
                    "outcome": outcomes[a.id].model_dump(mode="json") if a.id in outcomes else None,
                }
                for a in plan.actions
            ],
        }
    if detail:
        view["evidence"] = {k: v.model_dump() for k, v in st.evidence.items()}
        view["realized_monthly_savings"] = round(sum(o.realized_monthly_savings for o in st.outcomes), 2)
    return view


def finding_view(f: Finding, estate: Estate) -> dict[str, Any]:
    r = estate.by_ref[f.ref]
    flagged = "suspicious_content" in r.flags
    u = f.utilization
    return {
        "ref": f.ref,
        "resource_id": f.resource_id,
        "name": f.name,
        "provider": f.provider,
        "kind": f.kind,
        "account": r.account,
        "region": r.region,
        "environment": f.environment,
        "team": f.team,
        "owner": f.owner,
        "category": f.category,
        "operation": f.operation,
        "eligible": f.eligible,
        "blockers": list(f.blockers),
        "warnings": list(f.warnings),
        "current_sku": f.current_sku,
        "target_sku": f.target_sku,
        "monthly_cost_usd": f.monthly_cost_usd,
        "est_monthly_savings_usd": f.est_monthly_savings_usd,
        "price_source": f.price_source,
        "price_reference": f.price_reference,
        "utilization": u.model_dump(mode="json") if u else None,
        "unattached_days": f.unattached_days,
        "flagged_untrusted_text": flagged,
        "flags": list(r.flags),
    }


def dashboard(
    estate: Estate, findings: list[Finding], quality: list[dict[str, Any]], quarantined: int
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in estate.resources:
        counts[r.provider][r.kind] += 1
    spend = estate.monthly_spend()
    by_category: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "monthly_usd": 0.0, "eligible_count": 0, "eligible_monthly_usd": 0.0}
    )
    blocked: Counter[str] = Counter()
    for f in findings:
        c = by_category[f.category]
        c["count"] += 1
        c["monthly_usd"] += f.est_monthly_savings_usd or 0
        if f.eligible:
            c["eligible_count"] += 1
            c["eligible_monthly_usd"] += f.est_monthly_savings_usd or 0
        for b in f.blockers:
            blocked[b.split(":")[0]] += 1
    by_env: Counter[str] = Counter(r.environment.value for r in estate.resources)
    statuses = Counter(q["status"] for q in quality)
    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for lines in estate.base.costs.values():
        for line in lines:
            daily[line.day.isoformat()][line.provider] += line.billed_cost
    changed = [r.ref for r in estate.resources if "changed_in_workspace" in r.flags]
    return {
        "data_as_of": estate.as_of.isoformat(),
        "resources": {
            "total": len(estate.resources),
            "by_provider": {p: dict(k) for p, k in sorted(counts.items())},
            "by_environment": dict(sorted(by_env.items())),
            "accounts": len({r.account for r in estate.resources}),
        },
        "spend": {
            "period": "2026-10",
            "by_provider": spend,
            "total": round(sum(spend.values()), 2),
            "daily": [
                {"day": d, **{k: round(v, 2) for k, v in sorted(p.items())}} for d, p in sorted(daily.items())
            ],
        },
        "waste": {
            "monthly_usd": round(sum(f.est_monthly_savings_usd or 0 for f in findings if f.operation), 2),
            "eligible_monthly_usd": round(
                sum(f.est_monthly_savings_usd or 0 for f in findings if f.eligible), 2
            ),
            "findings": len(findings),
            "eligible": sum(1 for f in findings if f.eligible),
            "by_category": {
                k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(by_category.items())
            },
            "blocked_by": dict(blocked.most_common()),
        },
        "data_health": {"checks": dict(statuses), "quarantined": quarantined},
        "changed_in_workspace": changed,
    }
