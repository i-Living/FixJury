"""Headless entry point for the OpenHands SDK, pinned inside the harness image.

OpenHands 1.x has no headless flag: ``openhands`` was the terminal UI and the GUI
launcher, and both want a terminal a sandbox does not have. This driver is the
missing entry point — it builds the agent the CLI builds, runs it once on the task
file, and writes the trajectory the platform reads back.

Nothing here is FixJury policy. The model, the endpoint, the key, the session header
and the step ceiling arrive in a JSON file written by the platform for this one run,
and the file is deleted before the diff is captured.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, LocalWorkspace, Observation
from openhands.sdk.event.base import Event
from openhands.tools import get_default_tools

TRAJECTORY_FORMAT = "fixjury-openhands-1"
DEFAULT_TIMEOUT_S = 180.0
# How many failed observations to keep. A run that ends in `error` is explained by
# the last one; the earlier ones only add noise to a database column.
NOTES_KEPT = 3
# The SDK reports a spent step ceiling or a spent budget as a plain ERROR. Neither is
# a crash: the agent stopped because a limit the platform set said stop, and such a
# run deserves a name of its own instead of "error".
LIMIT_CODES = frozenset({"MaxIterationsReached", "MaxBudgetReached"})


def event_note(event: Event) -> str:
    """What an event adds to the run's story: a stop code or a failed tool call.

    A conversation that ends early has lost its reason — the SDK keeps the status, not
    a message. Without these notes the platform records "status error" and nothing
    else, and a spent step ceiling is indistinguishable from a provider that died.
    """

    code = str(getattr(event, "code", ""))
    if code:
        detail = " ".join(str(getattr(event, "detail", "")).split())
        return f"{code}: {detail}"[:400]
    observation = getattr(event, "observation", None)
    if not isinstance(observation, Observation) or not observation.is_error:
        return ""
    text = " ".join(str(part) for part in observation.content)
    return " ".join(text.split())[:400]


def build_llm(config: dict[str, object]) -> LLM:
    """The model exactly as the run spec names it, plus the session header."""

    session = str(config.get("session") or "")
    headers = {"x-opencode-session": session} if session else {}
    return LLM(
        model=str(config["model"]),
        base_url=str(config["base_url"]),
        api_key=str(config["api_key"]),
        usage_id="agent",
        extra_headers=headers,
        num_retries=2,
        timeout=float(config.get("timeout_s") or DEFAULT_TIMEOUT_S),
        drop_params=True,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write("usage: openhands_driver.py CONFIG TASK_FILE TRAJECTORY_FILE\n")
        return 2
    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    task = Path(argv[2]).read_text(encoding="utf-8")
    trajectory = Path(argv[3])

    # The default tool set minus the browser: the sandbox has no route out, so a
    # browser tool can only fail — and it refuses to start without Chromium, which
    # would take down the run before the agent reads the task.
    agent = Agent(
        llm=build_llm(config),
        tools=get_default_tools(enable_browser=False),
        system_prompt_kwargs={"cli_mode": True},
    )
    workspace = Path(os.getcwd())
    # The last failed observations, kept in step with the conversation by the SDK's
    # own callback: the status alone does not say why a run ended in `error`.
    notes: list[str] = []

    def collect(event: Event) -> None:
        text = event_note(event)
        if text:
            notes.append(text)
            del notes[:-NOTES_KEPT]

    with tempfile.TemporaryDirectory(prefix="fixjury-openhands-") as scratch:
        conversation = Conversation(
            agent=agent,
            workspace=LocalWorkspace(working_dir=str(workspace)),
            persistence_dir=scratch,
            max_iteration_per_run=int(config.get("max_steps") or 60),
            visualizer=None,
            delete_on_close=True,
            callbacks=[collect],
        )
        conversation.send_message(task)
        conversation.run()

        state = conversation.state
        status = getattr(state.execution_status, "value", str(state.execution_status))
        if status == "error" and any(note.split(":")[0] in LIMIT_CODES for note in notes):
            # A ceiling is not a crash: the agent used every step the platform allowed.
            status = "step_limit"
        tokens_in = 0
        tokens_out = 0
        calls = 0
        for metrics in conversation.conversation_stats.usage_to_metrics.values():
            usage = metrics.accumulated_token_usage
            if usage is not None:
                tokens_in += usage.prompt_tokens
                tokens_out += usage.completion_tokens
            calls += len(metrics.token_usages)

    trajectory.parent.mkdir(parents=True, exist_ok=True)
    trajectory.write_text(
        json.dumps(
            {
                "trajectory_format": TRAJECTORY_FORMAT,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "steps": calls,
                "exit_status": status,
                "notes": " | ".join(notes),
            }
        ),
        encoding="utf-8",
    )
    for note in notes:
        print(f"openhands: last failed observation: {note}")
    print(f"openhands: status {status}, {calls} model call(s), {tokens_in}/{tokens_out} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
