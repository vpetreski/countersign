"""Screening of untrusted text: secret redaction and prompt-injection detection.

Both run at ingest, so a secret found in cloud metadata is never stored, shown or sent to a model, and a
record that tries to instruct the agent is flagged before any tool returns it. Detection is deliberately
simple and explainable; the authority of the agent is bounded by construction elsewhere (the policy engine
and the capability token), so a missed injection still cannot cause an unapproved change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"(?<![A-Z0-9])(AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    # 40 base64 characters; "=" may precede it (key=value) but is padding only at the end.
    (
        "aws_secret_access_key",
        re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{39}[A-Za-z0-9/+=](?![A-Za-z0-9/+=])"),
    ),
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assignment", re.compile(r"(?i)\b(pass(word)?|pwd|secret|token)\s*[:=]\s*\S+")),
    ("connection_string", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@]+@[^\s]+")),
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\bignore\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|polic(y|ies)|rules?)"
    ),
    re.compile(r"(?i)\b(system|developer)\s*(notice|prompt|message|override)\b"),
    re.compile(r"(?i)\b(to|for)\s+the\s+(ai|llm|agent|assistant|model)\b"),
    re.compile(
        r"(?i)\b(pre-?approved|approval is (pre-?)?granted|no approval (is )?needed|skip (the )?approval)\b"
    ),
    re.compile(r"(?i)\byou (must|should|are instructed to)\b"),
    re.compile(r"(?i)\b(disregard|override|bypass)\b.{0,30}\b(policy|guard|instructions?|rules?|safety)\b"),
)


@dataclass(frozen=True)
class Screened:
    text: str
    secrets: tuple[str, ...]
    injection: bool


def redact(text: str) -> tuple[str, tuple[str, ...]]:
    """Mask every secret-looking span; return the masked text and the kinds found."""
    kinds: list[str] = []
    out = text
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(out):
            kinds.append(kind)
            out = pattern.sub(REDACTED, out)
    return out, tuple(kinds)


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def screen(text: str) -> Screened:
    """Detect injection on the original text, then redact secrets."""
    injection = looks_like_injection(text)
    masked, secrets = redact(text)
    return Screened(masked, secrets, injection)


def contains_secret(text: str) -> bool:
    return any(p.search(text) for _, p in _SECRET_PATTERNS)
