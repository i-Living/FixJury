"""Verification runner: fresh container, applied diff, checks, held-out (PLAN §11).

Two containers per verification, and the split is the point:

1. a *prepare* container **with** network, which creates a virtualenv inside the
   workspace and installs the project and its test dependencies;
2. a *verify* container **without** network, which applies nothing and installs
   nothing — it only runs the checks and the held-out suite against the tree the
   first container prepared.

Installing dependencies needs the network; running an untrusted patch does not,
and the patch is only executed in the second container. The held-out files are
copied in between the two, after the agent's diff is applied, so a patch cannot
overwrite the tests that judge it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from fixjury.models import RejectionCode
from fixjury.run import RunError, checkout_workspace
from fixjury.sandbox.container import SandboxSpec, open_sandbox
from fixjury.sandbox.tools import ExecResult, exec_command
from fixjury.tasks.heldout import HELDOUT_FILES_DIRNAME, read_heldout
from fixjury.tasks.schema import CheckTool, TaskDocument, parse_check
from fixjury.verify.policy import PolicyResult, Violation, check_policy
from fixjury.verify.verdict import CheckOutcome, VerificationResult, assemble

LOGGER = logging.getLogger(__name__)
# ``git describe`` reads a local checkout; a hung one is a bug, not patience.
GIT_QUERY_TIMEOUT_S = 30.0
VENV = ".venv"
VENV_BIN = f"/workspace/{VENV}/bin"
PATH_WITH_VENV = f"{VENV_BIN}:/usr/local/bin:/usr/bin:/bin"
PIP = f"{VENV_BIN}/pip"
INSTALL_STEPS = (
    ["python", "-m", "venv", VENV],
    [PIP, "install", "--no-input", "--disable-pip-version-check", "-e", ".[dev]"],
    [PIP, "install", "--no-input", "--disable-pip-version-check", "-e", "."],
)
TOOL_VERSION_COMMANDS = (
    ("python", ["python", "--version"]),
    ("pytest", ["pytest", "--version"]),
    ("ruff", ["ruff", "--version"]),
    ("mypy", ["mypy", "--version"]),
)


class VerificationError(RuntimeError):
    """The verification environment could not be built."""


def apply_patch(workspace: Path, diff: str) -> None:
    """Apply the agent's diff to the pristine checkout, or say why not."""

    git = shutil.which("git")
    if git is None:
        raise VerificationError("git is not installed")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [git, "-C", str(workspace), "apply", "--binary", "--whitespace=nowarn", "-"],
        input=diff,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise VerificationError(f"the diff does not apply: {detail}")


def overlay_heldout(workspace: Path, bundle: Path) -> tuple[str, ...]:
    """Copy the post-fix test files over the checkout and return their paths."""

    spec = read_heldout(bundle)
    source_root = bundle / HELDOUT_FILES_DIRNAME
    for relative in spec.files:
        source = source_root / relative
        if not source.is_file():
            raise VerificationError(f"held-out bundle is missing {relative}")
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return spec.files


def check_argv(command: str) -> list[str]:
    """Turn a task check into argv, keeping the whitelist from ``parse_check``."""

    parsed = parse_check(command)
    if parsed.tool is CheckTool.ruff:
        return ["ruff", "check", *parsed.args]
    return [parsed.tool.value, *parsed.args]


def scm_version_env(workspace: Path) -> dict[str, str]:
    """A version for projects that read it from git, which the sandbox does not have.

    The prepare image is ``python:slim``: no git binary, so hatch-vcs and
    setuptools-scm cannot read the tag and the install dies while generating
    metadata. The nearest tag is resolved here, on the checkout the container sees,
    so the version stays the real one instead of a made-up constant.
    """

    git = shutil.which("git")
    if git is None:
        return {}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(workspace), "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_QUERY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # A hung lookup must not hold the verification: the install is what matters.
        LOGGER.warning("git describe timed out in %s", workspace)
        return {}
    tag = completed.stdout.strip()
    if completed.returncode != 0 or not tag:
        return {}
    return {"SETUPTOOLS_SCM_PRETEND_VERSION": tag.removeprefix("v")}


async def _install(spec: SandboxSpec, *, extra_env: dict[str, str] | None = None) -> None:
    env = {"PATH": PATH_WITH_VENV, **(extra_env or {})}
    async with open_sandbox(spec) as sandbox:
        for step in INSTALL_STEPS:
            result = await exec_command(
                sandbox,
                step,
                timeout_s=600.0,
                workdir="/workspace",
                env=env,
            )
            if result.exit_code == 0:
                continue
            if step[-1] == ".[dev]":
                # A project without a dev extra is not a broken project.
                continue
            raise VerificationError(f"{' '.join(step)} failed: {result.output.strip()[-600:]}")


async def _attempt_install(spec: SandboxSpec, workspace: Path) -> str | None:
    """Install the project, returning the failure instead of raising: see ``verify_workspace``."""

    # git is a blocking subprocess: off the loop, or a slow describe stalls every other
    # task in the process and holds up cancellation.
    extra_env = await asyncio.to_thread(scm_version_env, workspace)
    try:
        await _install(spec, extra_env=extra_env)
    except VerificationError as exc:
        return str(exc)
    return None


