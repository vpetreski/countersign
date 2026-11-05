# Testing

A control is only as real as the test that would fail without it. The suite is organized by what each test
proves, it runs with no model key, no cloud account and no paid call, and it checks the documents against the
code: a failure mode, an OpenAPI route or an evaluation report that drifts from the implementation fails CI.

Counts below are for `main`; exact results with their commit are in
[reports/tests/summary.md](../reports/tests/summary.md).

## Layers

| Layer | Selector | Tests | Where | What it proves |
|---|---|---|---|---|
| Unit | `-m unit` | 97 | `tests/data/test_normalize.py`, `test_pricing.py`, `tests/policy/`, `tests/security/`, `tests/execution/test_tokens_and_audit.py`, `tests/agent/test_resilience_and_tools.py`, `tests/models/test_reference_policy.py`, `tests/evals/test_stats.py` | Pure rules: the 730-hour month and nearest-rank percentiles, price lookup and fallback, policy verdicts and risk routing, injection screening and secret masking, token signatures and the hash chain, jittered backoff and the circuit breaker, the reference policy's request grammar, the statistics against known values |
| Integration | `-m integration` | 117 | `tests/agent/test_runtime.py`, `test_tools.py`, `tests/domain/`, `tests/execution/test_executor.py`, `tests/evals/`, `tests/test_cli.py` | The runtime end to end on the real fixture estate: the critical workflow, every injectable fault, crash and replay, leases, budgets, expiry, compensation, tenant isolation; the analyzer against every planted scenario; the dataset against the analyzer; the graders, mutants and runner; the documents against the code |
| API | `-m "api_success or api_validation or api_authz or api_failure"` | 34 (7 + 12 + 8 + 7) | `tests/api/test_api.py` | Success paths, malformed input, authentication, roles, separation of duties and tenant isolation, conflicts and dependency failures over HTTP; RFC 9457 problem+json shapes, idempotency keys, rate limits, the security headers |
| Data validation | `-m data_validation` | 12 | `tests/data/test_ingest.py` | Every planted data defect is caught: schema violations quarantined with reasons, duplicates removed and counted, tags normalized, the stopped-but-billed VM reconciled, freshness past its service level |
| Claude contract | `-m claude_contract` | 63 | `tests/models/test_claude_contract.py`, `test_claude_runtime.py` | The Claude adapter against the Messages API wire format with sockets refused: the request body, strict tool schemas, every stop reason, error and `retry-after` mapping, one HTTP call per turn, the key never in logs, `repr` or errors; and one run driven through the runtime to `awaiting_approval` |
| PostgreSQL | `-m postgres` | 2 | `tests/test_postgres.py` | The critical workflow on PostgreSQL, and two real threads racing on one run: one model call per tick taken, no lost update. Skipped unless `COUNTERSIGN_TEST_DATABASE_URL` is set; CI runs them on PostgreSQL 17 |
| Frontend | Vitest | 53 in 9 files | `frontend/src/**/*.test.ts(x)` | Citations and evidence chips, the not-proposed list, the timeline reducer, money formatting, persona permissions, the trace layout, the auto-advance controller, the evals view |
| Browser | Playwright | 6 | `frontend/e2e/countersign.spec.ts` | The critical workflow, the production refusal, a pricing outage, RBAC, the evals page and the audit chain, in Chromium; the `local` project against a fresh seeded backend, the `live` project against a deployment |
| Evaluation | `countersign-eval` | 30 cases | `backend/evals/dataset/` | The agent and the evaluator: see [evaluation.md](evaluation.md) |
| Fuzzing | Schemathesis (nightly) | about 3,200 cases | `.github/workflows/nightly.yml` | No input the OpenAPI document allows makes the API answer 5xx (an operator's token, the cron route excluded); OpenAPI links let its stateful phase follow created runs and findings into every operation on them |

The backend's 325 tests (the six pytest layers) cover 93% of the `countersign` package, lines and branches
together; CI's floor is 90%. Coverage is a floor, not the goal: the tests that matter are the ones below.

## The tests that carry the most weight

- **The critical workflow, five ways.** In process
  (`test_full_workflow_executes_only_after_two_countersigns`), over HTTP (`test_the_critical_workflow_over_http`),
  on PostgreSQL (`test_the_critical_workflow_on_postgres`), in a browser against a fresh backend, and in a
  browser against the live deployment.
- **Crash after a side effect.** `test_crash_after_a_side_effect_resumes_without_repeating_it` lets the sandbox
  commit land, kills the tick before the run commits, then drives the run to the end and asserts one execution
  row per step and every step of the first action `replayed`.
- **A real race.** `test_two_workers_racing_on_one_run_call_the_model_once` releases two threads through a barrier
  onto one run on PostgreSQL. Either the compare-and-set lease refuses one (409) or they take consecutive ticks;
  either way there is exactly one model call per tick taken, and the run's version counts every one, so no
  update was lost.
- **A property, not examples.** `test_no_combination_of_actions_lets_a_blocked_resource_through` (Hypothesis)
  draws arbitrary action lists over every resource and operation; no allowed action is ever production, unknown
  environment, protected, flagged or ungrounded.
- **Ground truth for the analyzer.** `tests/domain/test_analysis_ground_truth.py` checks every planted scenario,
  including `test_no_finding_outside_the_planted_scenarios`: the analyzer finds nothing that was not planted.
- **The evaluator is tested before it is trusted.** Fifteen grader tests plant defects (an inflated figure, a
  phantom citation, an out-of-scope change, a hidden price outage, an uncountersigned effect...) and assert the
  right grader fails for the right reason; the sixteenth checks that the clean reference record passes all six. The runner
  tests assert each mutant is caught by the grader built for it and that reruns reproduce every outcome. The
  statistics are tested against known values and closed forms (Wilson, the unbiased pass^k estimator, the
  exact McNemar test).
- **The documents are tested.** `test_every_documented_failure_mode_names_a_proof_that_exists` parses
  [failure-modes.md](failure-modes.md): every id must name tests and cases that exist and appear in the tests,
  the dataset or the browser tests. `test_the_published_openapi_document_is_current` compares
  [openapi.json](openapi.json) with what the API serves. `test_the_published_report_describes_this_exact_code`
  recomputes the evaluation fingerprint and compares it with the published report.

## What the fuzzer found

The first nightly run that followed the OpenAPI links into real runs sent
`GET /api/runs/{run_id}/events?after=140261119261041691197440` and got a 500: SQLite cannot bind an integer
that large, and PostgreSQL's `INTEGER` stops at 2^31 - 1. The cursor is now bounded to the column, so the same
request is a 422, and that exact input is part of `test_query_and_answer_validation`. Before the links, the
fuzzer's requests to run-scoped routes carried invented ids and stopped at 404, and most of its run creations
stopped at the hourly limit; it could not have reached this code.

## What is faked, and how

| Real thing | Stand-in | Why it is still a test of the real code |
|---|---|---|
| Claude | A respx router behind the SDK's own transport, sockets refused | The SDK still serializes the request and parses real wire JSON; only `POST /v1/messages` is answered, anything else fails the test |
| The model in every other test | The offline reference policy, or a scripted model | Same `ChatModel` protocol, same tools, same policy and approvals as Claude |
| AWS and Azure APIs | The sandbox cloud | It enforces capability tokens, idempotency keys and preconditions; the executor cannot tell the difference |
| Live Azure prices | Off in tests; the fetcher is tested against a mocked Retail Prices API (throttled, empty and untrustworthy answers) | The snapshot fallback is the path tests exercise; the live path's request and parsing are asserted |
| Time | An injected clock | Lease expiry, run expiry and freshness are tested by moving the clock past each limit |
| PostgreSQL | SQLite in most tests | The two PostgreSQL tests run the critical workflow and the race on the real thing in CI |

## Running them

```sh
cd backend
uv run pytest                                   # everything except PostgreSQL
uv run pytest -m claude_contract                # one layer
COUNTERSIGN_TEST_DATABASE_URL=postgresql://... uv run pytest -m postgres
uv run pytest --cov=countersign --cov-report=term
uv run countersign-eval run --gate              # the evaluation and its gate

cd ../frontend
pnpm exec vitest run
pnpm exec playwright test                       # local: starts a seeded backend on :8000 and the UI on :5173
BASE_URL=https://countersign-agent.vercel.app pnpm exec playwright test --project=live
```

The `local` browser project writes the screenshots committed in `reports/tests/`; a `live` run keeps its own
with its results, so testing the deployment never changes the evidence in the repository.

## In CI

Every push and pull request runs five jobs: `backend` (ruff, format, mypy strict, five import-linter contracts,
pytest with the coverage floor, the published-report check), `postgres` (the two PostgreSQL tests on a
PostgreSQL 17 service), `frontend` (Biome, `tsc -b`, Vitest, the production build), `e2e` (Playwright against a
fresh backend and the built UI, one retry, screenshots and traces kept as artifacts) and `evals` (the full
evaluation with its gate, then an exact comparison with the published report). The nightly workflow adds the
deep evaluation (10 trials, 1,000 coverage replicates), authenticated stateful Schemathesis fuzzing, and the Claude job:
contract tests always, the live evaluation only when the repository has a key.

## What is not tested, and why

- **Claude's quality on these cases.** Measuring it spends money; the adapter is contract-tested, and the nightly
  job runs the evaluation on Claude as soon as a key is added.
- **Real provider APIs.** The sandbox plays them; a real integration needs least-privilege roles, the providers'
  idempotency tokens and their eventual consistency, and its own tests.
- **Load.** Rate limits and leases are tested for correctness, not throughput.
- **Other browsers and visual regressions.** Playwright runs Chromium; screenshots are evidence, not baselines.
