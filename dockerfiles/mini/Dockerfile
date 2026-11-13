# The mini-swe-agent harness image (PLAN §1, invariant 7).
#
# Why we build instead of pulling: the public SWE-agent images are either gated
# (ghcr.io without a login) or internal library images with no CLI entry point, so
# there was nothing to pin. mini-swe-agent ships as a normal PyPI package, so the
# image is one pinned install and the pin is visible in the Dockerfile.
#
# Build it with the tag recorded in FIXJURY_MINI_VERSION, then point
# FIXJURY_MINI_IMAGE at it:
#
#   docker build -t fixjury-mini:2.4.6 dockerfiles/mini
#   FIXJURY_MINI_IMAGE=fixjury-mini:2.4.6
#   FIXJURY_MINI_VERSION=2.4.6
#
# The sandbox runs this image as an unprivileged user with a read-only root
# filesystem, so nothing here may need to write outside /workspace or /tmp.
FROM python:3.12-slim

# git: the workspace is a checkout and agents run git commands; bash and
# ca-certificates are what any sane tool loop needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# The only pinned dependency of this image. Bump the pin and FIXJURY_MINI_VERSION
# together: runs recorded under different versions are not comparable.
RUN pip install --no-cache-dir mini-swe-agent==2.4.6

# mini writes its last-run copy under HOME and reads the first-run interview from
# stdin; the sandbox has no writable $HOME and no terminal, so the harness sets
# MSWEA_CONFIGURED=1 and HOME=/tmp itself.
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

CMD ["sh", "-c", "mini --help"]
