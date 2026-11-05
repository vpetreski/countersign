"""The HTTP API: success paths, validation, authorization and tenant isolation, failure handling."""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from sqlalchemy import update

import countersign.api.app as app_module
from countersign.store.db import Database
from countersign.store.schema import RunRow, WorkspaceRow
from tests.conftest import Api, Clock, make_api

REQUEST = {"request": "Find idle development VMs and unattached disks and propose savings."}
UNTIL_WAIT = {"awaiting_approval", "awaiting_input", "completed", "failed", "budget_exhausted"}
UNTIL_END = {"completed", "completed_with_failures", "failed", "rejected", "cancelled"}


def start(api: Api, tokens: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = api.client.post("/api/runs", json=body or REQUEST, headers=api.auth(tokens["operator"]))
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def to_approval(api: Api, tokens: dict[str, str]) -> dict[str, Any]:
    run = start(api, tokens)
    run = api.drive(run["id"], tokens["operator"], UNTIL_WAIT)
    assert run["status"] == "awaiting_approval"
    return run


def decision(run: dict[str, Any], verdict: str = "approve") -> dict[str, Any]:
    return {
        "plan_hash": run["plan"]["hash"],
        "decisions": [{"action_id": a["id"], "decision": verdict} for a in run["plan"]["actions"]],
    }


# ---------------------------------------------------------------- success


@pytest.mark.api_success
def test_the_critical_workflow_over_http(api: Api) -> None:
    t = api.workspace()
    before = api.client.get("/api/dashboard", headers=api.auth(t["viewer"])).json()
    run = to_approval(api, t)
    assert run["can"]["decide"] is False and run["waiting_for"] == "approver"
    assert all(a["evidence_ids"] and a["est_monthly_savings"] > 0 for a in run["plan"]["actions"])
    for approver in ("approver", "approver2"):
        resp = api.client.post(
            f"/api/runs/{run['id']}/decision", json=decision(run), headers=api.auth(t[approver])
        )
        assert resp.status_code == 200, resp.text
    run = api.drive(run["id"], t["operator"], UNTIL_END)
    assert run["status"] == "completed"
    assert run["realized_monthly_savings"] == pytest.approx(run["plan"]["total_monthly_savings"])
    after = api.client.get("/api/dashboard", headers=api.auth(t["viewer"])).json()
    assert after["waste"]["eligible_monthly_usd"] == pytest.approx(
        before["waste"]["eligible_monthly_usd"] - run["realized_monthly_savings"], abs=0.05
    )
    assert len(after["changed_in_workspace"]) == len(run["plan"]["actions"])
    verify = api.client.get("/api/audit/verify", headers=api.auth(t["viewer"])).json()
    assert verify["valid"] and verify["entries"] >= 8


@pytest.mark.api_success
def test_events_are_incremental_and_the_trace_follows_genai_conventions(api: Api) -> None:
    t = api.workspace()
    run = to_approval(api, t)
    events = api.client.get(f"/api/runs/{run['id']}/events", headers=api.auth(t["viewer"])).json()["events"]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    later = api.client.get(
        f"/api/runs/{run['id']}/events?after={events[2]['seq']}", headers=api.auth(t["viewer"])
    ).json()["events"]
    assert later == events[3:]
    trace = api.client.get(f"/api/runs/{run['id']}/trace", headers=api.auth(t["viewer"])).json()
    names = {s["name"].split(" ")[0] for s in trace["spans"]}
    assert {"invoke_agent", "chat", "execute_tool"} <= names
    chat = next(s for s in trace["spans"] if s["name"].startswith("chat "))
    assert chat["attributes"]["gen_ai.provider.name"] == "countersign.offline"  # never claims to be Anthropic
    assert (
        chat["attributes"]["gen_ai.usage.input_tokens"] > 0
        and chat["attributes"]["countersign.usage.simulated"] is True
    )
    assert len({s["trace_id"] for s in trace["spans"]}) == 1
    ids = {s["span_id"] for s in trace["spans"]}
    assert all(s["parent_id"] is None or s["parent_id"] in ids for s in trace["spans"])  # no dangling parents
    assert trace["spans"][0]["offset_us"] == 0 and all(s["duration_us"] >= 0 for s in trace["spans"])


@pytest.mark.api_success
def test_estate_routes(api: Api) -> None:
    t = api.workspace()
    h = api.auth(t["viewer"])
    dash = api.client.get("/api/dashboard", headers=h).json()
    assert dash["resources"]["total"] == 149 and dash["data_health"]["quarantined"] == 3
    assert set(dash["spend"]["by_provider"]) == {"aws", "azure"} and len(dash["spend"]["daily"]) == 31
    findings = api.client.get("/api/findings?category=unattached_disk&eligible=true", headers=h).json()[
        "findings"
    ]
    assert findings and all(f["category"] == "unattached_disk" and f["eligible"] for f in findings)
    detail = api.client.get(f"/api/resources/{findings[0]['ref']}", headers=h).json()
    assert detail["resource"]["ref"] == findings[0]["ref"] and detail["finding"]["eligible"]
    vm = api.client.get("/api/resources/i-0c4f9e2a7b1d30001", headers=h).json()
    assert len(vm["cpu"]) >= 28 and len(vm["daily_cost"]) == 31
    sources = api.client.get("/api/sources", headers=h).json()
    assert len(sources["snapshots"]) == 3 and len(sources["quarantine"]) == 3 and sources["checks"]


@pytest.mark.api_success
def test_public_routes(api: Api) -> None:
    assert api.client.get("/api/health/live").json() == {"status": "ok"}
    ready = api.client.get("/api/health/ready")
    assert ready.status_code == 200 and ready.json()["status"] == "ok"
    cfg = api.client.get("/api/config").json()
    assert (
        cfg["model"]["provider"] == "offline" and cfg["model"]["keyless"] and "cost" in cfg["model"]["reason"]
    )
    assert {f["name"] for f in cfg["faults"]} == {
        "pricing_unavailable",
        "tool_timeout",
        "model_overloaded",
        "throttling",
        "state_drift",
    }
    assert len(cfg["personas"]) == 4 and cfg["policy"]["version"] == 1
    assert api.client.get("/api/openapi.json").status_code == 200


@pytest.mark.api_success
def test_answering_the_agent_over_http(api: Api) -> None:
    t = api.workspace()
    run = start(api, t, {"request": "make it cheaper"})
    run = api.drive(run["id"], t["operator"], UNTIL_WAIT)
    assert run["status"] == "awaiting_input" and run["can"]["answer"]
    resp = api.client.post(
        f"/api/runs/{run['id']}/input", json={"answer": "Unused public IPs"}, headers=api.auth(t["operator"])
    )
    assert resp.status_code == 200 and resp.json()["status"] == "planning"


@pytest.mark.api_success
def test_runs_are_listed_newest_first(api: Api) -> None:
    t = api.workspace()
    first = start(api, t)
    api.clock.advance(seconds=5)
    second = start(api, t)
    runs = api.client.get("/api/runs", headers=api.auth(t["viewer"])).json()["runs"]
    assert [r["id"] for r in runs] == [second["id"], first["id"]]


# ---------------------------------------------------------------- validation


@pytest.mark.api_validation
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"request": "x"},
        {"request": "y" * 2001},
        {"request": "stop idle vms", "faults": ["meteor_strike"]},
        {"request": "stop idle vms", "model": "claude"},
        {"request": 42},
    ],
)
def test_run_creation_rejects_malformed_bodies(api: Api, body: dict[str, Any]) -> None:
    t = api.workspace()
    resp = api.client.post("/api/runs", json=body, headers=api.auth(t["operator"]))
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["errors"]


