# Security

The threat model of an agent that can change cloud resources, and what stands between a bad input and a bad
change. The short version: the model has no write path. Every change needs a policy verdict, the right number
of human countersignatures from people other than the requester, and a capability token that names the exact
actions, verified again where the change lands.

## Threats and controls

| Threat | Control | Where |
|---|---|---|
| Prompt injection through cloud metadata (tags, names) | Screened at ingest; flagged text is withheld from every tool result (the model sees the flag, never the text); the resource is excluded by P-003 until a human reviews it | `security/screening.py`, `tools/catalog.py`, `policy/policy.yaml` |
| Secrets in metadata | Redacted at ingest, before storage, so they never reach the database, a tool, a model or a screen | `security/screening.py`, `data/normalize.py` |
| A model that proposes something dangerous | Read-only tools; policy-as-code at submission, again at decision against the current estate (before the token is minted), and at the sandbox through the token and its precondition checks; revisions are bounded and a model that insists fails the run with nothing executed | `agent/runtime.py`, `policy/engine.py` |
| Approve one plan, execute another | Approvals bind to the SHA-256 plan hash (summary, caveats, actions, exclusions, policy version); any change invalidates them | `agent/state.py::plan_hash` |
| A code path that executes without approval | The executor needs an HMAC-SHA256 capability token scoped to run, workspace, plan hash, action ids and approvers, with an expiry; the sandbox verifies it on every step and fails closed | `execution/tokens.py`, `execution/sandbox.py` |
| Self-approval, double counting | Separation of duties; one decision per approver per plan; two distinct approvers for high risk or >= $1,000/month | `policy/policy.yaml`, `agent/runtime.py::_decide` |
| Cross-tenant access | Every query is scoped by the workspace in the signed token; effects are a per-workspace overlay | `auth/personas.py`, `data/estate.py`, `execution/sandbox.py` |
| Token forgery or replay of old tokens | HS256 JWTs with `aud`, `iss`, `exp`, `nbf`, `iat` all required and checked against the service clock; short TTL | `auth/personas.py` |
| Audit tampering | Hash chain per workspace; `/api/audit/verify` recomputes every link | `audit/chain.py` |
| Cost abuse of a public demo | Keyless by default (no model spend at all); fixed-window rate limits per workspace and client; workspaces expire after 72 hours | `settings.py`, `api/app.py` |
| Weak production configuration | Production refuses to start unless the JWT, capability and cron secrets are set and at least 32 characters | `settings.py::assert_production_safe` |
| Leaking the model key | The key lives only in settings; the SDK gets it explicitly; errors are redacted; the key never appears in repr, logs or spans (contract-tested) | `models/claude.py`, `tests/models/` |
| Response hardening | `X-Content-Type-Options`, `X-Frame-Options: DENY`, a strict `Referrer-Policy`, a content security policy, and problem+json errors that never echo internals | `api/app.py` |

## Honest limits

- The capability token protects against mistakes in code paths and against approve-then-change. A compromised
  server holds the signing secret and is out of scope.
- The public demo hands out four personas per workspace, so anyone can approve. Separation of duties is real
  between personas; in production the personas would be real identities from an identity provider.
- The audit chain is in the same database as the data. It detects edits and deletions; anchoring the head
  outside the database (a transparency log, a write-once bucket) would make it tamper-evident against a
  database administrator too.
- Injection screening is a heuristic and will miss things. The design does not depend on it: a missed
  injection can still only lead to a proposal, which the policy and people dispose of.
- The sandbox cloud stands in for real provider APIs. A real integration would also need least-privilege IAM
  roles scoped to the stop, resize, snapshot, delete and release calls, and the provider's own idempotency
  tokens where they exist (for example EC2's `ClientToken`).
