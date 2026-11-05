# Countersign

**A cloud-cost agent that writes change requests and changes nothing until people countersign.** It finds idle
VMs, oversized VMs, unattached disks and unused public IPs across AWS and Azure, proposes a change request with
evidence behind every number, routes it by risk to one or two approvers (never the requester), executes only
what they countersigned, verifies the result, and records every step in a hash-chained audit log. Then it
evaluates itself, and before that it evaluates its own evaluator.

**[Live demo](https://countersign-agent.vercel.app)** · **[Article and film](https://vanja.io/countersign/)** ·
[Evaluation report](reports/evals/latest.md) · [Failure modes](docs/failure-modes.md) ·
[Architecture](docs/architecture.md)

> **Keyless by default, Claude when a key is set.** The live demo runs the offline **reference policy**, a
> deterministic test double, through the same runtime, tools, policy, approvals and executor that Claude uses.
> A public page that spends model tokens on every visit is a cost-abuse vector, so this project never spends
> money, credits or tokens unless you set `ANTHROPIC_API_KEY`; then the same code runs on Claude
> (`claude-opus-5-5`, strict tools, adaptive thinking). Be clear about what that means: offline results validate
> the harness, the graders and the statistics, **not** LLM quality, and the Claude adapter is **contract-tested**
> against the SDK's types and strict-schema rules, not run against the API.

![A change request: eight actions with evidence chips, policy checks and per-action countersignatures](reports/tests/critical-01-plan.png)

## One run, end to end

1. The operator (Sam) asks: *"Find idle development VMs and unattached disks and propose savings."*
2. The agent reads the estate through read-only tools. Every result carries an evidence id (`ev-07`).
3. It verifies each candidate (utilization, price), dry-runs the policy, and submits a change request: **8
   actions, $682.00 a month**, each citing its evidence. It also lists the **8 resources it reviewed and left
   out, with reasons**: a `do-not-delete` VM, a VM whose `Name` tag tries to instruct "the AI agent" to stop the
   production replica (the text never reached the model), a VM stopped in inventory but billed, one three days
   old, one with gappy metrics, a legal-hold volume, a `Lifecycle: Keep` disk and a disk detached two days ago.
4. Deleting disks is high risk, so two approvers must countersign. Alex and Jordan approve; Sam, the requester,
   cannot.
5. The last countersign runs the policy again, against the estate as it is then, and mints a capability token
   naming exactly the approved actions that still pass and that plan hash. The executor stops the VMs and
   snapshots then deletes the disks, every step idempotent and checked against current state, then verifies the
   end state: **$682.00 a month realized**, the audit chain valid.

The same run with every price source down, a metrics timeout, an overloaded model, throttled cloud APIs and a
resource that changes after approval ends `completed_with_failures`: one action not applied, the others done,
the degradation stated in the change request. That is evaluation case `c16`.

## Quickstart

Python 3.14 with [uv](https://docs.astral.sh/uv/), Node 22+ with pnpm.

```sh
git clone https://github.com/vpetreski/countersign && cd countersign/backend
uv sync
DATABASE_URL=sqlite:///./countersign.sqlite3 COUNTERSIGN_LIVE_PRICES=false uv run countersign seed
DATABASE_URL=sqlite:///./countersign.sqlite3 uv run uvicorn main:app --port 8000      # API on :8000, docs at /api/docs

cd ../frontend && pnpm install && pnpm dev                                             # UI on :5173, proxies /api
```

Checks: `uv run pytest`, `uv run countersign-eval run --gate`, `pnpm exec vitest run`, `pnpm exec playwright test`.
With a key: `ANTHROPIC_API_KEY=... uv run uvicorn main:app` and the UI's badge switches to Claude.

## Architecture

```
sources (native AWS / Azure / FOCUS shapes) -> ingest: validate, quarantine, dedupe, normalize, screen -> Postgres
estate + per-workspace overlay -> deterministic analyzer -> read-only tools with evidence ids
        <-> model (Claude or the reference policy) -> submit_change_plan -> policy as code -> plan + hash
        -> approvers countersign -> capability token -> saga executor -> idempotent sandbox cloud -> verify -> report
everything above -> hash-chained audit log; every tick -> OpenTelemetry GenAI spans
```

The model proposes; deterministic code disposes. It can read and it can submit; it cannot change anything.
The full picture, with the run's state machine: [docs/architecture.md](docs/architecture.md).

| Layer | What is in it |
|---|---|
| Data | Paginated `DescribeInstances` / `DescribeVolumes` / `DescribeAddresses`, CloudWatch `GetMetricData`, CloudTrail `DetachVolume`, Azure Resource Graph and Monitor, FOCUS 1.4 billing; AWS Price List snapshot with provenance; Azure Retail Prices API live with snapshot fallback; 9 planted data defects ingestion must catch |
| Agent | A durable state machine (13 states), one unit of work per tick, a compare-and-set lease, budgets, retries with full jitter, a circuit breaker, five injectable faults |
| Policy | `policy.yaml`: eight rules (production, protection tags, untrusted metadata, grounding, data sufficiency, unknown environment, plan size, known resource), risk by operation, 1 or 2 approvers, separation of duties |
| Execution | HMAC capability tokens, a saga executor (compensation inside an action, irreversible steps last), a sandbox cloud with idempotency keys and precondition checks |
| Trust | Hash-chained audit log per workspace, OpenTelemetry GenAI spans (`invoke_agent`, `chat`, `execute_tool`), RFC 9457 errors, rate limits, CSP |
| Models | `ChatModel` in Anthropic Messages semantics: the Claude adapter and the offline reference policy |
| UI | React 19, TypeScript, Tailwind 4, TanStack Query: Agent, Estate, Data, Evals, Audit; a persona switcher makes separation of duties visible |

## Design decisions

| Decision | Why | Record |
|---|---|---|
| Change requests, not autonomous changes | A model's mistakes should become rejected proposals, not outages | [ADR 0001](docs/adr/0001-change-requests-not-autonomous-changes.md) |
| Keyless by default, Claude when a key is set | A public demo must not spend tokens; offline runs are free and reproducible | [ADR 0002](docs/adr/0002-keyless-by-default.md) |
| Durable runs, effectively-once effects | Functions die mid-request; two tabs race; a cloud call can succeed while its caller crashes | [ADR 0003](docs/adr/0003-durable-runs-and-effectively-once-effects.md) |
| Policy as code, evaluated three times | Rules must not depend on the model remembering them | [ADR 0004](docs/adr/0004-policy-as-code.md) |
| Evaluate the evaluator first | An untested grader is indistinguishable from a good agent | [ADR 0005](docs/adr/0005-evaluate-the-evaluator.md) |
| Every number in a plan is computed by code | The model chooses what to change; costs, savings, risk and steps come from the analyzer | `agent/runtime.py::_build_plan` |
| The change request lists what was left out, and why | An approver needs to see the exclusions next to the proposals | `tools/catalog.py::SubmitPlanIn` |
| Untrusted metadata is withheld, not just flagged | An injection that never reaches the prompt cannot persuade anyone | `security/screening.py`, `tools/catalog.py` |
| Approvals bind to a plan hash; tokens bind to actions | Approve-then-change and unapproved code paths both fail closed | `agent/state.py::plan_hash`, `execution/tokens.py` |
| Statistics at the level of cases | Trials of one case are correlated; the case is the independent unit | [docs/evaluation.md](docs/evaluation.md) |

## Assumptions

- The estate is synthetic (149 resources, two AWS accounts, two Azure subscriptions, generated with a fixed
  seed) but in each provider's native response shapes, with every scenario and defect documented in
  [backend/fixtures/README.md](backend/fixtures/README.md). Prices are real: the official AWS Price List file
  and the Azure Retail Prices API.
- Monthly estimates use 730 hours. A stop, delete or release saves the resource's monthly cost; a resize saves
  the price difference to the next size down. Stopped VMs keep paying for their disks, and snapshot storage
  after a delete is not netted out; both make the estimate slightly high, and the plan says it is an estimate.
- Idle means p95 daily CPU at most 5% and peak at most 20% over at least 14 days with 80% coverage; oversized
  means p95 at most 15% and peak at most 40%; a disk must be unattached for 7 days.
- The personas (viewer, operator, two approvers) stand in for identities from an identity provider. In the
  public demo anyone can be any persona; separation of duties holds between personas.

## Test strategy

Tests are layered by what they prove, and every failure mode in [docs/failure-modes.md](docs/failure-modes.md)
names a test or an evaluation case that exists (CI fails on an untraced id or a renamed test). The full strategy,
including what is faked and what is not tested: [docs/testing.md](docs/testing.md).

| Layer | Count | What it proves |
|---|---|---|
| Unit (`-m unit`) | 98 | Pure rules: statistics against known values, policy (with a Hypothesis property: no combination of actions lets a blocked resource through), tokens, retries, the grammar |
| Integration (`-m integration`) | 120 | The runtime end to end on the real fixture estate: the critical workflow, every fault, crash-and-replay, leases, budgets, compensation, tenant isolation, the analyzer against every planted scenario, the documents against the code |
| API (four `api_*` markers) | 34 | Success, validation, authorization and failure paths over HTTP, problem+json shapes, idempotency, rate limits, security headers |
| Data validation (`-m data_validation`) | 12 | Every planted defect is caught: quarantine with reasons, deduplication, normalization, freshness |
| Claude contract (`-m claude_contract`) | 63 | The adapter against a mock of the Messages API with sockets refused: request shape, strict schemas, every stop reason, error mapping, key hygiene |
| PostgreSQL (`-m postgres`) | 2 | The workflow and a real two-thread race on one run (CI's PostgreSQL 17 service) |
| Frontend (Vitest) | 53 | Citations, the timeline reducer, money formatting, permissions, the trace layout, the auto-advance controller |
| End to end (Playwright) | 6 | The critical workflow in a browser, the production refusal, a pricing outage, RBAC, the evals page, the audit chain; also run against the live deployment |
| Evaluation | 30 cases | See below |

329 backend tests, 93% line and branch coverage of the `countersign` package, no warnings. Summary and screenshots:
[reports/tests/summary.md](reports/tests/summary.md).

## Evaluation

Every trial is a real run through the runtime: 30 cases x 3 trials, six mutants and 300 calibration runs, 570
runs in about 20 seconds. Six criteria, one grader each, all reading structured state (tool calls, submissions,
policy events, approvals, executions, spans), none calling a model.

| Criterion | Reference policy | Mutant built to fail it | Cases it flipped |
|---|---|---|---|
| Task completion | 30/30 | `quitter`: reads, then proposes nothing | 23 |
| Groundedness | 30/30 | `fabricator`: rounder, larger numbers and a citation it never read | 29 |
| Relevance | 30/30 | `scope_creeper`: ignores the request's filters | 28 |
| Tool accuracy | 30/30 | `sloppy_caller`: wrong argument name, repeats, skips price checks | 29 |
| Safety | 30/30 | `reckless`: proposes blocked resources, skips the dry run | 20 |
| Failure handling | 30/30 | `denier`: hides caveats and conflicts, never retries | 14 |

Mutation score 6/6 with zero grader false positives on the reference. 30/30 is reported with its Wilson
interval, [88.6%, 100%], because perfect on 30 cases is not proof of perfection; the minimum detectable effect is
6 one-directional case flips (exact McNemar). A seeded simulator with a known slip rate calibrates the intervals:
over 300 replicates the 95% interval covered the truth 97.7% of the time. The published single draw narrowly
missed (its slip rate came out 14% against 20%, z = -2.1); that is a 1-in-20 event, reported, not re-seeded.
Method, grammar and limits: [docs/evaluation.md](docs/evaluation.md). Report: [reports/evals/latest.md](reports/evals/latest.md).

![The Evals view: criteria with intervals, the mutation matrix and calibration](reports/tests/evals.png)

## CI/CD

| Workflow | Job | Checks |
|---|---|---|
| CI (push, PR) | backend | ruff, format, mypy strict, five import-linter contracts, pytest with a 90% coverage floor, the published report matches the code |
| | postgres | the workflow and a real two-worker race on PostgreSQL 17 |
| | frontend | Biome, `tsc -b`, Vitest, production build |
| | e2e | Playwright against a fresh seeded backend and the built UI; screenshots as artifacts |
| | evals | the full evaluation with its gate (no false positive, every mutant caught, no regression), then an exact comparison with the published report |
| Nightly | deep evals | 10 trials, 20 calibration trials, 1000 coverage replicates |
| | API fuzzing | authenticated, stateful Schemathesis that follows OpenAPI links into real runs: no input may produce a 5xx |
| | Live contracts | every Azure snapshot price still comes back live, trusted and within 25%; the deployed app is ready, keyless and current |
| | Claude | contract tests always; the live evaluation only if the repository has a key |

Deployment: one Vercel project with two services (`web`, `api`) from [vercel.json](vercel.json), Neon Postgres,
a daily cron that re-ingests and purges expired workspaces, deployed with the Vercel CLI from `main`.
Production refuses to start with development secrets.

## Failure modes

28 failure modes, each with its control, how it shows and its proof: [docs/failure-modes.md](docs/failure-modes.md).
A few that matter most:

| Failure | Control |
|---|---|
| Prompt injection in a tag | Screened at ingest; the model sees a flag, never the text; P-003 excludes the resource |
| The agent proposes production | P-001 at submission, decision and sandbox; a model that insists runs out of revisions and nothing executes |
| Approve one plan, run another | Approvals bind to the plan hash; the capability token names exact actions; the sandbox verifies it |
| Crash after a side effect | Idempotency keys per step; the next tick replays instead of repeating |
| Two workers, one run | Compare-and-set lease; tested with real threads on PostgreSQL |
| A resource changes after approval | Precondition checks; only that action fails; `completed_with_failures` |
| The agent hides a price outage | The failure-handling grader; the `denier` mutant proves it catches this |

## Keyless: why and how

The demo is public and has no login. If every visit called a paid model, anyone could run up the bill, and no
two runs would be the same. So the default model is the reference policy: it reads the conversation the way a
model does, calls the same tools with the same schemas, gets the same policy verdicts and goes through the same
approvals and executor. What it cannot show is how well a real model plans, and nothing here claims that.
Switching is one environment variable:

```sh
ANTHROPIC_API_KEY=sk-ant-...            # the API, the UI and countersign-eval now use Claude
COUNTERSIGN_MODEL_PROVIDER=offline      # force the reference policy even with a key
COUNTERSIGN_CLAUDE_MODEL=claude-opus-5-5
COUNTERSIGN_CLAUDE_EFFORT=high
```

## Built, automated, evaluated, mocked, deferred

| Area | Built | Automated | Evaluated | Mocked or stood in | Deferred |
|---|---|---|---|---|---|
| Ingestion and data quality | yes | 12 data tests, cron | defects planted and caught | sources are fixtures in native shapes | live provider APIs |
| Pricing | yes, live Azure + AWS snapshot | tests, nightly | price outage cases | AWS live prices (snapshot from the official file) | commitments, spot |
| Agent runtime | yes | integration tests, Postgres race | 30 cases x 3 | ticks driven by client or test | a queue-driven worker |
| Model | Claude adapter + reference policy | 63 contract tests | graders, mutants | the reference policy in the demo | live-model evaluation (wired, needs a key) |
| Policy and approvals | yes | unit + property tests | safety grader | personas instead of real identities | OIDC |
| Execution | yes, saga + idempotency | integration tests | failure handling | sandbox cloud instead of provider APIs | real API calls with least-privilege roles |
| Audit and tracing | yes | tamper tests | trace read by graders | head anchored in the same database | external anchoring |
| UI | yes | Vitest, Playwright local + live | e2e on the live app | | |

## Limitations

- One synthetic estate and 30 cases: they cover the failure modes I could name, not the long tail of real
  accounts.
- The sandbox cloud plays the provider APIs. A real integration needs least-privilege roles, the providers'
  own idempotency tokens and their eventual consistency.
- Rightsizing looks at CPU only; memory, network and burst credits would change some verdicts.
- Savings ignore reserved instances and savings plans, which can make a "saving" smaller.
- The offline grammar is English and small on purpose; it is a test double, not language understanding.
- The Claude path is contract-tested, not measured. Its quality on these cases is unknown until someone runs
  `countersign-eval run --model claude` with a key.

## With more time

- Run the evaluation on Claude at several efforts and compare them with the exact McNemar test.
- An LLM judge for the change request's prose (is it clear to a busy approver?), calibrated against human labels.
- Real AWS and Azure calls behind the same sandbox interface, with dry-run modes, least-privilege roles and the
  providers' idempotency tokens.
- A queue-driven worker for ticks, and the audit head anchored outside the database.
- Memory metrics and commitment-aware savings; more cases mined from real incidents.

## How AI was used

Claude Code (Claude Opus 5.5) wrote this repository in one long session, running inside the harness I built
for my own work: a verifier-gated goal loop whose definition of done is a single script with dozens of checks
(fresh-clone tests, the live app, the published article and film), role subagents working in separate git
worktrees (the UI and the Claude adapter were built in parallel and merged through pull requests), and an
isolated second-opinion review of the plan at plan freeze. I set the brief and its constraints: no money spent
by the public demo, fully pluggable into Claude, deployed live. The commits carry `Co-Authored-By: Claude`
trailers from the fourth one on; all of them were written with it.

The history reads as a build log: the tag `core` is the smallest version that does the whole job; later commits
harden it.

## License

MIT, see [LICENSE](LICENSE).
