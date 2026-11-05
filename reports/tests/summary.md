# Test results

Results for commit `9bb7497`, main after the second independent review round (#5 safety edges, #6 the
content-bound capability and the sandbox guardrails, #7 evaluation validity), recorded 5 November 2026. Local runs:
macOS 27 on arm64, Python 3.14.10 with uv 0.13.7, Node 26.13.0 with pnpm 10.34.9, Playwright 1.64.1 (Chromium),
PostgreSQL 16.15. CI: GitHub's Ubuntu runners, Node 24, PostgreSQL 17.

## Summary

| Check | Where | Result |
|---|---|---|
| Lint and format (ruff) | local, CI | clean, 106 files |
| Types (mypy strict) | local, CI | no issues in 63 source files |
| Architecture (import-linter) | local, CI | 5 contracts kept, 0 broken |
| Backend tests, SQLite and PostgreSQL | local | **385 passed**, 0 warnings, 100 s |
| Backend tests | CI [run 41989922736](https://github.com/vpetreski/countersign/actions/runs/41989922736) | 383 passed, 2 skipped (the PostgreSQL pair, run by the next job) |
| PostgreSQL 17 | CI, same run | 2 passed |
| Coverage of `countersign` | local | 94.2% with the PostgreSQL pair, 93.75% without it, lines and branches together (5,126 statements, 231 missed; 1,306 branches, 145 partial); the documents say 93%, rounded down |
| Frontend lint (Biome) and types (`tsc -b`) | local, CI | clean, 61 files |
| Frontend unit tests (Vitest) | local, CI | **57 passed** in 11 files |
| Production build | local, CI | built |
| Browser, `local` project | local, CI | **6 passed** (local 25 s, CI 34 s) |
| Browser, `live` project against https://countersign-agent.vercel.app | local | **6 passed** (50 s), on Neon PostgreSQL at schema 0002 |
| Live API: the critical workflow | local, against production | 8 actions, $682.00 a month, two countersigns, the policy rechecked at decision, completed, $682.00 realized, audit chain valid (16 entries) |
| Evaluation with its gate | CI, same run | 30 cases x 3 trials, every criterion 30/30, mutation score 6/6, 0 false positives, the 30 cases reproduce the published report exactly (fingerprint `5377214f2f11cd33`) |
| Deep evaluation | Nightly [run 41990667512](https://github.com/vpetreski/countersign/actions/runs/41990667512) on `9bb7497` | 30 cases x 10 trials, every criterion 30/30, mutation score 6/6, 0 false positives, interval coverage 99.5% over 1,000 replicates |
| API fuzzing (Schemathesis, stateful) | Nightly, same run | 3,134 cases, 204 stateful scenarios, API links covered 8 of 21 (13 of them inferred by Schemathesis), no 5xx |
| Claude contract tests | Nightly, same run | 63 passed; the live Claude evaluation skipped (no key, by design) |
| Live contracts | Nightly, same run | 18/18 Azure prices live, trusted and within 25% of the snapshot; the deployment ready, keyless, in production mode, build `9bb7497` |

The evaluation report itself, with intervals, the mutation matrix and calibration:
[reports/evals/latest.md](../evals/latest.md).

## A failure on the record

Nightly [run 41975490133](https://github.com/vpetreski/countersign/actions/runs/41975490133), on `e26007d`,
failed. It was the first run that followed OpenAPI links into real runs, and it found a 500 on
`GET /api/runs/{run_id}/events?after=140261119261041691197440`. The fix and its regression test are commit
`d29b2a7`; [docs/testing.md](../../docs/testing.md#what-the-fuzzer-found) has the details.

## Screenshots

Written by the `local` browser project on every run. A `live` run keeps its own with its results.

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
