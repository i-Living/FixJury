"""OpenHands as a container harness: what to run, what it writes (PLAN §11, 4.2).

The 1.x line of OpenHands has no headless flag. The ``openhands`` command was the
terminal UI and the GUI launcher; the maintained surfaces are the SDK, the terminal
UI and the web app, and none of them fits a container with no terminal. So the
harness image carries this repository's own entry point, ``openhands_driver.py``,
which builds the same agent the CLI builds and runs it once on ``TASK.md``.

Two files cross the boundary, and both are removed before the diff is captured:

``fixjury-openhands.json``
    The model, the endpoint, the key, the session header and the step ceiling for
    one run. It is a file rather than argv because argv is visible in ``docker exec``
    and in the container's process list.

``fixjury-trajectory.json``
    Written by the driver: prompt and completion tokens, the number of model calls,
    and the conversation's own final status. Tokens come from the SDK's metrics, not
    from the platform's guess (invariant 8).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fixjury.agents.base import LlmConfig
from fixjury.agents.model_names import litellm_model
from fixjury.agents.trajectory import HarnessRun, as_int, read_json_object

LOGGER = logging.getLogger(__name__)

HARNESS = "openhands"
CONFIG_FILENAME = "fixjury-openhands.json"
TRAJECTORY_FILENAME = "fixjury-trajectory.json"
TRAJECTORY_FORMAT = "fixjury-openhands-1"
# The conversation reports this once the agent finished the task. A run that stops
# at the step ceiling ends as ``paused`` instead, which is not a completion.
SUCCESS_STATUS = "finished"

DRIVER_PATH = "/opt/fixjury/openhands_driver.py"
COMMAND: tuple[str, ...] = (
    "python",
    DRIVER_PATH,
    CONFIG_FILENAME,
    "TASK.md",
    TRAJECTORY_FILENAME,
)
# The image sets both, and the sandbox passes no environment of its own: naming them
# here keeps the harness working if the image is rebuilt without them.
ENV: tuple[tuple[str, str], ...] = (
    ("HOME", "/tmp"),  # noqa: S108 - inside the run's container
    ("OPENHANDS_SUPPRESS_BANNER", "1"),
)


def config_text(llm: LlmConfig | None, max_steps: int) -> str:
    """The run's settings file, one JSON object.

    Every value is dumped as JSON, so a model name or a key with a quote in it stays
    a value instead of becoming a syntax error in a file the harness has to parse.
    """

    payload = {
        "model": litellm_model(llm.model if llm else ""),
        "base_url": llm.base_url if llm else "",
        "api_key": llm.api_key if llm else "",
        "session": llm.session if llm else "",
        "max_steps": max_steps,
    }
    return json.dumps(payload, indent=2) + "\n"


def read_run(path: Path) -> HarnessRun | None:
    """What the driver wrote about this run, or ``None`` if the file is unusable.

    An unreadable trajectory is not a failed run: the patch is verified as usual and
    only the token columns stay zero.
    """

    payload = read_json_object(path, "openhands")
    if payload is None:
        return None
    if payload.get("trajectory_format") != TRAJECTORY_FORMAT:
        LOGGER.warning("openhands trajectory at %s is not a fixjury trajectory", path)
        return None
    return HarnessRun(
        tokens_in=as_int(payload.get("tokens_in")),
        tokens_out=as_int(payload.get("tokens_out")),
        steps=as_int(payload.get("steps")),
        exit_status=str(payload.get("exit_status", "")),
        notes=str(payload.get("notes", ""))[:400],
    )


__all__ = [
    "COMMAND",
    "CONFIG_FILENAME",
    "DRIVER_PATH",
    "ENV",
    "HARNESS",
    "SUCCESS_STATUS",
    "TRAJECTORY_FILENAME",
    "TRAJECTORY_FORMAT",
    "config_text",
    "read_run",
]
