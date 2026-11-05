# ADR 0002: Keyless by default, Claude when a key is set

Status: accepted, 2026-11-04

## Context

The demo is public and unauthenticated. A page that spends model tokens on every visit is a cost-abuse vector,
and its results are not reproducible. Yet the system must work with a real model.

## Decision

A `ChatModel` protocol in Anthropic Messages semantics with two implementations: the Claude adapter (official
SDK, strict tools, adaptive thinking, prompt caching), selected when `ANTHROPIC_API_KEY` is set, and the offline
reference policy, a deterministic test double that works through the same tools, policy and runtime. The live
demo runs keyless; no paid call is ever made by this project.

## Consequences

The demo is free, fast and reproducible, and the harness around the model is what is shown: the runtime,
policy, approvals, execution and evaluation. The claim is stated plainly everywhere: offline results validate
the harness, graders and statistics, not LLM quality; the Claude path is contract-tested against the SDK, not
run against the API.
