"""mini-swe-agent as the container harness: config, trajectory, outcome."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from fixjury.agents import mini
from fixjury.agents.base import LlmConfig
from fixjury.agents.container_harness import HarnessError, HarnessSpec, outcome
from fixjury.agents.mini import MiniRun, config_text, read_run

LLM = LlmConfig(
    base_url="https://opencode.ai/zen/go/v1",
    model="opencode-go/deepseek-v4.1-flash",
    api_key="secret-key",
    session="session-1",
)


def _trajectory(
    *,
    status: str = "Submitted",
    usages: list[dict[str, int]] | None = None,
    api_calls: int = 2,
) -> dict[str, object]:
    messages: list[dict[str, object]] = [{"role": "system", "content": "you are an agent"}]
    for usage in (
        usages
        if usages is not None
        else [
            {"prompt_tokens": 100, "completion_tokens": 10},
            {"prompt_tokens": 200, "completion_tokens": 20},
        ]
    ):
        messages.append(
            {"role": "assistant", "content": "step", "extra": {"response": {"usage": usage}}}
        )
    return {
        "trajectory_format": "mini-swe-agent-1.1",
        "info": {
            "exit_status": status,
            "model_stats": {"api_calls": api_calls, "instance_cost": 0.02},
        },
        "messages": messages,
    }


def test_the_config_is_yaml_and_carries_the_endpoint_without_argv() -> None:
    text = config_text(LLM, max_steps=12)
    parsed = yaml.safe_load(text)

    assert parsed["model"]["model_name"] == LLM.model
    assert parsed["model"]["model_kwargs"]["api_base"] == LLM.base_url
    assert parsed["model"]["model_kwargs"]["api_key"] == LLM.api_key
    assert parsed["agent"]["step_limit"] == 12
    # No prompt, and no key in the command line: argv is visible in docker.
    assert "secret-key" not in " ".join(mini.COMMAND)


def test_a_bare_model_name_is_named_for_litellm() -> None:
    """litellm refuses a name without a provider; the endpoint speaks OpenAI."""

    bare = LlmConfig(base_url=LLM.base_url, model="deepseek-v4.1-flash")
    parsed = yaml.safe_load(config_text(bare, max_steps=1))

    assert parsed["model"]["model_name"] == "openai/deepseek-v4.1-flash"


def test_the_session_header_is_written_only_when_a_session_is_configured() -> None:
    with_session = yaml.safe_load(config_text(LLM, max_steps=5))
    without_llm = LlmConfig(base_url=LLM.base_url, model=LLM.model)
    without = yaml.safe_load(config_text(without_llm, max_steps=5))

    headers = with_session["model"]["model_kwargs"]["extra_headers"]
    assert headers["x-opencode-session"] == "session-1"
    assert "extra_headers" not in without["model"]["model_kwargs"]


def test_a_run_without_an_llm_still_produces_a_config() -> None:
    parsed = yaml.safe_load(config_text(None, max_steps=3))

    assert parsed["model"]["model_name"] == ""
    assert parsed["agent"]["mode"] == "yolo"
    assert parsed["agent"]["confirm_exit"] is False


def test_the_trajectory_gives_tokens_and_steps(tmp_path: Path) -> None:
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(_trajectory()))

    run = read_run(path)

    assert run == MiniRun(tokens_in=300, tokens_out=30, steps=2, exit_status="Submitted")


def test_a_missing_trajectory_is_not_a_crash(tmp_path: Path) -> None:
    assert read_run(tmp_path / "nope.json") is None


def test_a_trajectory_of_another_tool_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "traj.json"
    path.write_text(json.dumps({"trajectory_format": "something-else", "messages": []}))

    assert read_run(path) is None


def test_broken_usage_degrades_to_zero_instead_of_guessing(tmp_path: Path) -> None:
    payload = _trajectory(usages=[])
    payload["messages"] = [
        {"role": "assistant", "extra": {"response": {"usage": {"prompt_tokens": 5}}}},
        {"role": "assistant", "extra": {"response": {}}},
        {"role": "assistant", "extra": "not-a-dict"},
        "not-a-message",
    ]
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(payload))

    run = read_run(path)

    assert run is not None
    assert run.tokens_in == 5
    assert run.tokens_out == 0


def test_a_harness_with_a_config_file_needs_a_function_to_write_it() -> None:
    spec = HarnessSpec(name="x", version="1", image="img:1", command=("x",), config_file="c.yaml")

    with pytest.raises(HarnessError, match="no function to write it"):
        spec.validate()


def test_exit_zero_is_not_a_success_when_the_harness_stopped_at_its_limit() -> None:
    declared, reason = outcome(
        0,
        False,
        MiniRun(tokens_in=1, tokens_out=1, steps=30, exit_status="LimitsExceeded"),
        "Submitted",
    )

    assert declared is False
    assert reason == "harness stopped with status LimitsExceeded"


def test_a_submission_declares_success_only_with_a_diff() -> None:
    submitted = MiniRun(tokens_in=1, tokens_out=1, steps=4, exit_status="Submitted")

    assert outcome(0, False, submitted, "Submitted") == (True, None)
    assert outcome(0, True, submitted, "Submitted") == (False, None)


def test_without_a_trajectory_the_declaration_falls_back_to_the_exit_code() -> None:
    assert outcome(0, False, None, "") == (True, None)
    assert outcome(0, False, None, "Submitted") == (False, None)
    assert outcome(3, False, None, "") == (False, "harness exited 3")


def test_the_command_loads_minis_packaged_config_not_one_from_the_repository() -> None:
    """A bare `-c mini.yaml` resolves against the workspace first, so it cannot be used."""

    script = " ".join(mini.COMMAND)

    assert "locate_file" in script, "the packaged path is resolved, not named"
    assert "-c $PKG/mini.yaml" in script or '"$PKG/mini.yaml"' in script
    assert "-c mini.yaml" not in script


def test_a_trajectory_symlink_is_refused(tmp_path: Path) -> None:
    """The agent writes the trajectory: a symlink to a device must not be followed."""

    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_trajectory()))
    link = tmp_path / "traj.json"
    link.symlink_to(outside)

    assert read_run(link) is None
    assert outside.exists(), "reading must not have touched the target"


def test_a_trajectory_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "traj.json"
    directory.mkdir()

    assert read_run(directory) is None


def test_a_fifo_is_refused_without_waiting_for_a_writer(tmp_path: Path) -> None:
    """A FIFO would block the open, so the size check would never run and the worker would park."""

    path = tmp_path / "traj.json"
    os.mkfifo(path)

    assert read_run(path) is None


def test_a_trajectory_over_the_cap_is_refused_without_reading_it(tmp_path: Path) -> None:
    path = tmp_path / "traj.json"
    path.write_text("{}")
    with path.open("r+b") as handle:
        handle.truncate(mini.MAX_TRAJECTORY_BYTES + 1)

    assert read_run(path) is None


def test_messages_that_are_not_a_list_cost_the_tokens_but_not_the_status(tmp_path: Path) -> None:
    """One broken field must not throw away the exit status the run needs."""

    payload = _trajectory()
    payload["messages"] = 1
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(payload))

    run = read_run(path)

    assert run == MiniRun(tokens_in=0, tokens_out=0, steps=2, exit_status="Submitted")
