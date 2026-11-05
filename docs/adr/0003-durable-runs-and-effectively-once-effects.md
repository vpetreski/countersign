# ADR 0003: Durable runs, one unit of work per tick, effectively-once effects

Status: accepted, 2026-11-04

## Context

Serverless functions die mid-request, two browser tabs can advance the same run, and a cloud API call can
succeed while the process that made it crashes before recording it.

## Decision

A run is a persisted state machine. Each tick (one model turn, one action, or the verification) takes a
compare-and-set lease, does its work and saves the state, events, spans and audit entries in one transaction.
Side effects use idempotency keys derived from the run, plan hash, action and step, committed separately, so a
replay after a crash is detected instead of repeated. Human wait time is not charged to the run's budget.

## Consequences

At-least-once attempts give effectively-once effects. Ticks are driven by the client or a test, not by a
background worker; a production deployment would put the same tick behind a queue or workflow engine.