@pytest.mark.api_validation
@pytest.mark.parametrize(
    "body",
    [
        {"plan_hash": "nothex", "decisions": [{"action_id": "act-01", "decision": "approve"}]},
        {"plan_hash": "a" * 64, "decisions": []},
        {"plan_hash": "a" * 64, "decisions": [{"action_id": "act-1", "decision": "approve"}]},
        {"plan_hash": "a" * 64, "decisions": [{"action_id": "act-01", "decision": "maybe"}]},
        {
            "plan_hash": "a" * 64,
            "decisions": [
                {"action_id": "act-01", "decision": "approve"},
                {"action_id": "act-01", "decision": "reject"},
            ],
        },
    ],
)
def test_decisions_reject_malformed_bodies(api: Api, body: dict[str, Any]) -> None:
    t = api.workspace()
    run = start(api, t)
    resp = api.client.post(f"/api/runs/{run['id']}/decision", json=body, headers=api.auth(t["approver"]))
    assert resp.status_code == 422, resp.text


@pytest.mark.api_validation
def test_query_and_answer_validation(api: Api) -> None:
    t = api.workspace()
    assert api.client.get("/api/runs?limit=0", headers=api.auth(t["viewer"])).status_code == 422
    assert api.client.get("/api/runs/x/events?after=-1", headers=api.auth(t["viewer"])).status_code == 422
    run = start(api, t)
    events = f"/api/runs/{run['id']}/events?after="
    assert api.client.get(events + str(2**31 - 1), headers=api.auth(t["viewer"])).json() == {"events": []}
    for too_big in (2**31, 140261119261041691197440):  # past the column; the second is the fuzzer's input
        assert api.client.get(events + str(too_big), headers=api.auth(t["viewer"])).status_code == 422
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/input", json={"answer": ""}, headers=api.auth(t["operator"])
        ).status_code
        == 422
    )
    long_key = {"Idempotency-Key": "k" * 201, **api.auth(t["operator"])}
    assert api.client.post("/api/runs", json=REQUEST, headers=long_key).status_code == 422


