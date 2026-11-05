# Test results

Results for commit `d29b2a7`, the last change to the application and its tests, recorded 5 November 2026.
`5a5bfd2` adds the nightly live-contract job (its first run is below); later commits change documents only. Local runs: macOS 27 on arm64, Python 3.14.10 with uv 0.13.7, Node 26.13.0 with pnpm 10.34.9,
Playwright 1.64.1 (Chromium), PostgreSQL 16.15. CI: GitHub's Ubuntu runners, Node 24, PostgreSQL 17.

## Summary

| Check | Where | Result |
|---|---|---|
| Lint and format (ruff) | local, CI | clean, 103 files |
| Types (mypy strict) | local, CI | no issues in 64 source files |
| Architecture (import-linter) | local, CI | 5 contracts kept, 0 broken |
| Backend tests, SQLite and PostgreSQL | local | **325 passed**, 0 warnings, 30 s |
| Backend tests | CI [run 41976079986](https://github.com/vpetreski/countersign/actions/runs/41976079986) | 323 passed, 2 skipped (the PostgreSQL pair, run by the next job) |
| PostgreSQL 17 | CI, same run | 2 passed |
| Coverage of `countersign` | local | 93%, lines and branches together (4,930 statements, 230 missed; 1,230 branches, 147 partial) |
| Frontend lint (Biome) and types (`tsc -b`) | local, CI | clean, 59 files |
| Frontend unit tests (Vitest) | local, CI | **53 passed** in 9 files |
| Production build | local, CI | built |
| Browser, `local` project | local, CI | **6 passed** (CI 34 s) |
| Browser, `live` project against https://countersign-agent.vercel.app | local | **6 passed** (40 s) |
| Live API: the critical workflow | local, against production | 8 actions, $682.00 a month, requester refused, two countersigns, completed, audit chain valid |
| Evaluation with its gate | CI, same run | 30 cases x 3 trials, every criterion 30/30, mutation score 6/6, 0 false positives, matches the published report |
| Deep evaluation | Nightly [run 41976086210](https://github.com/vpetreski/countersign/actions/runs/41976086210) | 30 cases x 10 trials, every criterion 30/30, mutation score 6/6, interval coverage 99.5% over 1,000 replicates |
| API fuzzing (Schemathesis, stateful) | Nightly, same run | 3,139 cases, 202 stateful scenarios, 8 of 8 OpenAPI links followed, no 5xx |
| Claude contract tests | Nightly, same run | 63 passed; the live Claude evaluation skipped (no key, by design) |
| Live contracts | Nightly [run 41976889601](https://github.com/vpetreski/countersign/actions/runs/41976889601) on `5a5bfd2` | 18/18 Azure prices live, trusted and within 25% of the snapshot; the deployment ready, keyless, build `5a5bfd2`; that run's fuzzing (3,189 cases) and deep evaluation also green |

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
