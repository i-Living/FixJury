"""OpenTelemetry spans for a run (PLAN §11, task 4.4).

Traces are optional: with no exporter configured the spans are created and
dropped, and the ids still reach ``runs.trace_id``. ``FIXJURY_OTEL_CONSOLE=1``
prints them, which is what the phase checkpoint asks for ("trace visible in the
OTel console").
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

_CONFIGURED = False
SCOPE = "fixjury"


def configure(console: bool | None = None) -> None:
    """Install a tracer provider once per process."""

    global _CONFIGURED
    if _CONFIGURED:
        return
    provider = TracerProvider()
    if console is None:
        console = os.environ.get("FIXJURY_OTEL_CONSOLE", "") not in {"", "0"}
    if console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


def tracer() -> trace.Tracer:
    configure()
    return trace.get_tracer(SCOPE)


type AttributeValue = str | int | float | bool


@contextmanager
def span(name: str, attributes: Mapping[str, AttributeValue] | None = None) -> Iterator[trace.Span]:
    """A span with attributes; exceptions are recorded, not swallowed."""

    with tracer().start_as_current_span(name) as current:
        for key, value in (attributes or {}).items():
            current.set_attribute(key, value)
        yield current


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or ``None`` when there is none."""

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")