# ---------------------------------------------------------------- authorization


@pytest.mark.api_authz
@pytest.mark.parametrize(
    "header",
    [
        None,
        "Token abc",
        "Bearer garbage",
        "Bearer " + jwt.encode({"sub": "x"}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256"),
    ],
)
def test_missing_or_invalid_credentials_are_401(api: Api, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    resp = api.client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 401 and resp.json()["type"].endswith("/unauthorized")


@pytest.mark.api_authz
def test_unsigned_and_forged_tokens_are_refused(api: Api) -> None:
    t = api.workspace()
    claims = jwt.decode(t["viewer"], options={"verify_signature": False})
    none_alg = jwt.encode({**claims, "role": "approver", "persona": "approver"}, key=None, algorithm="none")  # type: ignore[arg-type]
    assert api.client.get("/api/dashboard", headers=api.auth(none_alg)).status_code == 401
    secret = api.services.settings.jwt_secret.get_secret_value()
    inconsistent = jwt.encode(
        {**claims, "role": "approver"}, secret, algorithm="HS256"
    )  # viewer persona with approver role
    assert api.client.get("/api/dashboard", headers=api.auth(inconsistent)).status_code == 401
    expired = jwt.encode({**claims, "exp": 1}, secret, algorithm="HS256")
    assert api.client.get("/api/dashboard", headers=api.auth(expired)).status_code == 401


@pytest.mark.api_authz
def test_roles_bound_every_mutation(api: Api) -> None:
    t = api.workspace()
    assert api.client.post("/api/runs", json=REQUEST, headers=api.auth(t["viewer"])).status_code == 403
    assert api.client.post("/api/runs", json=REQUEST, headers=api.auth(t["approver"])).status_code == 403
    run = to_approval(api, t)
    assert api.client.post(f"/api/runs/{run['id']}/advance", headers=api.auth(t["viewer"])).status_code == 403
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/decision", json=decision(run), headers=api.auth(t["operator"])
        ).status_code
        == 403
    )
    assert (
        api.client.post(f"/api/runs/{run['id']}/cancel", headers=api.auth(t["approver"])).status_code == 403
    )
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/input", json={"answer": "x"}, headers=api.auth(t["viewer"])
        ).status_code
        == 403
    )