async def _run_checks(
    spec: SandboxSpec,
    document: TaskDocument,
    workspace: Path,
    heldout_bundle: Path | None,
) -> tuple[tuple[CheckOutcome, ...], bool | None, dict[str, str]]:
    outcomes: list[CheckOutcome] = []
    heldout_pass: bool | None = None
    versions: dict[str, str] = {}
    async with open_sandbox(spec) as sandbox:
        for name, argv in TOOL_VERSION_COMMANDS:
            result = await exec_command(
                sandbox, argv, timeout_s=60.0, workdir="/workspace", env={"PATH": PATH_WITH_VENV}
            )
            # A tool the task never runs may not be installed at all; recording
            # its error text as a version would poison tool_versions.
            if result.exit_code == 0 and result.output.strip():
                versions[name] = result.output.strip().splitlines()[0]
        for command in document.checks:
            result = await exec_command(
                sandbox,
                check_argv(command),
                timeout_s=900.0,
                workdir="/workspace",
                env={"PATH": PATH_WITH_VENV},
            )
            outcomes.append(
                CheckOutcome(
                    command=command,
                    tool=parse_check(command).tool,
                    exit_code=result.exit_code,
                    output=_tail(result),
                )
            )
        if heldout_bundle is not None:
            # The overlay lands after the task's own checks have run, so
            # ``tests_pass`` measures the agent's tree and ``heldout_pass``
            # measures the post-fix tests. Copying first would make the two the
            # same measurement and hide a patch that broke the repository's
            # tests while satisfying the held-out ones.
            spec_heldout = read_heldout(heldout_bundle)
            heldout_files = overlay_heldout(workspace, heldout_bundle)
            result = await exec_command(
                sandbox,
                ["pytest", *heldout_files],
                timeout_s=900.0,
                workdir="/workspace",
                env={"PATH": PATH_WITH_VENV},
            )
            heldout_pass = result.exit_code == 0
            outcomes.append(
                CheckOutcome(
                    command=f"held-out: {spec_heldout.command}",
                    tool=CheckTool.pytest,
                    exit_code=result.exit_code,
                    output=_tail(result),
                    heldout=True,
                )
            )
    return tuple(outcomes), heldout_pass, versions


def _tail(result: ExecResult, limit: int = 2000) -> str:
    text = result.output.strip()
    return text[-limit:] if len(text) > limit else text


async def verify_workspace(
    document: TaskDocument,
    diff: str,
    *,
    workspace: Path,
    image: str,
    heldout_bundle: Path | None = None,
) -> VerificationResult:
    """Verify one diff against one task. Raises ``VerificationError`` when the
    environment cannot be built; a bad patch is a result, not an error."""

    base_commit = document.base_commit
    if not base_commit:
        raise VerificationError(f"task {document.id} has no base_commit")

    policy: PolicyResult = check_policy(
        diff,
        allowed_paths=document.allowed_paths,
        forbidden=document.forbidden,
    )

    await checkout_workspace(document.repo, base_commit, workspace)
    try:
        apply_patch(workspace, diff)
    except VerificationError as exc:
        return assemble(
            (),
            policy=policy,
            heldout_pass=None,
            tool_versions={},
            extra_violations=(Violation(RejectionCode.BUILD_BREAK, str(exc)),),
            note="the diff does not apply to the base commit",
        )

    prepared = SandboxSpec(image=image, workspace=workspace, network=True, timeout_s=1800)
    patched_failure = await _attempt_install(prepared, workspace)
    # A single failed install is not evidence: a resolver can time out on the network.
    # Only an install that fails twice is worth blaming on the diff.
    if patched_failure is not None:
        patched_failure = await _attempt_install(prepared, workspace)
    if patched_failure is not None:
        # Whose failure it is decides how the pair is counted, and the exit status does
        # not say. The same install on the base commit does: if it fails there too, no
        # diff could have been verified and the pair is an exclusion; if it succeeds,
        # the diff broke a build that worked, which is a failed fix.
        #
        # The base gets its own checkout rather than a revert: the install runs a build
        # backend from the diff, with the workspace mounted writable, so ``.git`` is
        # untrusted by then. Running host git against it — even ``reset --hard`` — hands
        # that backend hooks, filters and config that execute on the host.
        base_workspace = workspace.with_name(f"{workspace.name}-base")
        try:
            await checkout_workspace(document.repo, base_commit, base_workspace)
        except RunError as exc:
            raise VerificationError(f"cannot check out the base commit to compare: {exc}") from exc
        try:
            base_spec = SandboxSpec(
                image=image, workspace=base_workspace, network=True, timeout_s=1800
            )
            base_failure = await _attempt_install(base_spec, base_workspace)
        finally:
            shutil.rmtree(base_workspace, ignore_errors=True)
        if base_failure is not None:
            raise VerificationError(
                f"install fails on the base commit too ({base_failure}); "
                f"with the diff applied: {patched_failure}"
            )
        return assemble(
            (),
            policy=policy,
            heldout_pass=None,
            tool_versions={},
            extra_violations=(Violation(RejectionCode.BUILD_BREAK, patched_failure),),
            note="the diff breaks an install that works on the base commit",
        )

    running = SandboxSpec(image=image, workspace=workspace, network=False, timeout_s=1800)
    outcomes, heldout_pass, versions = await _run_checks(
        running, document, workspace, heldout_bundle
    )
    return assemble(
        outcomes,
        policy=policy,
        heldout_pass=heldout_pass,
        tool_versions=versions,
    )


__all__ = [
    "VerificationError",
    "apply_patch",
    "check_argv",
    "overlay_heldout",
    "verify_workspace",
]
