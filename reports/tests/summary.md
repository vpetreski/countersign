# Test results

Results for commit `86da46b` (main `8250ef1`), after the isolated reviews: #5 safety edges, #6 the content-bound
capability and the sandbox guardrails, #7 evaluation validity, #8 undo and plan-integrity checks at their boundaries
and the grader's vocabulary. Recorded 5 November 2026. Local runs: macOS 27 on arm64, Python 3.14.10 with uv 0.13.7,
Node 26.13.0 with pnpm 10.34.9, Playwright 1.64.1 (Chromium), PostgreSQL 16.15. CI: GitHub's Ubuntu runners, Node 24,
PostgreSQL 17.

## Summary

| Check | Where | Result |
|---|---|---|
| Lint and format (ruff) | local, CI | clean, 106 files |
| Types (mypy strict) | local, CI | no issues in 64 source files |
| Architecture (import-linter) | local, CI | 5 contracts kept, 0 broken |
| Backend tests, SQLite and PostgreSQL | local | **404 passed**, 0 warnings, 40 s |
| Backend tests | CI [run 41995289195](https://github.com/vpetreski/countersign/actions/runs/41995289195) (main) | 401 passed, 3 skipped (the PostgreSQL three, run by the next job) |
| PostgreSQL 17 | CI, same run | 3 passed |
| Coverage of `countersign` | local | 94.2% with the PostgreSQL three, 94.1% without them, lines and branches together (5,152 statements, 231 missed; 1,318 branches, 145 partial); the documents say 94%, rounded down |
| Frontend lint (Biome) and types (`tsc -b`) | local, CI | clean, 61 files |
| Frontend unit tests (Vitest) | local, CI | **57 passed** in 11 files |
| Production build | CI | built |
| Browser, `local` project | CI, same run | **6 passed** (32 s) |
| Browser, `live` project against https://countersign-agent.vercel.app | local | **6 passed** (46 s), on Neon PostgreSQL at schema 0002 |
| Live API: the critical workflow | local, against production | 8 actions, $682.00 a month, two countersigns, the policy rechecked at decision, completed, $682.00 realized, audit chain valid (16 entries; run `run_f9ec20e1a29011b7`) |
| Evaluation with its gate | CI, same run | 30 cases x 3 trials, every criterion 30/30, mutation score 6/6, 0 false positives, the 30 cases reproduce the published report exactly (fingerprint `2553533bdecf50ad`) |
| Deep evaluation | Nightly [run 41995691888](https://github.com/vpetreski/countersign/actions/runs/41995691888) on `8250ef1` | 30 cases x 10 trials, every criterion 30/30, mutation score 6/6, 0 false positives, interval coverage 99.5% over 1,000 replicates of 20 calibration trials per case |
| API fuzzing (Schemathesis, stateful) | Nightly, same run | 3,119 cases, 206 stateful scenarios, API links covered 8 of 21 (13 of them inferred by Schemathesis), no 5xx |
| Claude contract tests | Nightly, same run | 63 passed; the live Claude evaluation skipped (no key, by design) |
| Live contracts | Nightly, same run | 18/18 Azure prices live, trusted and within 25% of the snapshot; the deployment ready, keyless, in production mode, build `86da46b` |

The evaluation report itself, with intervals, the mutation matrix and calibration:
[reports/evals/latest.md](../evals/latest.md).

## A failure on the record

Nightly [run 41975490133](https://github.com/vpetreski/countersign/actions/runs/41975490133), on `e26007d`,
failed. It was the first run that followed OpenAPI links into real runs, and it found a 500 on
`GET /api/runs/{run_id}/events?after=140261119261041691197440`. The fix and its regression test are commit
`d29b2a7`; [docs/testing.md](../../docs/testing.md#what-the-fuzzer-found) has the details.

## Screenshots

Written by the `local` browser project on every run. A `live` run keeps its own with its results. These were taken
on `9bb7497`; #8 changed no part of the interface.

| File | What it shows |
|---|---|
| [critical-01-plan.png](critical-01-plan.png) | The change request: 8 actions with evidence chips, policy checks, risk and the countersign panel |
| [critical-02-countersigned.png](critical-02-countersigned.png) | Both countersigns in: 2 of 2 approvals, each signer named, the capability minted |
| [critical-03-completed.png](critical-03-completed.png) | Executed and verified: savings realized, each step's outcome |
| [trace.png](trace.png) | The run's trace: 42 spans (`invoke_agent`, `chat`, `execute_tool`, `execute_action`) with `gen_ai.*` attributes, idle gaps collapsed |
| [policy-production.png](policy-production.png) | A request to stop a production replica: no change request, the replica in the not-proposed list with the reason |
| [fault-pricing-outage.png](fault-pricing-outage.png) | Every price source down: the degraded banner and the pricing caveat in the change request |
| [rbac-viewer.png](rbac-viewer.png) | A viewer: the start button disabled, with the reason |
| [estate.png](estate.png) | The Estate view: findings across AWS and Azure |
| [estate-resource.png](estate-resource.png) | The VM whose `Name` tag carries an injection: flagged as instruction-like text, never shown to the model |
| [evals.png](evals.png) | The Evals view: criteria with intervals, the mutation matrix, calibration |
| [audit.png](audit.png) | The audit log after a run, the hash chain verified |
