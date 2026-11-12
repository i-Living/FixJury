"""The baseline agent: the model in the platform process, tools in the sandbox.

Invariant 7 forbids the baseline from calling the model inside the container, so
the loop lives here and only the tool calls cross into the sandbox. The model
never supplies the diff: it can only edit files, and the platform captures what
changed afterwards.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from fixjury.agents.base import AgentResult, ModelCall, RunRequest
from fixjury.config import settings
from fixjury.llm import complete
from fixjury.sandbox.container import SandboxHandle, SandboxSpec, open_sandbox
from fixjury.sandbox.diff import DiffError, UnreadableDiff, capture_diff
from fixjury.sandbox.tools import (
    ToolError,
    exec_command,
    list_dir,
    read_file,
    write_file,
)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "baseline_agent.md"
DEFAULT_MAX_STEPS = 30
TOOL_NAMES = frozenset({"exec", "read", "write", "list", "finish"})


class ProtocolError(RuntimeError):
    """The model answered with something the loop cannot execute."""


@dataclass(frozen=True)
class Step:
    """One decoded model answer."""

    tool: str
    arguments: dict[str, Any]


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def parse_step(text: str) -> Step:
    """Decode one answer. A fenced block is tolerated; anything else is not."""

    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :] if "{" in body else body
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ProtocolError("answer is not a JSON object")
    try:
        payload = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"answer is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("answer is not a JSON object")
    tool = payload.get("tool")
    if not isinstance(tool, str) or tool not in TOOL_NAMES:
        raise ProtocolError(f"unknown tool {tool!r}")
    return Step(tool=tool, arguments={str(key): value for key, value in payload.items()})


async def _apply_step(handle: SandboxHandle, step: Step) -> str:
    """Run one tool call and return the observation shown to the model."""

    if step.tool == "exec":
        argv = step.arguments.get("argv")
        if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
            raise ProtocolError("exec needs an argv list of strings")
        timeout = step.arguments.get("timeout_s", 120)
        timeout_s = float(timeout) if isinstance(timeout, int | float) else 120.0
        result = await exec_command(handle, argv, timeout_s=timeout_s)
        return f"exit_code={result.exit_code}\n{result.output}"

    if step.tool == "read":
        path = step.arguments.get("path")
        if not isinstance(path, str):
            raise ProtocolError("read needs a path")
        return await read_file(handle, path)

    if step.tool == "write":
        path = step.arguments.get("path")
        content = step.arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ProtocolError("write needs a path and content")
        await write_file(handle, path, content)
        return f"wrote {path} ({len(content)} bytes)"

    if step.tool == "list":
        path = step.arguments.get("path", ".")
        target = path if isinstance(path, str) else "."
        return "\n".join(await list_dir(handle, target))

    raise ProtocolError(f"tool {step.tool!r} is not executable")


async def run_baseline(
    request: RunRequest, handle: SandboxHandle, call_model: ModelCall
) -> AgentResult:
    """Drive the model until it finishes or runs out of steps."""

    system = load_prompt().replace("{spec}", request.spec)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Begin. Answer with one JSON object."},
    ]
    tokens_in = 0
    tokens_out = 0
    steps = 0
    declared_success = False
    unable_reason: str | None = "step limit reached before the model finished"
    summary = ""
    started = time.monotonic()

    while steps < request.max_steps:
        steps += 1
        answer, used_in, used_out = await call_model(messages)
        tokens_in += used_in
        tokens_out += used_out
        messages.append({"role": "assistant", "content": answer})
        try:
            step = parse_step(answer)
            if step.tool == "finish":
                declared_success = step.arguments.get("unable") is not True
                unable_reason = None if declared_success else "model reported unable"
                summary = str(step.arguments.get("summary", ""))
                break
            observation = await _apply_step(handle, step)
        except (ProtocolError, ToolError) as exc:
            messages.append({"role": "user", "content": f"error: {exc}. Try again."})
            continue
        messages.append({"role": "user", "content": f"observation:\n{observation}"})

    elapsed = Decimal(str(round(time.monotonic() - started, 2)))
    try:
        captured = capture_diff(request.workspace)
    except UnreadableDiff as exc:
        return AgentResult(
            diff="",
            diff_sha256="",
            declared_success=False,
            unable_reason=f"UNREADABLE_DIFF: {exc}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            steps=steps,
            sandbox_seconds=elapsed,
            summary=summary,
        )
    except DiffError as exc:
        return AgentResult(
            diff="",
            diff_sha256="",
            declared_success=False,
            unable_reason=f"diff capture failed: {exc}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            steps=steps,
            sandbox_seconds=elapsed,
            summary=summary,
        )

    return AgentResult(
        diff=captured.diff,
        diff_sha256=captured.sha256,
        declared_success=declared_success,
        unable_reason=unable_reason,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps=steps,
        sandbox_seconds=elapsed,
        summary=summary,
    )


class BaselineAdapter:
    """The reference agent: one model, one sandbox, no external harness."""

    name = "baseline"
    version = "1"

    async def run(self, request: RunRequest) -> AgentResult:
        spec = SandboxSpec(image=request.image, workspace=request.workspace)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:

            async def call_model(messages: list[dict[str, str]]) -> tuple[str, int, int]:
                return await complete(
                    client=client,
                    base_url=settings.llm_base_url,
                    model=settings.llm_model,
                    messages=messages,
                    api_key=settings.llm_api_key,
                    session=settings.llm_session,
                )

            opened = time.monotonic()
            async with open_sandbox(spec) as handle:
                result = await run_baseline(request, handle, call_model)
            # The run costs sandbox time, not loop time: the container is alive
            # from creation to removal, and that is what a run is billed for.
            lifetime = Decimal(str(round(time.monotonic() - opened, 2)))
            return replace(result, sandbox_seconds=lifetime)