@pytest.mark.api_authz
def test_workspaces_are_isolated(api: Api) -> None:
    """FM-19: another workspace's run does not exist for you; its audit chain and dashboard are separate."""
    mine, theirs = api.workspace(), api.workspace()
    run = start(api, theirs)
    for path in (f"/api/runs/{run['id']}", f"/api/runs/{run['id']}/events", f"/api/runs/{run['id']}/trace"):
        assert api.client.get(path, headers=api.auth(mine["viewer"])).status_code == 404
    assert (
        api.client.post(f"/api/runs/{run['id']}/advance", headers=api.auth(mine["operator"])).status_code
        == 404
    )
    assert api.client.get("/api/runs", headers=api.auth(mine["viewer"])).json()["runs"] == []
    assert (
        api.client.get("/api/audit", headers=api.auth(mine["viewer"])).json()["entries"][0]["action"]
        == "workspace.created"
    )


@pytest.mark.api_authz
def test_expired_workspaces_lose_access(api: Api) -> None:
    t = api.workspace()
    with api.services.db.session() as s:
        s.execute(update(WorkspaceRow).values(expires_at=api.clock() - dt.timedelta(seconds=1)))
    resp = api.client.get("/api/dashboard", headers=api.auth(t["viewer"]))
    assert resp.status_code == 401 and resp.json()["type"].endswith("/workspace_expired")


# ---------------------------------------------------------------- failure


@pytest.mark.api_failure
def test_conflicts_are_409(api: Api) -> None:
    """FM-13 and FM-14 over HTTP: concurrent advance, stale plan, double decision, wrong state."""
    t = api.workspace()
    run = to_approval(api, t)
    stale = {**decision(run), "plan_hash": "b" * 64}
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/decision", json=stale, headers=api.auth(t["approver"])
        ).status_code
        == 409
    )
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/decision", json=decision(run), headers=api.auth(t["approver"])
        ).status_code
        == 200
    )
    again = api.client.post(
        f"/api/runs/{run['id']}/decision", json=decision(run), headers=api.auth(t["approver"])
    )
    assert again.status_code == 409 and again.headers["content-type"].startswith("application/problem+json")
    assert (
        api.client.post(
            f"/api/runs/{run['id']}/input", json={"answer": "x"}, headers=api.auth(t["operator"])
        ).status_code
        == 409
    )
    other = start(api, t)
    with api.services.db.session() as s:
        s.execute(
            update(RunRow)
            .where(RunRow.id == other["id"])
            .values(lease_owner="someone", lease_until=api.clock() + dt.timedelta(minutes=1))
        )
    assert (
        api.client.post(f"/api/runs/{other['id']}/advance", headers=api.auth(t["operator"])).status_code
        == 409
    )


