# Phase 4.1–4.2 — third-party harness adapters and egress

Plan for `docs/PLAN.md` §11 phase 4, second half. The rule that shapes this change: the platform never
re-implements an agent and never trusts the agent's own patch file. SWE-agent and OpenHands run inside
the sandbox; the diff is captured by the platform afterwards.

## Scope

| Step | Deliverable | Proof |
| --- | --- | --- |
| 4.1/4.2 | `src/fixjury/agents/container_harness.py` — pinned image + command, task text handed over, diff captured by the platform | container test: a scripted harness patches a file, the platform captures the diff and drops its own task file |
| invariant 7 | `src/fixjury/sandbox/proxy.py` — allowlisting forward proxy on the docker bridge address | a real proxy test: the allowed target answers, another target gets 403 |
| 4.1/4.2 | `LlmConfig` on `RunRequest`; adapters read no global settings | the baseline and the harness both get the endpoint in the request |
| 4.1/4.2 | `swe-agent` and `openhands` in the registry, driven by settings | an unconfigured harness refuses with a clear message instead of pulling a tag nobody chose |

## What is deliberately not invented

The image tag, the command line and the version of SWE-agent and OpenHands are **configuration**:
`FIXJURY_SWE_AGENT_IMAGE`, `FIXJURY_SWE_AGENT_COMMAND`, `FIXJURY_SWE_AGENT_VERSION` and the
OpenHands equivalents. With no image set, the adapter refuses to run and says so. Guessing a tag or a
CLI would produce runs that look real and are not, which is the one failure this project exists to
prevent.

Consequence, stated plainly: no third-party harness has been executed end to end yet. What is proven
here is the contract — a pinned command in a container, the task text delivered, the diff captured by
the platform, egress restricted to one endpoint — and it is proven with a scripted harness that
behaves like one.

## Known limitation

The proxy is enforced for clients that honour `HTTP(S)_PROXY`. The harness container still sits on the
default bridge network, so a harness that ignores proxy variables could reach the network directly.
Closing that needs a per-run `internal` docker network plus a proxy sidecar on it; that is the next
increment, and until then the restriction is documented rather than claimed as airtight.

## Verification checkpoint

- `uv run pytest -q` green; `uv run mypy src tests` clean.
- Proxy: allowed target answers through the proxy; another target gets `403`.
- Harness: a scripted container patches a file and the platform captures the diff; the task file the
  platform wrote never appears in the patch; a failing harness still returns its patch with
  `declared_success = false` and the exit code.
- `fixjury run --agent swe-agent` without an image fails with "no pinned image".

## Registry findings (measured)

What the public registries actually give you, so the next session does not repeat the search:

- `ghcr.io/swe-agent/swe-agent` — **denied**. The manifest request is refused, so the documented
  `docker pull` cannot work without a login.
- `docker.io/sweagent/swe-agent-run:latest` — pulls (2.78 GB, digest
  `sha256:e1581853fc8fa3500cf72b17ec28c69386c9206da01055f74150e36b3e7d0cd4`), but it is a **library**
  image: `sweagent` 0.7.0, `Cmd=[python3]`, no `[project.scripts]` in its `pyproject.toml`, no
  `sweagent` executable in `PATH`, and `python3 -m sweagent` fails because there is no `__main__`.
  Its own Docker Hub description says "Internal (!) image used by swe-agent". Pinning it would pin a
  thing that cannot be driven as a CLI.
- `ghcr.io/all-hands-ai/openhands:latest` — manifest is public and pinnable, so OpenHands is the
  reachable half of 4.1/4.2.

So the remaining route for SWE-agent is to build our own image from a pinned source tag
(`git clone --depth 1 --branch <tag>` + `pip install -e .`) inside `docker build`, which makes the
harness version a build argument instead of a registry tag. That is a decision about adding a build
step to the repo, so it is not taken unasked.

**Settled later: the third-party harness is mini-swe-agent, not SWE-agent.** mini-swe-agent is the
same team's deliberately minimal agent, ships as a pinned PyPI package and a single bash loop, so it
needs no docker-in-docker and no bespoke build: `dockerfiles/mini/Dockerfile` is one pinned install.
SWE-agent stays out of the MVP for the reason above — its runner drives docker itself, and the public
images are either gated or library-only. Its trajectory is also what lets a container harness report
tokens at all, without which `tokens_per_accepted_fix` would be zero for every third-party agent.
