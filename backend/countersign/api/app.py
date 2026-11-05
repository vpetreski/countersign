"""The HTTP API (FastAPI). Errors are RFC 9457 problem+json; mutating routes accept an Idempotency-Key."""

import datetime as dt
import hashlib
import json
import os
import secrets
import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from countersign import __version__
from countersign.agent.faults import CATALOGUE, Fault
from countersign.agent.runtime import Principal, Role, RunError, Runtime
from countersign.api import views
from countersign.audit import chain
from countersign.auth.personas import PERSONAS, AuthError, issue, principal
from countersign.data.estate import ESTATE, price_fn, quality_results, quarantined
from countersign.data.ingest import ingest
from countersign.data.pricing import PriceCatalog, azure_fetcher
from countersign.models.factory import make_model
from countersign.policy.engine import default_policy
from countersign.settings import BACKEND_ROOT, Settings, get_settings
from countersign.store.db import Database
from countersign.store.schema import (
    SCHEMA_VERSION,
    AuditRow,
    IdempotencyRow,
    RateLimitRow,
    RunEventRow,
    RunRow,
    SourceSnapshot,
    WorkspaceRow,
)

KEYLESS_REASON = (
    "This public demo runs without an Anthropic API key on purpose: an unauthenticated page that spends model tokens "
    "is an open invitation to cost abuse, and the point is the harness around the model. The offline reference policy "
    "drives the same runtime, tools, policy and approvals; set ANTHROPIC_API_KEY and the same code uses Claude."
)
REPORT_FILE = BACKEND_ROOT / "countersign" / "evals" / "published" / "latest.json"


