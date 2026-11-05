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
exact McNemar with its minimum significant discordance and its exact power), calibrated against a seeded
simulator whose truth is known in closed form. A fingerprint ties the published report to the code: the
measurement's definition and the runtime, tools, analyzer, policy engine, sandbox and executor every trial runs
through.

## Consequences

The report can say what it measures and how sensitive it is. With 30 cases the minimum significant discordance
is 6 one-directional flips (20%): fewer can never be significant. That is a floor, not a detectable effect: for
two agents with per-case pass probabilities 0.8 and 0.6 that fail independently of each other, the exact test
reaches p < 0.05 only about 28% of the time (power computed exactly). The report says so instead of implying more precision than it has, and its
Wilson interval on 30 hand-picked cases is illustrative, with no sampling interpretation.
