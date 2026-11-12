from __future__ import annotations

from fixjury.telemetry import configure, current_trace_id, span


def test_a_span_has_a_trace_id() -> None:
    configure(console=False)

    with span("run", {"fixjury.task": "T-001"}):
        trace_id = current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    int(trace_id, 16)


def test_there_is_no_trace_id_outside_a_span() -> None:
    configure(console=False)

    assert current_trace_id() is None


def test_nested_spans_share_the_trace_id() -> None:
    configure(console=False)

    with span("run") as outer:
        outer_id = current_trace_id()
        with span("step", {"fixjury.step": 1}) as inner:
            inner_id = current_trace_id()

    assert outer_id == inner_id
    assert outer.get_span_context().trace_id == inner.get_span_context().trace_id


def test_attributes_land_on_the_span() -> None:
    configure(console=False)

    with span("step", {"fixjury.tool": "exec", "fixjury.step": 3}) as current:
        # The SDK span exposes its attributes; the API type does not.
        attributes = dict(current.attributes or {})  # type: ignore[attr-defined]

    assert attributes["fixjury.tool"] == "exec"
    assert attributes["fixjury.step"] == 3


def test_a_span_without_attributes_is_fine() -> None:
    configure(console=False)

    with span("run") as current:
        assert current.is_recording()
