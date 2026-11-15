"""mini-swe-agent as a container harness: config, command and trajectory (PLAN §1).

mini-swe-agent is the third-party agent in the MVP. It is a bash-only loop, so it
runs inside our sandbox without docker-in-docker and without a bespoke image: the
image only has to provide ``mini`` from a pinned release.

Two files cross the boundary, and both are deleted before the diff is captured so
neither ends up in the patch:

``fixjury-mini.yaml``
    The overrides merged over mini's own ``mini.yaml``: endpoint, model, tool
    limits. The API key lives here rather than in argv because argv is visible in
    ``docker exec`` and in the process list of the container; the file is written
    into the run's workspace and removed afterwards.

``fixjury-trajectory.json``
    mini's own record of the run. It is the only honest source of token counts for
    a container harness — without it every third-party agent would report zero
    tokens, and ``tokens_per_accepted_fix`` (invariant 8) would be a constant.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fixjury.agents.base import LlmConfig
from fixjury.agents.model_names import litellm_model
from fixjury.agents.trajectory import (
    MAX_TRAJECTORY_BYTES,
    HarnessRun,
    as_int,
    read_json_object,
)

LOGGER = logging.getLogger(__name__)

HARNESS = "mini"
CONFIG_FILENAME = "fixjury-mini.yaml"
TRAJECTORY_FILENAME = "fixjury-trajectory.json"
# mini's own defaults; passing -c at all replaces them, so both are named.
DEFAULT_CONFIG = "mini.yaml"
TRAJECTORY_FORMAT = "mini-swe-agent"
# mini writes this once the agent issued its finish command.
SUCCESS_STATUS = "Submitted"

# mini resolves a bare config name against the working directory before its own
# package directory, so `-c mini.yaml` inside a target repository would load a file
# the repository committed. The packaged path is asked for by name at run time: the
# image pins the version, but the install prefix is not ours to hardcode.
_PACKAGED_CONFIG = (
    "python3 -c 'import importlib.metadata as m;"
    ' print(m.distribution("mini-swe-agent").locate_file("minisweagent/config"))\''
)
COMMAND: tuple[str, ...] = (
    "sh",
    "-c",
    # One level of quoting only: the substitution prints the directory, and the
    # filename is appended inside the script.
    f'PKG="$({_PACKAGED_CONFIG})" && mini -c "$PKG/mini.yaml"'
    f' -c {CONFIG_FILENAME} -t "$(cat TASK.md)" -o {TRAJECTORY_FILENAME}',
)
# MSWEA_CONFIGURED silences the first-run interview (it reads from stdin, which a
# sandbox does not have); HOME keeps mini's own last-run copy inside the tmpfs.
ENV: tuple[tuple[str, str], ...] = (
    ("MSWEA_CONFIGURED", "1"),
    ("HOME", "/tmp"),  # noqa: S108 - inside the run's container
    ("MSWEA_COST_TRACKING", "ignore_errors"),
)


def config_text(llm: LlmConfig | None, max_steps: int) -> str:
    """The overrides file for one run.

    mini's keys are nested, and a hand-built YAML block keeps the file readable in
    a bug report. Every value is quoted: a model name or a URL is input from
    settings, not something this function may assume is a safe YAML scalar.
    """

    endpoint = llm.base_url if llm else ""
    model = llm.model if llm else ""
    api_key = llm.api_key if llm else ""
    session = llm.session if llm else ""

    lines = [
        "# Written by FixJury for one run; removed before the diff is captured.",
        "model:",
        f"  model_name: {json.dumps(litellm_model(model))}",
        "  model_kwargs:",
        f"    api_base: {json.dumps(endpoint)}",
        f"    api_key: {json.dumps(api_key)}",
    ]
    if session:
        # The Zen Go endpoint rejects requests without this header (AGENTS.md).
        # It belongs under model_kwargs, which is where litellm receives it.
        lines += [
            "    extra_headers:",
            "      x-opencode-session: " + json.dumps(session),
        ]
    lines += [
        "  cost_tracking: ignore_errors",
        "agent:",
        # yolo: no confirmation prompts, there is no terminal to answer them.
        "  mode: yolo",
        "  confirm_exit: false",
        f"  step_limit: {max_steps}",
    ]
    return "\n".join(lines) + "\n"


# The trajectory model is shared with the other container harnesses: what mini
# records and what the OpenHands driver records answer the same four questions.
MiniRun = HarnessRun


def read_run(path: Path) -> MiniRun | None:
    """Sum the token counts mini recorded, or ``None`` if the file is unusable.

    A trajectory that cannot be read is not a failed run: the patch is still
    verified as usual. It only means the token columns stay zero for this run, and
    the caller logs that instead of inventing numbers.
    """

    payload = read_json_object(path, "mini")
    if payload is None:
        return None
    if not str(payload.get("trajectory_format", "")).startswith(TRAJECTORY_FORMAT):
        LOGGER.warning("mini trajectory at %s is not a mini trajectory", path)
        return None
    raw_messages = payload.get("messages")
    if isinstance(raw_messages, list):
        messages: list[object] = raw_messages
    else:
        # No usable per-call records, so no token counts. The exit status is written
        # separately and still decides whether the agent declared success, so the run
        # keeps its status instead of losing both signals to one broken field.
        LOGGER.warning("mini trajectory at %s has no message list", path)
        messages = []

    tokens_in = 0
    tokens_out = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        extra = message.get("extra")
        if not isinstance(extra, dict):
            continue
        response = extra.get("response")
        if not isinstance(response, dict):
            continue
        usage = response.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens_in += as_int(usage.get("prompt_tokens"))
        tokens_out += as_int(usage.get("completion_tokens"))

    raw_info = payload.get("info")
    info: dict[str, object] = raw_info if isinstance(raw_info, dict) else {}
    raw_stats = info.get("model_stats")
    stats: dict[str, object] = raw_stats if isinstance(raw_stats, dict) else {}
    return MiniRun(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps=as_int(stats.get("api_calls")),
        exit_status=str(info.get("exit_status", "")),
    )


__all__ = [
    "COMMAND",
    "CONFIG_FILENAME",
    "ENV",
    "HARNESS",
    "MAX_TRAJECTORY_BYTES",
    "SUCCESS_STATUS",
    "TRAJECTORY_FILENAME",
    "MiniRun",
    "config_text",
    "litellm_model",
    "read_run",
]