@pytest.mark.api_failure
def test_idempotency_keys_replay_and_refuse_reuse(api: Api) -> None:
    t = api.workspace()
    headers = {"Idempotency-Key": "abc-123", **api.auth(t["operator"])}
    first = api.client.post("/api/runs", json=REQUEST, headers=headers)
    second = api.client.post("/api/runs", json=REQUEST, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"] and second.headers["Idempotent-Replayed"] == "true"
    assert len(api.client.get("/api/runs", headers=api.auth(t["viewer"])).json()["runs"]) == 1
    mismatch = api.client.post("/api/runs", json={"request": "something else entirely"}, headers=headers)
    assert mismatch.status_code == 422 and mismatch.json()["type"].endswith("/idempotency_mismatch")


@pytest.mark.api_validation
def test_an_idempotency_key_belongs_to_its_caller(api: Api) -> None:
    """Two approvers sending the same decision with the same key: the second is refused, never handed the first
    approver's stored response, which would leave the second countersign unrecorded."""
    t = api.workspace()
    run = to_approval(api, t)
    body, url = decision(run), f"/api/runs/{run['id']}/decision"
    first = api.client.post(url, json=body, headers={"Idempotency-Key": "k-1", **api.auth(t["approver"])})
    assert first.status_code == 200 and first.json()["plan"]["approvals_received"] == 1
    reused = api.client.post(url, json=body, headers={"Idempotency-Key": "k-1", **api.auth(t["approver2"])})
    assert reused.status_code == 422 and reused.json()["type"].endswith("/idempotency_mismatch")
    own = api.client.post(url, json=body, headers={"Idempotency-Key": "k-2", **api.auth(t["approver2"])})
    assert own.status_code == 200 and own.json()["status"] == "executing"


@pytest.mark.api_failure
def test_rate_limits_hold_per_workspace_and_per_client(clock: Clock) -> None:
    """FM-20: the public demo bounds how much one visitor can start."""
    api = make_api(clock, rate_limit_runs_per_hour=2, rate_limit_workspaces_per_hour=2)
    t = api.workspace()
    api.workspace()
    assert api.client.post("/api/workspaces").status_code == 429
    for _ in range(2):
        start(api, t)
    resp = api.client.post("/api/runs", json=REQUEST, headers=api.auth(t["operator"]))
    assert resp.status_code == 429 and resp.json()["retry_after_s"] > 0
    clock.advance(hours=1)
    assert api.client.post("/api/runs", json=REQUEST, headers=api.auth(t["operator"])).status_code == 201


@pytest.mark.api_failure
def test_cron_sync_requires_its_secret_and_purges_expired_workspaces(api: Api) -> None:
    t = api.workspace()
    start(api, t)
    assert api.client.get("/api/cron/sync").status_code == 401
    assert api.client.get("/api/cron/sync", headers={"Authorization": "Bearer nope"}).status_code == 401
    api.clock.advance(hours=73)
    secret = api.services.settings.cron_secret.get_secret_value()
    resp = api.client.get("/api/cron/sync", headers={"Authorization": f"Bearer {secret}"})
    assert (
        resp.status_code == 200 and resp.json()["purged_workspaces"] == 1 and resp.json()["changed"] is False
    )


@pytest.mark.api_failure
def test_readiness_fails_closed_on_an_empty_database(clock: Clock) -> None:
    api = make_api(clock)
    api.services.db.engine.dispose()
    api.services.db = Database("sqlite://")  # a fresh, unmigrated database
    resp = api.client.get("/api/health/ready")
    assert resp.status_code == 503 and resp.json()["status"] == "unavailable"


@pytest.mark.api_failure
def test_unexpected_errors_are_problems_without_internals(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    t = api.workspace()

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(api.services.runtime, "create", boom)
    resp = api.client.post("/api/runs", json=REQUEST, headers=api.auth(t["operator"]))
    assert (
        resp.status_code == 500
        and "secret internal detail" not in resp.text
        and "reference" in resp.json()["detail"]
    )


@pytest.mark.api_failure
def test_no_published_report_is_a_404_problem(
    api: Api, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(app_module, "REPORT_FILE", tmp_path / "missing.json")
    resp = api.client.get("/api/evals/latest")
    assert resp.status_code == 404 and resp.json()["type"].endswith("/no_report")


@pytest.mark.api_success
def test_every_response_carries_the_security_headers(api: Api) -> None:
    resp = api.client.get("/api/health/live")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert resp.headers["Cache-Control"] == "no-store"
    assert (
        "Content-Security-Policy" not in api.client.get("/api/docs").headers
    )  # Swagger UI loads its own assets
