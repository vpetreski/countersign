# ADR 0005: Evaluate the evaluator before the agent

Status: accepted, 2026-11-04

## Context

Agent evaluations often report high scores from graders nobody has tested. A grader that passes everything is
indistinguishable from a good agent.

## Decision

Graders read structured state (tool calls, submissions, policy events, approvals, executions, spans), not prose
alone. They are validated three ways before any agent score is reported: planted-defect records per grader, six
frozen mutants that must each be caught by their expected grader, and zero false positives on the reference
policy. Statistics are case-level (cluster bootstrap with a Wilson fallback, Wilson on all-pass cases, pass^k,
exact McNemar, the minimum detectable effect), calibrated against a seeded simulator whose truth is known in
closed form. A fingerprint ties the published report to the code.

## Consequences

The report can say what it measures and how sensitive it is. With 30 cases the minimum detectable effect is 6
one-directional flips (20%); the report says so instead of implying more precision than it has.
