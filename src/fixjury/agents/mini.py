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
import os
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from fixjury.agents.base import LlmConfig

LOGGER = logging.getLogger(__name__)

HARNESS = "mini"
CONFIG_FILENAME = "fixjury-mini.yaml"
TRAJECTORY_FILENAME = "fixjury-trajectory.json"
# mini's own defaults; passing -c at all replaces them, so both are named.
DEFAULT_CONFIG = "mini.yaml"
TRAJECTORY_FORMAT = "mini-swe-agent"
# mini writes this once the agent issued its finish command.
SUCCESS_STATUS = "Submitted"
# A run of a handful of model calls writes kilobytes. The cap only has to be well
# above that, and well below what would hurt the worker.
MAX_TRAJECTORY_BYTES = 8 * 1024 * 1024

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
        f"  model_name: {json.dumps(model)}",
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


class MiniRun(BaseModel):
    """What mini's own trajectory says about one run."""

    model_config = ConfigDict(frozen=True)

    tokens_in: int
    tokens_out: int
    steps: int
    exit_status: str


def _read_bounded(path: Path) -> bytes | None:
    """Read a regular file without following a symlink and without trusting its size.

    The trajectory is written by the agent, inside a container, and read here with
    the worker's own privileges. A symlink to ``/dev/zero`` or to a file on the host
    must be refused, not followed, and a file whose reported size lies must not be
    read past the cap. ``O_NONBLOCK`` matters as much as ``O_NOFOLLOW``: opening a
    FIFO read-only waits for a writer, so the size check would never get a turn and
    the agent could park the worker.
    """

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        LOGGER.warning("mini trajectory at %s is unreadable: %s", path, exc)
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            LOGGER.warning("mini trajectory at %s is not a regular file", path)
            return None
        if info.st_size > MAX_TRAJECTORY_BYTES:
            LOGGER.warning(
                "mini trajectory at %s is %d bytes, over the %d cap",
                path,
                info.st_size,
                MAX_TRAJECTORY_BYTES,
            )
            return None
        raw = os.read(fd, MAX_TRAJECTORY_BYTES + 1)
    except OSError as exc:
        LOGGER.warning("mini trajectory at %s is unreadable: %s", path, exc)
        return None
    finally:
        os.close(fd)
    if len(raw) > MAX_TRAJECTORY_BYTES:
        LOGGER.warning("mini trajectory at %s grew past the cap while reading", path)
        return None
    return raw


def read_run(path: Path) -> MiniRun | None:
    """Sum the token counts mini recorded, or ``None`` if the file is unusable.

    A trajectory that cannot be read is not a failed run: the patch is still
    verified as usual. It only means the token columns stay zero for this run, and
    the caller logs that instead of inventing numbers.
    """

    raw = _read_bounded(path)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        LOGGER.warning("mini trajectory at %s is not JSON: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        LOGGER.warning("mini trajectory at %s is not an object", path)
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
        tokens_in += _as_int(usage.get("prompt_tokens"))
        tokens_out += _as_int(usage.get("completion_tokens"))

    raw_info = payload.get("info")
    info: dict[str, object] = raw_info if isinstance(raw_info, dict) else {}
    raw_stats = info.get("model_stats")
    stats: dict[str, object] = raw_stats if isinstance(raw_stats, dict) else {}
    return MiniRun(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps=_as_int(stats.get("api_calls")),
        exit_status=str(info.get("exit_status", "")),
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "COMMAND",
    "CONFIG_FILENAME",
    "ENV",
    "HARNESS",
    "SUCCESS_STATUS",
    "TRAJECTORY_FILENAME",
    "MiniRun",
    "config_text",
    "read_run",
]
