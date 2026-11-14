"""A container harness adapter: third-party agents run inside the sandbox (PLAN §11, 4.1/4.2).

SWE-agent and OpenHands are not re-implemented here. The platform starts the
pinned harness inside a container, hands it the task text, and captures the diff
from the workspace afterwards — the same rule as for the baseline: the agent's
own patch file is never trusted, the platform's ``git diff`` is.

Egress is an allowlisting proxy on the gateway of a per-run internal network
(invariant 7): the container has no route out, so the harness can reach the model
endpoint and nothing else — not by unsetting a variable.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fixjury.agents.base import AgentResult, LlmConfig, RunRequest
from fixjury.agents.mini import MiniRun, read_run
from fixjury.sandbox.container import SandboxSpec, open_sandbox
from fixjury.sandbox.diff import UnreadableDiff, capture_diff
from fixjury.sandbox.network import open_run_network
from fixjury.sandbox.proxy import AllowlistProxy
from fixjury.sandbox.tools import TRUNCATION_MARKER, exec_command

LOGGER = logging.getLogger(__name__)
TASK_FILENAME = "TASK.md"


class HarnessError(RuntimeError):
    """The harness image or command is not configured."""


@dataclass(frozen=True)
class HarnessSpec:
    """How to run one third-party agent. Everything here is configuration."""

    name: str
    version: str
    image: str
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    timeout_s: int = 1800
    # A harness that needs its own settings file gets it written into the
    # workspace before the run and removed before the diff is captured. The text
    # comes from a function of the run, so a key never has to enter argv.
    config_file: str = ""
    config_text: Callable[[LlmConfig | None, int], str] | None = None
    # A harness that records its own trajectory can report tokens and whether it
    # submitted; without one every third-party agent claims zero tokens.
    trajectory_file: str = ""
    trajectory_success_status: str = ""

    def validate(self) -> None:
        if not self.image:
            raise HarnessError(
                f"{self.name} has no pinned image; set the image and version before running it"
            )
        if not self.command:
            raise HarnessError(f"{self.name} has no command to run inside its image")
        if self.config_file and self.config_text is None:
            raise HarnessError(f"{self.name} has a config file but no function to write it")


def llm_target(llm: LlmConfig | None) -> str | None:
    """``host:port`` of the model endpoint, the single allowed egress target."""

    if llm is None or not llm.base_url:
        return None
    split = urlsplit(llm.base_url)
    if not split.hostname:
        return None
    port = split.port or (443 if split.scheme == "https" else 80)
    return f"{split.hostname.lower()}:{port}"


def exposes_secret(diff: str, key: str) -> bool:
    """Did the agent put the provider key into the patch we are about to store?

    The key reaches the sandbox because the harness needs it, so a run can copy it
    into the workspace and the diff would carry it into the database, the report
    and the review. Nothing outside the run may see it.
    """

    return bool(key) and key in diff


def proxy_environment(proxy_url: str) -> dict[str, str]:
    """Every egress variable points at the proxy, and none of them is an exception.

    An empty ``NO_PROXY`` is deliberate. Excluding localhost would only make sense
    if the sandbox had something local worth reaching, and it does not; worse, it
    silently bypasses the proxy whenever the model endpoint is self-hosted on the
    host's loopback, which turns a working run into a connection error.
    """

    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "",
        "no_proxy": "",
    }


@dataclass(frozen=True)
class ContainerHarnessAdapter:
    """Runs ``spec.command`` in a sandbox and reports the captured diff."""

    spec: HarnessSpec

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def version(self) -> str:
        return self.spec.version

    async def run(self, request: RunRequest) -> AgentResult:
        self.spec.validate()
        workspace = request.workspace
        # A repository may already track any of these names, as a file, a directory
        # or a symlink. Stash first so the platform neither writes through a symlink
        # nor deletes tracked content, and restore before the diff is captured.
        stashes = {
            name: stash_workspace_entry(workspace, name)
            for name in (TASK_FILENAME, self.spec.config_file, self.spec.trajectory_file)
            if name
        }
        write_workspace_file(workspace, TASK_FILENAME, request.spec)
        write_config = self.spec.config_text
        if self.spec.config_file and write_config is not None:
            write_workspace_file(
                workspace, self.spec.config_file, write_config(request.llm, request.max_steps)
            )

        target = llm_target(request.llm)
        proxy = AllowlistProxy(allowed={target} if target else set())

        started = time.monotonic()
        # The container runs on a network with no route out, so the proxy is not a
        # suggestion in the environment but the only reachable peer: clearing
        # HTTP_PROXY in the sandbox no longer reaches the internet.
        async with open_run_network() as network:
            proxy.host = network.gateway
            await proxy.start()
            environment = proxy_environment(proxy.url)
            try:
                sandbox_spec = SandboxSpec(
                    image=self.spec.image,
                    workspace=workspace,
                    network_name=network.name,
                    timeout_s=self.spec.timeout_s,
                )
                async with open_sandbox(sandbox_spec) as sandbox:
                    result = await exec_command(
                        sandbox,
                        list(self.spec.command),
                        timeout_s=float(self.spec.timeout_s),
                        workdir="/workspace",
                        env={**environment, **dict(self.spec.env)},
                    )
                exit_code = result.exit_code
                output = result.output
            finally:
                await proxy.stop()
        lifetime = Decimal(str(round(time.monotonic() - started, 2)))

        # mini's trajectory is read before cleanup: it carries the token counts and
        # whether the agent submitted. A missing or broken file is not a failed
        # run, only a run whose token columns stay zero.
        trail: MiniRun | None = None
        if self.spec.trajectory_file:
            trail = read_run(workspace / self.spec.trajectory_file)
        elif self.spec.trajectory_success_status:
            LOGGER.warning("%s asks for a success status but has no trajectory", self.spec.name)

        # Files we wrote or the harness wrote for its own bookkeeping are not the
        # agent's change: remove them before the diff is captured, or every harness
        # run would ship the prompt, the key and the log as a patch. Restoring what
        # the repository tracked keeps the platform's deletions out of the patch too.
        for name, stash in stashes.items():
            restore_workspace_entry(workspace, name, stash)

        try:
            captured = capture_diff(workspace)
        except UnreadableDiff as exc:
            return AgentResult(
                diff="",
                diff_sha256="",
                declared_success=False,
                unable_reason=f"UNREADABLE_DIFF: {exc}",
                # The run happened even if the patch cannot be transported: what it
                # consumed stays measured, or rejected attempts look free.
                tokens_in=trail.tokens_in if trail else 0,
                tokens_out=trail.tokens_out if trail else 0,
                steps=trail.steps if trail else 0,
                sandbox_seconds=lifetime,
                summary="the harness produced a diff the platform cannot read",
            )

        key = request.llm.api_key if request.llm else ""
        if exposes_secret(captured.diff, key):
            # The patch is dropped rather than stored: keeping it would publish the
            # key in the database, the report and every review page.
            return AgentResult(
                diff="",
                diff_sha256="",
                declared_success=False,
                unable_reason="KEY_IN_DIFF: the patch carried the provider key and was dropped",
                tokens_in=trail.tokens_in if trail else 0,
                tokens_out=trail.tokens_out if trail else 0,
                steps=trail.steps if trail else 0,
                sandbox_seconds=lifetime,
                summary="the harness wrote the provider key into the patch",
            )

        declared, unable_reason = outcome(
            exit_code, captured.is_empty, trail, self.spec.trajectory_success_status
        )

        return AgentResult(
            diff=captured.diff,
            diff_sha256=captured.sha256,
            declared_success=declared,
            unable_reason=unable_reason,
            tokens_in=trail.tokens_in if trail else 0,
            tokens_out=trail.tokens_out if trail else 0,
            steps=trail.steps if trail else 0,
            sandbox_seconds=lifetime,
            summary=_last_meaningful_line(output),
        )


def _last_meaningful_line(output: str) -> str:
    """The last line a reader can use, skipping the truncation markers.

    A capped output ends with bookkeeping rather than with the failure, and the
    failure is the only reason this string exists.
    """

    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith("…") and stripped != TRUNCATION_MARKER:
            return stripped
    return ""


def stash_workspace_entry(workspace: Path, name: str) -> str:
    """Move whatever occupies a platform path aside, returning its stash name.

    The workspace is a checkout of somebody else's repository, so ``TASK.md``,
    ``fixjury-mini.yaml`` and the trajectory name can already exist — as a file, a
    directory or a symlink. Renaming is the only handling that neither writes
    through a symlink nor deletes tracked content: the entry comes back untouched
    before the diff is captured, so the patch never claims the platform deleted it.

    Returns ``""`` when the path was free.
    """

    target = workspace / name
    if not (target.is_symlink() or target.exists()):
        return ""
    stash = f".fixjury-stash-{uuid4().hex}"
    os.rename(target, workspace / stash)
    return stash


def write_workspace_file(workspace: Path, name: str, text: str) -> None:
    """Create a platform file privately and without following a symlink.

    ``O_EXCL|O_NOFOLLOW`` refuses a path that reappeared as a symlink between the
    stash and this call, and mode ``0600`` keeps the API key off every other
    account on the host.
    """

    target = workspace / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def restore_workspace_entry(workspace: Path, name: str, stash: str) -> None:
    """Remove the platform file and put back what was there before the run."""

    target = workspace / name
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
    if stash:
        os.rename(workspace / stash, target)


def workspace_for_run(prefix: str = "fixjury-harness-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def outcome(
    exit_code: int,
    diff_is_empty: bool,
    trail: MiniRun | None,
    success_status: str,
) -> tuple[bool, str | None]:
    """Did the harness declare success, and if not, what does the run say?

    Exit code zero only means the process ended: a harness that stops at its step
    limit also exits zero. When the harness records its own status, that status is
    the declaration of success, and the reason names it instead of hiding behind
    "exited 0".
    """

    if exit_code != 0:
        return False, f"harness exited {exit_code}"
    declared = not diff_is_empty
    if success_status:
        declared = declared and trail is not None and trail.exit_status == success_status
        if trail is not None and trail.exit_status != success_status:
            return False, f"harness stopped with status {trail.exit_status or 'unknown'}"
    return declared, None


__all__ = [
    "ContainerHarnessAdapter",
    "HarnessError",
    "HarnessSpec",
    "llm_target",
    "outcome",
    "proxy_environment",
    "restore_workspace_entry",
    "stash_workspace_entry",
    "workspace_for_run",
    "write_workspace_file",
]
