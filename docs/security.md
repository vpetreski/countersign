# Security

The threat model of an agent that can change cloud resources, and what stands between a bad input and a bad
change. The short version: the model has no write path. Every change needs a policy verdict, the right number
of human countersignatures from people other than the requester, and a capability token that binds the exact
content of each approved action, verified again where the change lands, where the sandbox's own guardrails
refuse production, untagged, protected and flagged resources whatever the token says.

## Threats and controls

| Threat | Control | Where |
|---|---|---|
| Prompt injection through cloud metadata (tags, names) | Screened at ingest: every tag value, and the team and owner a disk inherits from its VM. A flagged resource's name, tags, team and owner are withheld from every tool result (the model sees the flag, never the text); the resource is excluded by P-003 until a human reviews it | `security/screening.py`, `data/normalize.py`, `data/ingest.py`, `tools/catalog.py`, `policy/policy.yaml` |
| Secrets in metadata | Redacted at ingest, before storage: tag values, and the raw text and reason of a quarantined record (refused before its tags are screened). They never reach the database, a tool, a model or a screen | `security/screening.py`, `data/normalize.py`, `data/sources/batch.py` |
| A model that proposes something dangerous | Read-only tools; policy-as-code at submission, again at decision against the current estate (before the token is minted), and at the sandbox through the token, its guardrails and its precondition checks; revisions are bounded and a model that insists fails the run with nothing executed | `agent/runtime.py`, `policy/engine.py` |
| Approve one plan, execute another | Approvals bind to the SHA-256 plan hash (summary, caveats, actions, exclusions, policy version); any change invalidates them | `agent/state.py::plan_hash` |
| A code path that executes without approval | The executor needs an HMAC-SHA256 capability token scoped to run, workspace, plan hash and approvers, binding each action id to a SHA-256 digest of the action's content, with a one-hour expiry; the sandbox verifies it on every step and fails closed: an action edited after the countersignatures, even under the same id, is refused; an expired token still lets the executor replay or undo what it named, never start a new effect | `execution/tokens.py`, `execution/sandbox.py` |
| A bug above the sandbox (a wrong verdict, a mis-minted token) | Guardrails in the sandbox itself: every forward effect reads the resource as it is now and refuses production, an unknown environment, a protection tag or flagged metadata; compensations, which restore a prior state, still run | `execution/sandbox.py::guardrail` |
| Self-approval, double counting | Separation of duties; one decision per approver per plan; two distinct approvers for high risk or >= $1,000/month | `policy/policy.yaml`, `agent/runtime.py::_decide` |
| Cross-tenant access | Every query is scoped by the workspace in the signed token; effects are a per-workspace overlay | `auth/personas.py`, `data/estate.py`, `execution/sandbox.py` |
| Token forgery or replay of old tokens | HS256 JWTs with `aud`, `iss`, `exp`, `nbf` and `iat` all required; `aud` and `iss` must match, `exp` and `nbf` are checked against the service clock, `iat` is required but not checked; short TTL | `auth/personas.py` |
| Audit tampering | Hash chain per workspace; `/api/audit/verify` recomputes every link | `audit/chain.py` |
| Cost abuse of a public demo | Keyless by default (no model spend at all); fixed-window rate limits per workspace and client; workspaces expire after 72 hours | `settings.py`, `api/app.py` |
| A runaway agent on a paid model | Budgets on turns, tool calls (held inside a single turn), input tokens (cache reads and writes included), planning time and revisions; the lease covers the slowest model call and is renewed before each retry, so a second worker never repeats a paid call | `agent/runtime.py`, `agent/state.py` |
| Weak production configuration | Production refuses to start unless the JWT, capability and cron secrets are set and at least 32 characters. Production is `COUNTERSIGN_ENV=production`, and any Vercel production deployment (`VERCEL_ENV=production`) whatever `COUNTERSIGN_ENV` says; the nightly live check fails unless the deployment reports `production` | `settings.py::assert_production_safe`, `.github/workflows/nightly.yml` |
| Leaking the model key | The key lives only in settings; the SDK gets it explicitly; errors are redacted; the key never appears in repr, logs or spans (contract-tested) | `models/claude.py`, `tests/models/` |
| Response hardening | `X-Content-Type-Options`, `X-Frame-Options: DENY`, a strict `Referrer-Policy`, a content security policy, and problem+json errors that never echo internals; problem responses set the headers themselves, so a 500 carries them too | `api/app.py` |

## Honest limits

- The capability token protects against mistakes in code paths and against approve-then-change, including an
  edit to an approved action that keeps its id. A compromised server holds the signing secret and is out of
  scope.
- The public demo hands out four personas per workspace, so anyone can approve. Separation of duties is real
  between personas; in production the personas would be real identities from an identity provider.
- The audit chain is in the same database as the data. It detects an edited, reordered or deleted entry; the
  removal of the newest entries, or a rewrite of the whole chain by a database administrator, needs the head
  anchored outside the database (a transparency log, a write-once bucket).
- The per-client rate limit keys on the first `X-Forwarded-For` address. That relies on Vercel overwriting the
  header with the connecting client's address, which its request-headers documentation says it does to prevent
  spoofing; behind a proxy that passes a client's own header through, a visitor could choose their bucket.
- Injection screening is a heuristic and will miss things. The design does not depend on it: a missed
  injection can still only lead to a proposal, which the policy and people dispose of.
- The sandbox cloud stands in for real provider APIs. A real integration would also need least-privilege IAM
  roles scoped to the stop, resize, snapshot, delete and release calls, and the provider's own idempotency
  tokens where they exist (for example EC2's `ClientToken`).