class Problem(Exception):
    def __init__(
        self, status: int, title: str, detail: str, code: str = "error", extra: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.status, self.title, self.detail, self.code, self.extra = status, title, detail, code, extra or {}


def _problem(
    status: int, title: str, detail: str, code: str, instance: str, extra: dict[str, Any] | None = None
) -> JSONResponse:
    body = {
        "type": f"https://countersign.dev/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        **(extra or {}),
    }
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


# ---------------------------------------------------------------- request bodies


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(_Body):
    request: str = Field(min_length=3, max_length=2000)
    faults: list[Fault] = Field(default_factory=list, max_length=5)


class AnswerIn(_Body):
    answer: str = Field(min_length=1, max_length=500)


class ActionDecision(_Body):
    action_id: str = Field(pattern=r"^act-\d{2}$")
    decision: Literal["approve", "reject"]


class DecisionIn(_Body):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[ActionDecision] = Field(min_length=1, max_length=25)
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- the application


class Services:
    """Built once per process, lazily, so importing the app never touches the network or the database."""

    def __init__(self, settings: Settings, clock: Callable[[], dt.datetime] | None = None) -> None:
        self.settings = settings
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self.db = Database(settings.database_url)
        fetch = azure_fetcher(settings.live_price_timeout_seconds) if settings.live_prices else None
        self.catalog = PriceCatalog(settings.fixtures_dir, fetch=fetch, now=self.clock)
        self.runtime = Runtime(
            self.db,
            settings,
            model_factory=make_model,
            catalog=self.catalog,
            policy=default_policy(),
            clock=self.clock,
        )


# An event sequence number is an INTEGER column: 32 bits in PostgreSQL. A larger cursor is a 422, not a 500
# from the driver (the nightly fuzzer found `after=140261119261041691197440`).
MAX_SEQ = 2**31 - 1


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_production_safe()
    app = FastAPI(
        title="Countersign",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    lock = threading.Lock()
    holder: dict[str, Services] = {}
    if services is not None:
        holder["s"] = services

    def svc() -> Services:
        with lock:
            if "s" not in holder:
                holder["s"] = Services(settings)
            return holder["s"]

    Svc = Annotated[Services, Depends(svc)]

    def who(s: Svc, authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise Problem(
                401,
                "Unauthorized",
                "send Authorization: Bearer <token> from POST /api/workspaces",
                "unauthorized",
            )
        try:
            p = principal(s.settings, authorization.split(" ", 1)[1].strip(), s.clock())
        except AuthError as exc:
            raise Problem(401, "Unauthorized", str(exc), "unauthorized") from exc
        with s.db.session() as session:
            ws = session.get(WorkspaceRow, p.workspace_id)
            if ws is None or _aware(ws.expires_at) < s.clock():
                raise Problem(
                    401,
                    "Unauthorized",
                    "this demo workspace has expired; create a new one",
                    "workspace_expired",
                )
        return p

    Who = Annotated[Principal, Depends(who)]

    # ------------------------------------------------------------ errors

    @app.exception_handler(Problem)
    async def _problem_handler(request: Request, exc: Problem) -> JSONResponse:
        return _problem(exc.status, exc.title, exc.detail, exc.code, request.url.path, exc.extra)

    @app.exception_handler(RunError)
    async def _run_error(request: Request, exc: RunError) -> JSONResponse:
        titles = {403: "Forbidden", 404: "Not Found", 409: "Conflict"}
        return _problem(
            exc.status, titles.get(exc.status, "Bad Request"), str(exc), exc.code, request.url.path
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "problem": e["msg"]}
            for e in exc.errors()[:20]
        ]
        return _problem(
            422,
            "Unprocessable Content",
            "the request body is invalid",
            "validation",
            request.url.path,
            {"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail), str(exc.detail), "http", request.url.path)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        ref = secrets.token_hex(6)
        app.state.last_error = f"{ref}: {type(exc).__name__}: {exc}"
        return _problem(
            500, "Internal Server Error", f"unexpected error (reference {ref})", "internal", request.url.path
        )

    @app.middleware("http")
    async def _headers(request: Request, call_next: Callable[[Request], Any]) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path != "/api/docs":  # the interactive docs page loads its own scripts
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ------------------------------------------------------------ guards

    def rate_limit(s: Services, bucket: str, limit: int) -> None:
        now = s.clock()
        window = now.replace(minute=0, second=0, microsecond=0)
        with s.db.session() as session:
            row = session.get(RateLimitRow, (bucket, window))
            if row is None:
                session.add(RateLimitRow(bucket=bucket, window_start=window, count=1))
                return
            if row.count >= limit:
                retry = int((window + dt.timedelta(hours=1) - now).total_seconds())
                raise Problem(
                    429,
                    "Too Many Requests",
                    f"limit of {limit} per hour reached",
                    "rate_limited",
                    {"retry_after_s": retry},
                )
            row.count += 1

    def idempotent(
        s: Services,
        key: str | None,
        workspace: str,
        route: str,
        body: dict[str, Any],
        run: Callable[[], tuple[int, dict[str, Any]]],
    ) -> JSONResponse:
        if not key:
            status, payload = run()
            return JSONResponse(payload, status_code=status)
        if len(key) > 200:
            raise Problem(
                422, "Unprocessable Content", "Idempotency-Key is longer than 200 characters", "validation"
            )
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        with s.db.session() as session:
            row = session.get(IdempotencyRow, (key, workspace, route))
            if row is not None:
                if row.request_hash != digest:
                    raise Problem(
                        422,
                        "Unprocessable Content",
                        "Idempotency-Key reused with a different request",
                        "idempotency_mismatch",
                    )
                return JSONResponse(
                    row.response, status_code=row.status_code, headers={"Idempotent-Replayed": "true"}
                )
        status, payload = run()
        try:
            with s.db.session() as session:
                session.add(
                    IdempotencyRow(
                        key=key,
                        workspace_id=workspace,
                        route=route,
                        request_hash=digest,
                        status_code=status,
                        response=payload,
                        created_at=s.clock(),
                    )
                )
        except IntegrityError:
            pass  # a concurrent duplicate stored it first; this response is equivalent
        return JSONResponse(payload, status_code=status)

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")

    def load_run(s: Services, p: Principal, run_id: str) -> RunRow:
        with s.db.session() as session:
            row = session.get(RunRow, run_id)
            if row is None or row.workspace_id != p.workspace_id:
                raise Problem(404, "Not Found", f"no run {run_id} in this workspace", "run_not_found")
            return row

    # ------------------------------------------------------------ public

    @app.get("/api/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def ready(s: Svc) -> JSONResponse:
        checks: list[dict[str, Any]] = []
        version = s.db.schema_version()
        checks.append({"check": "database", "ok": version is not None})
        checks.append(
            {
                "check": "schema",
                "ok": version == SCHEMA_VERSION,
                "detail": f"{version} (expected {SCHEMA_VERSION})",
            }
        )
        fresh: dict[str, Any] = {"check": "sources", "ok": False}
        if version is not None:
            with s.db.session() as session:
                snaps = list(session.scalars(select(SourceSnapshot).where(SourceSnapshot.current.is_(True))))
            fresh["ok"] = len(snaps) >= 3
            if snaps:
                age = (s.clock() - min(_aware(x.fetched_at) for x in snaps)).total_seconds() / 3600
                fresh["detail"] = f"{len(snaps)} sources, synced {age:.1f} h ago"
                checks.append(
                    {
                        "check": "freshness",
                        "ok": age <= s.settings.inventory_freshness_hours,
                        "informational": True,
                        "detail": f"{age:.1f} h (service level {s.settings.inventory_freshness_hours:.0f} h)",
                    }
                )
        checks.insert(2, fresh)
        ok = all(c["ok"] for c in checks if not c.get("informational"))
        return JSONResponse(
            {"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503
        )

    @app.get("/api/config")
    def config(s: Svc) -> dict[str, Any]:
        st = s.settings
        claude = st.model_mode == "claude"
        return {
            "name": "Countersign",
            "version": __version__,
            "model": {
                "provider": "anthropic" if claude else "offline",
                "model": st.claude_model if claude else "countersign-offline-reference",
                "keyless": not claude,
                "reason": None if claude else KEYLESS_REASON,
            },
            "build": {
                "sha": os.environ.get("COUNTERSIGN_BUILD_SHA")
                or os.environ.get("VERCEL_GIT_COMMIT_SHA")
                or "local",
                "environment": st.env,
            },
            "faults": [
                {"name": f.fault.value, "title": f.title, "effect": f.effect, "expected": f.expected}
                for f in CATALOGUE
            ],
            "personas": [
                {"key": p.key, "name": p.name, "role": p.role.value, "description": p.description}
                for p in PERSONAS
            ],
            "limits": {
                "max_model_turns": st.max_model_turns,
                "max_tool_calls": st.max_tool_calls,
                "runs_per_hour": st.rate_limit_runs_per_hour,
                "workspace_ttl_hours": st.workspace_ttl_hours,
            },
            "policy": {
                "version": default_policy().version,
                "rules": [{"id": k, **v} for k, v in default_policy().rules.items()],
            },
        }

    @app.post("/api/workspaces", status_code=201)
    def create_workspace(request: Request, s: Svc) -> dict[str, Any]:
        rate_limit(s, f"ws:{client_ip(request)}", s.settings.rate_limit_workspaces_per_hour)
        now = s.clock()
        ws_id = "ws_" + secrets.token_hex(8)
        expires = now + dt.timedelta(hours=s.settings.workspace_ttl_hours)
        with s.db.session() as session:
            session.add(WorkspaceRow(id=ws_id, created_at=now, expires_at=expires))
            session.flush()
            chain.append(
                session,
                ws_id,
                actor="system",
                action="workspace.created",
                subject=ws_id,
                payload={"expires_at": expires.isoformat()},
                at=now,
            )
        return {
            "workspace_id": ws_id,
            "expires_at": expires.isoformat(),
            "tokens": {p.key: issue(s.settings, ws_id, p, now) for p in PERSONAS},
            "personas": [
                {"key": p.key, "name": p.name, "role": p.role.value, "description": p.description}
                for p in PERSONAS
            ],
        }

    @app.get("/api/evals/latest")
    def evals_latest() -> JSONResponse:
        if not REPORT_FILE.exists():
            raise Problem(404, "Not Found", "no published evaluation report", "no_report")
        return JSONResponse(
            json.loads(REPORT_FILE.read_text()), headers={"Cache-Control": "public, max-age=300"}
        )

    # ------------------------------------------------------------ estate

    @app.get("/api/dashboard")
    def dashboard(s: Svc, p: Who) -> dict[str, Any]:
        with s.db.session() as session:
            estate = ESTATE.view(session, p.workspace_id)
            quality = [{"status": q.status} for q in quality_results(session)]
            q_count = len(quarantined(session))
        findings = estate.findings(price_fn(s.catalog, live=s.settings.live_prices))
        return views.dashboard(estate, findings, quality, q_count)

    @app.get(
        "/api/findings",
        operation_id="list_findings",
        responses={
            200: {
                "description": "Findings over the workspace's estate",
                "links": {
                    "GetResource": {
                        "operationId": "get_resource",
                        "parameters": {"ref": "$response.body#/findings/0/ref"},
                    }
                },
            }
        },
    )
    def findings(
        s: Svc,
        p: Who,
        category: str | None = None,
        environment: str | None = None,
        provider: str | None = None,
        eligible: bool | None = None,
    ) -> dict[str, Any]:
        with s.db.session() as session:
            estate = ESTATE.view(session, p.workspace_id)
        out = []
        for f in estate.findings(price_fn(s.catalog, live=s.settings.live_prices)):
            if (
                (category and f.category != category)
                or (environment and f.environment != environment)
                or (provider and f.provider != provider)
            ):
                continue
            if eligible is not None and f.eligible != eligible:
                continue
            out.append(views.finding_view(f, estate))
        return {"count": len(out), "findings": out}

    @app.get("/api/resources/{ref}", operation_id="get_resource")
    def resource(ref: str, s: Svc, p: Who) -> dict[str, Any]:
        with s.db.session() as session:
            estate = ESTATE.view(session, p.workspace_id)
        r = estate.resolve(ref)
        if r is None:
            raise Problem(404, "Not Found", f"no resource {ref}", "resource_not_found")
        finding = next((f for f in estate.findings(price_fn(s.catalog, live=False)) if f.ref == r.ref), None)
        cpu = [{"day": c.day.isoformat(), "avg": c.avg, "max": c.max} for c in estate.cpu_for(r)]
        costs: dict[str, float] = {}
        for c in estate.costs_for(r):
            costs[c.day.isoformat()] = round(costs.get(c.day.isoformat(), 0) + c.billed_cost, 4)
        data = r.model_dump(mode="json")
        return {
            "resource": data,
            "finding": views.finding_view(finding, estate) if finding else None,
            "cpu": cpu,
            "daily_cost": [{"day": d, "billed_usd": v} for d, v in sorted(costs.items())],
        }

    @app.get("/api/sources")
    def sources(s: Svc, p: Who) -> dict[str, Any]:
        del p
        with s.db.session() as session:
            snaps = list(
                session.scalars(
                    select(SourceSnapshot)
                    .where(SourceSnapshot.current.is_(True))
                    .order_by(SourceSnapshot.source)
                )
            )
            checks = quality_results(session)
            quar = quarantined(session)
        return {
            "snapshots": [
                {
                    "source": x.source,
                    "checksum": x.checksum,
                    "fetched_at": _iso(x.fetched_at),
                    "data_as_of": _iso(x.data_as_of),
                    "records_read": x.records_read,
                    "quarantined": x.quarantined,
                    "warnings": x.warnings,
                }
                for x in snaps
            ],
            "checks": [
                {
                    "check_id": c.check_id,
                    "category": c.category,
                    "status": c.status,
                    "observed": c.observed,
                    "expected": c.expected,
                    "detail": c.detail,
                }
                for c in checks
            ],
            "quarantine": [
                {"source": q.source, "source_ref": q.source_ref, "reason": q.reason, "raw": q.raw[:400]}
                for q in quar
            ],
        }

    # ------------------------------------------------------------ runs

    # OpenAPI links: a client, or a stateful fuzzer, can follow a created run to every operation on it.
    run_links = {
        name: {"operationId": op, "parameters": {"run_id": "$response.body#/id"}}
        for name, op in (
            ("GetRun", "get_run"),
            ("AdvanceRun", "advance_run"),
            ("AnswerRun", "answer_run"),
            ("DecideRun", "decide_run"),
            ("CancelRun", "cancel_run"),
            ("RunEvents", "run_events"),
            ("RunTrace", "run_trace"),
        )
    }

    @app.post(
        "/api/runs",
        status_code=201,
        operation_id="create_run",
        responses={201: {"description": "The new run", "links": run_links}},
    )
    def create_run(
        body: RunCreate, s: Svc, p: Who, idempotency_key: Annotated[str | None, Header()] = None
    ) -> JSONResponse:
        if p.role is not Role.OPERATOR:
            raise Problem(403, "Forbidden", "only an operator can start a run", "forbidden")

        def go() -> tuple[int, dict[str, Any]]:
            rate_limit(s, f"runs:{p.workspace_id}", s.settings.rate_limit_runs_per_hour)
            run_id = s.runtime.create(p, body.request, [f.value for f in body.faults])
            return 201, views.run_view(load_run(s, p, run_id), p)

        return idempotent(
            s, idempotency_key, p.workspace_id, "POST /api/runs", body.model_dump(mode="json"), go
        )

    @app.get("/api/runs")
    def list_runs(s: Svc, p: Who, limit: Annotated[int, Query(ge=1, le=50)] = 20) -> dict[str, Any]:
        with s.db.session() as session:
            rows = list(
                session.scalars(
                    select(RunRow)
                    .where(RunRow.workspace_id == p.workspace_id)
                    .order_by(RunRow.created_at.desc())
                    .limit(limit)
                )
            )
        return {"runs": [views.run_view(r, p, detail=False) for r in rows]}

    @app.get("/api/runs/{run_id}", operation_id="get_run")
    def get_run(run_id: str, s: Svc, p: Who) -> dict[str, Any]:
        return views.run_view(load_run(s, p, run_id), p)

    @app.post("/api/runs/{run_id}/advance", operation_id="advance_run")
    def advance(run_id: str, s: Svc, p: Who) -> dict[str, Any]:
        s.runtime.advance(p, run_id)
        return views.run_view(load_run(s, p, run_id), p)

    @app.post("/api/runs/{run_id}/input", operation_id="answer_run")
    def answer(run_id: str, body: AnswerIn, s: Svc, p: Who) -> dict[str, Any]:
        s.runtime.answer(p, run_id, body.answer)
        return views.run_view(load_run(s, p, run_id), p)

    @app.post("/api/runs/{run_id}/decision", operation_id="decide_run")
    def decide(
        run_id: str, body: DecisionIn, s: Svc, p: Who, idempotency_key: Annotated[str | None, Header()] = None
    ) -> JSONResponse:
        if p.role is not Role.APPROVER:
            raise Problem(403, "Forbidden", "only an approver can countersign", "forbidden")

        def go() -> tuple[int, dict[str, Any]]:
            decisions = {d.action_id: d.decision for d in body.decisions}
            if len(decisions) != len(body.decisions):
                raise Problem(422, "Unprocessable Content", "each action may appear once", "validation")
            s.runtime.decide(p, run_id, body.plan_hash, decisions, body.note)
            return 200, views.run_view(load_run(s, p, run_id), p)

        return idempotent(
            s,
            idempotency_key,
            p.workspace_id,
            f"POST /api/runs/{run_id}/decision",
            body.model_dump(mode="json"),
            go,
        )

    @app.post("/api/runs/{run_id}/cancel", operation_id="cancel_run")
    def cancel(run_id: str, s: Svc, p: Who) -> dict[str, Any]:
        s.runtime.cancel(p, run_id)
        return views.run_view(load_run(s, p, run_id), p)

    @app.get("/api/runs/{run_id}/events", operation_id="run_events")
    def events(
        run_id: str, s: Svc, p: Who, after: Annotated[int, Query(ge=0, le=MAX_SEQ)] = 0
    ) -> dict[str, Any]:
        load_run(s, p, run_id)
        with s.db.session() as session:
            rows = list(
                session.scalars(
                    select(RunEventRow)
                    .where(RunEventRow.run_id == run_id, RunEventRow.seq > after, RunEventRow.type != "span")
                    .order_by(RunEventRow.seq)
                )
            )
        return {
            "events": [{"seq": e.seq, "at": _iso(e.at), "type": e.type, "payload": e.payload} for e in rows]
        }

    @app.get("/api/runs/{run_id}/trace", operation_id="run_trace")
    def trace(run_id: str, s: Svc, p: Who) -> dict[str, Any]:
        load_run(s, p, run_id)
        with s.db.session() as session:
            rows = list(
                session.scalars(
                    select(RunEventRow)
                    .where(RunEventRow.run_id == run_id, RunEventRow.type == "span")
                    .order_by(RunEventRow.seq)
                )
            )
        spans = sorted((dict(e.payload) for e in rows), key=lambda x: x.get("start_ns", 0))
        origin = spans[0].get("start_ns", 0) if spans else 0
        for span in spans:  # nanosecond epochs exceed a double's exact range; offsets in microseconds do not
            span["offset_us"] = (span.get("start_ns", 0) - origin) // 1000
            span["duration_us"] = max(0, span.get("end_ns", 0) - span.get("start_ns", 0)) // 1000
        return {"trace_id": spans[0]["trace_id"] if spans else None, "spans": spans}

    # ------------------------------------------------------------ audit

    @app.get("/api/audit")
    def audit(s: Svc, p: Who, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> dict[str, Any]:
        with s.db.session() as session:
            rows = list(
                session.scalars(
                    select(AuditRow)
                    .where(AuditRow.workspace_id == p.workspace_id)
                    .order_by(AuditRow.seq.desc())
                    .limit(limit)
                )
            )
        return {
            "entries": [
                {
                    "seq": r.seq,
                    "at": _iso(r.at),
                    "actor": r.actor,
                    "action": r.action,
                    "subject": r.subject,
                    "payload": r.payload,
                    "prev_hash": r.prev_hash,
                    "hash": r.hash,
                }
                for r in rows
            ]
        }

    @app.get("/api/audit/verify")
    def audit_verify(s: Svc, p: Who) -> dict[str, Any]:
        with s.db.session() as session:
            v = chain.verify(session, p.workspace_id)
        return {
            "valid": v.valid,
            "entries": v.entries,
            "head": v.head,
            "broken_at": v.broken_at,
            "reason": v.reason,
        }

    # ------------------------------------------------------------ operations

    @app.get("/api/cron/sync")
    def cron_sync(s: Svc, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        expected = f"Bearer {s.settings.cron_secret.get_secret_value()}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise Problem(401, "Unauthorized", "cron secret required", "unauthorized")
        report = ingest(s.db, s.settings, now=s.clock())
        purged = purge_expired(s)
        return {
            "changed": report.changed,
            "resources": report.resources,
            "quarantined": report.quarantined,
            "purged_workspaces": purged,
        }

    def purge_expired(s: Services) -> int:
        now = s.clock()
        with s.db.session() as session:
            expired = list(session.scalars(select(WorkspaceRow.id).where(WorkspaceRow.expires_at < now)))
            if expired:
                session.execute(delete(AuditRow).where(AuditRow.workspace_id.in_(expired)))
                session.execute(delete(IdempotencyRow).where(IdempotencyRow.workspace_id.in_(expired)))
                session.execute(delete(WorkspaceRow).where(WorkspaceRow.id.in_(expired)))
            session.execute(
                delete(RateLimitRow).where(RateLimitRow.window_start < now - dt.timedelta(days=1))
            )
        return len(expired)

    app.state.services = svc
    app.state.count_runs = lambda: _count(svc().db)
    return app


def _count(db: Database) -> int:
    with db.session() as session:
        return int(session.scalar(select(func.count()).select_from(RunRow)) or 0)


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _iso(value: dt.datetime) -> str:
    return _aware(value).isoformat()
