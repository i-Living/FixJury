"""A container harness adapter: third-party agents run inside the sandbox (PLAN §11, 4.1/4.2).

SWE-agent and OpenHands are not re-implemented here. The platform starts the
pinned harness inside a container, hands it the task text, and captures the diff
from the workspace afterwards — the same rule as for the baseline: the agent's
own patch file is never trusted, the platform's ``git diff`` is.

Egress is an allowlisting proxy on the docker bridge address (invariant 7): the
harness can reach the model endpoint and nothing else.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fixjury.agents.base import AgentResult, LlmConfig, RunRequest
from fixjury.sandbox.container import SandboxSpec, open_sandbox
from fixjury.sandbox.diff import UnreadableDiff, capture_diff
from fixjury.sandbox.proxy import AllowlistProxy, docker_gateway
from fixjury.sandbox.tools import exec_command

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

    def validate(self) -> None:
        if not self.image:
            raise HarnessError(
                f"{self.name} has no pinned image; set the image and version before running it"
            )
        if not self.command:
            raise HarnessError(f"{self.name} has no command to run inside its image")


def llm_target(llm: LlmConfig | None) -> str | None:
    """``host:port`` of the model endpoint, the single allowed egress target."""

    if llm is None or not llm.base_url:
        return None
    split = urlsplit(llm.base_url)
    if not split.hostname:
        return None
    port = split.port or (443 if split.scheme == "https" else 80)
    return f"{split.hostname.lower()}:{port}"


def proxy_environment(proxy_url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
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
        (workspace / TASK_FILENAME).write_text(request.spec, encoding="utf-8")

        target = llm_target(request.llm)
        proxy = AllowlistProxy(allowed={target} if target else set())
        gateway = await docker_gateway()
        proxy.host = gateway
        await proxy.start()
        environment = proxy_environment(proxy.url)

        started = time.monotonic()
        try:
            sandbox_spec = SandboxSpec(
                image=self.spec.image,
                workspace=workspace,
                network=True,
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

        # The task file is ours, not the agent's change: remove it before the
        # diff is captured, or every harness run would ship the prompt as a patch.
        (workspace / TASK_FILENAME).unlink(missing_ok=True)

        try:
            captured = capture_diff(workspace)
        except UnreadableDiff as exc:
            return AgentResult(
                diff="",
                diff_sha256="",
                declared_success=False,
                unable_reason=f"UNREADABLE_DIFF: {exc}",
                tokens_in=0,
                tokens_out=0,
                steps=0,
                sandbox_seconds=lifetime,
                summary="the harness produced a diff the platform cannot read",
            )

        return AgentResult(
            diff=captured.diff,
            diff_sha256=captured.sha256,
            declared_success=exit_code == 0 and not captured.is_empty,
            unable_reason=None if exit_code == 0 else f"harness exited {exit_code}",
            tokens_in=0,
            tokens_out=0,
            steps=0,
            sandbox_seconds=lifetime,
            summary=output.strip().splitlines()[-1] if output.strip() else "",
        )


def workspace_for_run(prefix: str = "fixjury-harness-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


__all__ = [
    "ContainerHarnessAdapter",
    "HarnessError",
    "HarnessSpec",
    "llm_target",
    "proxy_environment",
    "workspace_for_run",
]
