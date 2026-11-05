# ADR 0001: The agent writes change requests, people countersign, code executes

Status: accepted, 2026-11-04

## Context

A cost agent is only useful if it can act, and only safe if it cannot act alone. Letting a model call
"stop instance" directly makes every prompt injection, hallucination and misread request a production incident.

## Decision

The model gets read-only tools and two control tools: `submit_change_plan` and `ask_operator`. A change request
is evaluated by policy-as-code, routed by risk (one or two approvers; never the requester), and executed by the
runtime only after the countersignatures, through an executor that needs a capability token minted by that
decision. Every number in a plan is computed by code from the analyzer, not taken from the model.

## Consequences

The model's mistakes become rejected proposals instead of outages. Throughput is bounded by human review,
which is the point for destructive changes; low-risk changes could later be auto-approved by policy without
changing the architecture.
