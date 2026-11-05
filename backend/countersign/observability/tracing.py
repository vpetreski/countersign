"""OpenTelemetry tracing with the GenAI semantic conventions (status: Development, open-telemetry/
semantic-conventions-genai): `invoke_agent countersign` per tick, `chat {model}` per model call and
`execute_tool {name}` per tool call.

Spans are exported synchronously into the run's own history (a SimpleSpanProcessor), so nothing is lost when
a serverless function freezes after responding and no collector is required to see a trace. All ticks of one
run share a trace id derived from the run id, so the waterfall reads as one trace.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, TraceFlags

AGENT_NAME = "countersign"
_bucket: ContextVar[list[ReadableSpan] | None] = ContextVar("countersign_spans", default=None)


class _RunExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        bucket = _bucket.get()
        if bucket is not None:
            bucket.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


_provider = TracerProvider(
    resource=Resource.create({"service.name": "countersign", "service.namespace": "countersign"})
)
_provider.add_span_processor(SimpleSpanProcessor(_RunExporter()))
tracer = _provider.get_tracer("countersign.agent", "1")


def _run_context(run_id: str) -> Context:
    digest = hashlib.sha256(run_id.encode()).hexdigest()
    parent = SpanContext(
        trace_id=int(digest[:32], 16),
        span_id=int(digest[32:48], 16),
        is_remote=True,
        trace_flags=TraceFlags(1),
    )
    return trace.set_span_in_context(NonRecordingSpan(parent))


@contextmanager
def collect() -> Iterator[list[dict[str, Any]]]:
    """Collect every span finished inside the block; the list is filled when the block exits."""
    bucket: list[ReadableSpan] = []
    token = _bucket.set(bucket)
    out: list[dict[str, Any]] = []
    try:
        yield out
    finally:
        _bucket.reset(token)
        out.extend(_as_dict(s) for s in bucket)


@contextmanager
def agent_span(run_id: str, status: str) -> Iterator[trace.Span]:
    with tracer.start_as_current_span(
        f"invoke_agent {AGENT_NAME}",
        context=_run_context(run_id),
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.conversation.id": run_id,
            "countersign.run.status": status,
        },
    ) as span:
        yield span


def provider_name(provider: str) -> str:
    """The offline reference policy is not a provider; its spans never claim to be Anthropic's."""
    return "anthropic" if provider == "anthropic" else "countersign.offline"


def _as_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.context
    parent = span.parent
    return {
        "trace_id": f"{ctx.trace_id:032x}" if ctx else "",
        "span_id": f"{ctx.span_id:016x}" if ctx else "",
        "parent_id": f"{parent.span_id:016x}" if parent and not parent.is_remote else None,
        "name": span.name,
        "kind": span.kind.name.lower(),
        "start_ns": span.start_time or 0,
        "end_ns": span.end_time or 0,
        "status": span.status.status_code.name.lower(),
        "attributes": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in (span.attributes or {}).items()
        },
    }
