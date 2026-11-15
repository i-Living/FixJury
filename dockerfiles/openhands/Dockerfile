# The OpenHands harness image (PLAN §1, invariant 7).
#
# Why a driver instead of a CLI flag: the OpenHands 1.x line has no headless mode.
# The ``openhands`` console script went away with the old CLI; what remains is the
# SDK, a terminal UI and a web app. A container has no terminal, so the image runs
# ``openhands_driver.py`` — this repository's entry point on top of the SDK — and
# the image pins the SDK build it was tested against.
#
# Why the second pin: openhands-ai 1.11.0 resolves agent-client-protocol 1.0.0,
# which no longer has the ``acp.helpers`` module the SDK imports at start-up, so the
# SDK dies on import with a resolver-approved dependency set. Naming the working ACP
# version here keeps the pin visible instead of leaving it to resolution order.
#
# Why no Chromium: the default tool set includes a browser tool, which refuses to
# start without Chromium. The sandbox has no route out, so the driver asks for the
# default tools minus the browser; without that a run dies before it says anything.
#
# Build it with the tag recorded in FIXJURY_OPENHANDS_VERSION, then point
# FIXJURY_OPENHANDS_IMAGE at it:
#
#   docker build -t fixjury-openhands:1.11.0 dockerfiles/openhands
#   FIXJURY_OPENHANDS_IMAGE=fixjury-openhands:1.11.0
#   FIXJURY_OPENHANDS_VERSION=1.11.0
#
# The sandbox runs this image as an unprivileged user with a read-only root
# filesystem, so nothing here may need to write outside /workspace or /tmp.
FROM python:3.12-slim

# git: the workspace is a checkout and agents run git commands. bash and tmux: the
# OpenHands terminal tool prefers tmux and falls back to a subprocess terminal, which
# it warns is less stable — a fallback that changes agent behaviour is not a pin.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git tmux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        openhands-ai==1.11.0 \
        agent-client-protocol==0.10.1

COPY openhands_driver.py /opt/fixjury/openhands_driver.py

# HOME points into the tmpfs: the SDK writes its own session store, and the root
# filesystem is read-only.
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENHANDS_SUPPRESS_BANNER=1 \
    HOME=/tmp

WORKDIR /workspace
CMD ["python", "/opt/fixjury/openhands_driver.py"]
