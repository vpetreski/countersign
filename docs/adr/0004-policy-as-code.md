# ADR 0004: Policy as code, evaluated three times

Status: accepted, 2026-11-04

## Context

Rules such as "never touch production" and "two approvers above $1,000 a month" must not depend on the model
remembering them, and must be reviewable by people who are not reading Python.

## Decision

`policy/policy.yaml` declares eight rules (production, protection tags, untrusted metadata, grounding, data
sufficiency, known environment, plan size, known resource), risk by operation and the approval thresholds. The
engine evaluates a plan when the model submits it, again when an approver decides, and a third time in effect
at the sandbox, through the capability token that only names actions that passed. The model can call
`check_policy` as a dry run.

## Consequences

The policy is versioned with the code and covered by property tests (no combination of proposed actions lets a
blocked resource through). Changing a rule changes the evaluation fingerprint, so the published report always
states which policy it measured.
