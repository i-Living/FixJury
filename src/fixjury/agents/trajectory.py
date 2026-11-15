"""What a container harness's own trajectory says about one run (PLAN §11, 4.1).

Every third-party harness writes its own record: mini-swe-agent dumps a JSON
document, the OpenHands driver writes the file this repository defines. The record
is the only honest source of token counts and of the agent's own declaration of
success — without it ``tokens_per_accepted_fix`` (invariant 8) would be a constant
zero, and a truncated run would look like a completion.

The file is written by an agent inside a container and read here with the worker's
privileges, so it is treated as untrusted input: no symlinks, no FIFOs, no sizes
that lie.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict

LOGGER = logging.getLogger(__name__)

# A run of a handful of model calls writes kilobytes. The cap only has to be well
# above that, and well below what would hurt the worker.
MAX_TRAJECTORY_BYTES = 8 * 1024 * 1024


class HarnessRun(BaseModel):
    """One run as its own harness recorded it."""

    model_config = ConfigDict(frozen=True)

    tokens_in: int
    tokens_out: int
    steps: int
    exit_status: str
    # Why a run that did not finish stopped. Empty is the normal case: a harness
    # that finished has nothing to explain.
    notes: str = ""


def read_bounded(path: Path, what: str) -> bytes | None:
    """Read a regular file without following a symlink and without trusting its size.

    ``O_NONBLOCK`` matters as much as ``O_NOFOLLOW``: opening a FIFO read-only waits
    for a writer, so the size check would never get a turn and the agent could park
    the worker.
    """

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        LOGGER.warning("%s trajectory at %s is unreadable: %s", what, path, exc)
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            LOGGER.warning("%s trajectory at %s is not a regular file", what, path)
            return None
        if info.st_size > MAX_TRAJECTORY_BYTES:
            LOGGER.warning(
                "%s trajectory at %s is %d bytes, over the %d cap",
                what,
                path,
                info.st_size,
                MAX_TRAJECTORY_BYTES,
            )
            return None
        raw = os.read(fd, MAX_TRAJECTORY_BYTES + 1)
    except OSError as exc:
        LOGGER.warning("%s trajectory at %s is unreadable: %s", what, path, exc)
        return None
    finally:
        os.close(fd)
    if len(raw) > MAX_TRAJECTORY_BYTES:
        LOGGER.warning("%s trajectory at %s grew past the cap while reading", what, path)
        return None
    return raw


def read_json_object(path: Path, what: str) -> dict[str, object] | None:
    """The trajectory as a JSON object, or ``None`` if it cannot be read as one.

    A trajectory that cannot be read is not a failed run: the patch is still
    verified as usual. It only means the token columns stay zero for this run, and
    the caller logs that instead of inventing numbers.
    """

    raw = read_bounded(path, what)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        LOGGER.warning("%s trajectory at %s is not JSON: %s", what, path, exc)
        return None
    if not isinstance(payload, dict):
        LOGGER.warning("%s trajectory at %s is not an object", what, path)
        return None
    return payload


def as_int(value: object) -> int:
    """A token count or a step count, or zero when the harness wrote something else."""

    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "MAX_TRAJECTORY_BYTES",
    "HarnessRun",
    "as_int",
    "read_bounded",
    "read_json_object",
]
