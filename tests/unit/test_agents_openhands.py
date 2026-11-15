"""OpenHands as the container harness: config, trajectory, outcome, wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixjury.agents import openhands
from fixjury.agents.base import LlmConfig
from fixjury.agents.container_harness import HarnessError, HarnessSpec, outcome
from fixjury.agents.registry import harness_spec
from fixjury.agents.trajectory import HarnessRun
from fixjury.config import settings

LLM = LlmConfig(
    base_url="https://opencode.ai/zen/go/v1",
    model="opencode-go/deepseek-v4.1-flash",
    api_key="secret-key",
    session="session-1",
)


def _trajectory(*, status: str = "finished") -> dict[str, object]:
    return {
        "trajectory_format": "fixjury-openhands-1",
        "tokens_in": 1200,
        "tokens_out": 240,
        "steps": 7,
        "exit_status": status,
    }


def test_the_config_is_json_and_carries_the_endpoint_without_argv() -> None:
    parsed = json.loads(openhands.config_text(LLM, max_steps=12))

    assert parsed["model"] == LLM.model
    assert parsed["base_url"] == LLM.base_url
    assert parsed["api_key"] == LLM.api_key
    assert parsed["session"] == "session-1"
    assert parsed["max_steps"] == 12
    # No key and no prompt in argv: argv is visible in `docker exec`.
    assert "secret-key" not in " ".join(openhands.COMMAND)


def test_a_bare_model_name_is_named_for_litellm() -> None:
    """The SDK reaches the model through litellm, which needs a provider prefix."""

    bare = LlmConfig(base_url=LLM.base_url, model="deepseek-v4.1-flash")

    assert json.loads(openhands.config_text(bare, max_steps=1))["model"] == (
        "openai/deepseek-v4.1-flash"
    )


def test_a_run_without_an_llm_still_produces_a_config() -> None:
    parsed = json.loads(openhands.config_text(None, max_steps=3))

    assert parsed["model"] == ""
    assert parsed["api_key"] == ""
    assert parsed["session"] == ""


def test_the_trajectory_gives_tokens_and_steps(tmp_path: Path) -> None:
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(_trajectory()))

    assert openhands.read_run(path) == HarnessRun(
        tokens_in=1200, tokens_out=240, steps=7, exit_status="finished"
    )


def test_a_missing_trajectory_is_not_a_crash(tmp_path: Path) -> None:
    assert openhands.read_run(tmp_path / "nope.json") is None


def test_a_trajectory_of_another_tool_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "traj.json"
    path.write_text(json.dumps({"trajectory_format": "mini-swe-agent", "messages": []}))

    assert openhands.read_run(path) is None


def test_broken_fields_degrade_to_zero_instead_of_guessing(tmp_path: Path) -> None:
    payload = _trajectory()
    payload["tokens_in"] = "1200"
    payload["steps"] = True
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(payload))

    run = openhands.read_run(path)

    assert run is not None
    assert run.tokens_in == 0, "a string is not a token count"
    assert run.steps == 0, "a boolean is not a step count"
    assert run.tokens_out == 240, "the readable fields survive"


def test_the_step_ceiling_is_not_a_completion() -> None:
    """The conversation pauses at the ceiling; only the agent's finish ends it."""

    paused = HarnessRun(tokens_in=1, tokens_out=1, steps=60, exit_status="paused")

    declared, reason = outcome(0, False, paused, openhands.SUCCESS_STATUS)

    assert declared is False
    assert reason == "harness stopped with status paused"


def test_the_note_the_harness_wrote_reaches_the_run_reason() -> None:
    """`status error` on its own says nothing; the last failed observation does."""

    failed = HarnessRun(
        tokens_in=1,
        tokens_out=1,
        steps=12,
        exit_status="error",
        notes="APIError: context length exceeded for model deepseek-v4.1-flash",
    )

    declared, reason = outcome(0, False, failed, openhands.SUCCESS_STATUS)

    assert declared is False
    assert reason == (
        "harness stopped with status error: "
        "APIError: context length exceeded for model deepseek-v4.1-flash"
    )


def test_a_spent_step_ceiling_is_its_own_status_not_a_crash() -> None:
    """The SDK calls the ceiling ERROR; the platform calls it what it is."""

    at_ceiling = HarnessRun(
        tokens_in=1,
        tokens_out=1,
        steps=30,
        exit_status="step_limit",
        notes="MaxIterationsReached: Agent reached maximum iterations limit (30).",
    )

    declared, reason = outcome(0, False, at_ceiling, openhands.SUCCESS_STATUS)

    assert declared is False
    assert reason is not None
    assert reason.startswith("harness stopped with status step_limit: MaxIterationsReached")


def test_the_trajectory_carries_the_note(tmp_path: Path) -> None:
    payload = _trajectory(status="error")
    payload["notes"] = "tool timeout after 30s"
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(payload))

    run = openhands.read_run(path)

    assert run is not None
    assert run.notes == "tool timeout after 30s"


def test_a_finished_conversation_declares_success_only_with_a_diff() -> None:
    finished = HarnessRun(tokens_in=1, tokens_out=1, steps=4, exit_status="finished")

    assert outcome(0, False, finished, openhands.SUCCESS_STATUS) == (True, None)
    assert outcome(0, True, finished, openhands.SUCCESS_STATUS) == (False, None)


def test_the_command_names_the_task_the_config_and_the_trajectory() -> None:
    command = " ".join(openhands.COMMAND)

    assert openhands.DRIVER_PATH in command
    assert openhands.CONFIG_FILENAME in command
    assert "TASK.md" in command
    assert openhands.TRAJECTORY_FILENAME in command


def test_a_harness_with_a_trajectory_needs_a_function_to_read_it() -> None:
    spec = HarnessSpec(
        name="x",
        version="1",
        image="img:1",
        command=("x",),
        trajectory_file="traj.json",
    )

    with pytest.raises(HarnessError, match="no function to read it"):
        spec.validate()


def test_the_registry_wires_the_image_the_reader_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openhands_image", "fixjury-openhands:1.11.0")
    monkeypatch.setattr(settings, "openhands_version", "1.11.0")
    monkeypatch.setattr(settings, "openhands_command", "")

    spec = harness_spec("openhands")

    assert spec.image == "fixjury-openhands:1.11.0"
    assert spec.version == "1.11.0"
    assert spec.command == openhands.COMMAND
    assert spec.read_trajectory is openhands.read_run, "tokens come from the harness"
    assert spec.config_text is openhands.config_text
    assert dict(spec.env) == dict(openhands.ENV)
    assert "HOME" in dict(spec.env), "the image's session store lives in the tmpfs"


def test_an_unpinned_image_is_named_as_such_instead_of_pulling_a_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openhands_image", "")
    monkeypatch.setattr(settings, "openhands_version", "")

    spec = harness_spec("openhands")

    assert spec.version == "unpinned"
    with pytest.raises(HarnessError, match="no pinned image"):
        spec.validate()
