#!/usr/bin/env python3
"""Corpus sanity runner: does a task behave the way PLAN §5 says it must?

For every task it builds a throwaway environment and checks four things:

1. the task's own checks pass on ``base_commit`` (a no-op baseline is not flaky);
2. the held-out suite fails on ``base_commit`` (it really covers the defect);
3. the checks pass with the reference fix applied;
4. the held-out suite passes with the reference fix applied.

This is host-side corpus QA, not the verification runner: verdicts are produced
later, in fresh containers, by the phase 3 runner. Run it with the project
environment so ``fixjury`` is importable:

    uv run python scripts/corpus_sanity.py --corpus benchmarks --jobs 4
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from fixjury.tasks.heldout import heldout_files, read_heldout
from fixjury.tasks.loader import discover_task_paths, heldout_path, read_task
from fixjury.tasks.sanity import SANITY_FILENAME, SanityResult, StageResult, write_sanity
from fixjury.tasks.schema import CheckTool, TaskDocument, parse_check

PYTHON_VERSION = "3.12"
INSTALL_TIMEOUT_S = 900
DEFAULT_COMMAND_TIMEOUT_S = 900

_SUMMARY = re.compile(r"\b(\d+ (passed|failed|error|errors|skipped)|no tests ran)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Runner:
    workdir: Path
    command_timeout_s: int
    write_evidence: bool


def _run(
    args: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None = None
) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 127, f"cannot run {args[0]}: {exc}"
    return completed.returncode, completed.stdout + completed.stderr


def summarize(output: str) -> str:
    """Last meaningful line of a test run, cut to a sane length."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if _SUMMARY.search(line):
            return line[:240]
    return lines[-1][:240] if lines else ""


def _tool(name: str) -> str:
    resolved = shutil.which(name)
    return resolved if resolved is not None else name


def bare_clone(repo: str, cache: Path) -> Path:
    slug = repo.rstrip("/").removeprefix("https://github.com/").replace("/", "__")
    bare = cache / f"{slug}.git"
    if (bare / "HEAD").exists():
        return bare
    code, output = _run(
        [_tool("git"), "clone", "--bare", repo, str(bare)],
        cwd=cache,
        timeout=INSTALL_TIMEOUT_S,
    )
    if code != 0:
        raise RuntimeError(f"git clone {repo} failed: {summarize(output)}")
    return bare


def checkout(bare: Path, commit: str, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run(
        [_tool("git"), "clone", "--no-checkout", "--shared", str(bare), str(destination)],
        cwd=destination.parent,
        timeout=INSTALL_TIMEOUT_S,
    )
    if code != 0:
        raise RuntimeError(f"clone of {bare.name} failed: {summarize(output)}")
    code, output = _run(
        [_tool("git"), "checkout", "--force", commit],
        cwd=destination,
        timeout=INSTALL_TIMEOUT_S,
    )
    if code != 0:
        raise RuntimeError(f"checkout {commit} failed: {summarize(output)}")


def _test_dependency(project: Path) -> tuple[str, str] | None:
    """Find how the project declares its test dependencies."""

    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for kind, section in (
        ("group", data.get("dependency-groups")),
        ("extra", data.get("project", {}).get("optional-dependencies")),
    ):
        if not isinstance(section, dict):
            continue
        for name in ("test", "tests", "testing", "dev"):
            if name in section:
                return (kind, name)
    return None


def create_environment(project: Path, venv: Path, tools: set[CheckTool]) -> tuple[Path, str]:
    """Create a venv for one task and install the project with its tests."""

    code, output = _run(
        [_tool("uv"), "venv", "--clear", "--python", PYTHON_VERSION, str(venv)],
        cwd=project,
        timeout=INSTALL_TIMEOUT_S,
    )
    if code != 0:
        raise RuntimeError(f"uv venv failed: {summarize(output)}")
    python = venv / "bin" / "python"

    installs: list[list[str]] = []
    declared = _test_dependency(project)
    if declared is not None:
        kind, name = declared
        suffix = ["--group", name] if kind == "group" else [f".[{name}]"]
        installs.append([_tool("uv"), "pip", "install", "--python", str(python), "-e", *suffix])
    installs.append([_tool("uv"), "pip", "install", "--python", str(python), "-e", "."])
    installs.append([_tool("uv"), "pip", "install", "--python", str(python), "pytest"])

    installed = ""
    for args in installs:
        code, output = _run(args, cwd=project, timeout=INSTALL_TIMEOUT_S)
        installed = output
        if code == 0:
            break
    else:
        raise RuntimeError(f"dependency install failed: {summarize(installed)}")

    code, output = _run([str(python), "-m", "pytest", "--version"], cwd=project, timeout=120)
    if code != 0:
        code, output = _run(
            [_tool("uv"), "pip", "install", "--python", str(python), "pytest"],
            cwd=project,
            timeout=INSTALL_TIMEOUT_S,
        )
        if code != 0:
            raise RuntimeError(f"pytest install failed: {summarize(output)}")

    for tool in sorted(tools, key=lambda item: item.value):
        if tool is CheckTool.pytest:
            continue
        code, output = _run(
            [_tool("uv"), "pip", "install", "--python", str(python), tool.value],
            cwd=project,
            timeout=INSTALL_TIMEOUT_S,
        )
        if code != 0:
            raise RuntimeError(f"install of {tool.value} failed: {summarize(output)}")

    code, output = _run([str(python), "--version"], cwd=project, timeout=60)
    if code != 0:
        raise RuntimeError(f"venv python does not run: {summarize(output)}")
    return python, output.strip() or f"Python {PYTHON_VERSION}"


def _command(argv: list[str], *, python: Path, repo: Path, timeout: int) -> tuple[int, str]:
    if argv[0] == CheckTool.pytest.value:
        return _run([str(python), "-m", "pytest", "-q", *argv[1:]], cwd=repo, timeout=timeout)
    binary = python.parent / argv[0]
    return _run([str(binary), *argv[1:]], cwd=repo, timeout=timeout)


def _tool_versions(python: Path, repo: Path, checks: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    code, output = _run([str(python), "-m", "pytest", "--version"], cwd=repo, timeout=120)
    if code == 0:
        versions["pytest"] = output.strip().splitlines()[0]
    for name in ("ruff", "mypy"):
        if any(parse_check(check).tool.value == name for check in checks):
            code, output = _run([str(python.parent / name), "--version"], cwd=repo, timeout=120)
            if code == 0:
                versions[name] = output.strip().splitlines()[0]
    return versions


def run_stages(
    checks: tuple[str, ...], *, python: Path, repo: Path, timeout: int
) -> tuple[int, str]:
    """Run every check command; the stage passes only if all of them pass."""

    failures: list[str] = []
    summaries: list[str] = []
    for raw in checks:
        command = parse_check(raw)
        code, output = _command(
            [command.tool.value, *command.args], python=python, repo=repo, timeout=timeout
        )
        summaries.append(summarize(output))
        if code != 0:
            failures.append(f"{raw}: {summarize(output)}")
    if failures:
        return 1, "; ".join(failures)[:400]
    return 0, " | ".join(summaries)[:400]


def _apply_patch(repo: Path, patch: Path) -> tuple[int, str]:
    return _run(
        [_tool("git"), "apply", "--whitespace=nowarn", str(patch)],
        cwd=repo,
        timeout=120,
    )


def _write_reference_patch(bare: Path, base: str, fix: str, destination: Path) -> None:
    code, output = _run(
        [_tool("git"), "diff", base, fix, "--"],
        cwd=bare,
        timeout=INSTALL_TIMEOUT_S,
    )
    if code != 0:
        raise RuntimeError(f"git diff {base} {fix} failed: {summarize(output)}")
    if not output.strip():
        raise RuntimeError(f"git diff {base} {fix} is empty")
    destination.write_text(output, encoding="utf-8")


def _reset(repo: Path) -> None:
    _run([_tool("git"), "checkout", "--force", "--", "."], cwd=repo, timeout=120)
    _run([_tool("git"), "clean", "-fdx"], cwd=repo, timeout=120)


def check_task(document: TaskDocument, corpus: Path, runner: Runner, cache: Path) -> SanityResult:
    repo = runner.workdir / document.id / "repo"
    venv = runner.workdir / document.id / "venv"
    bundle = heldout_path(corpus, document.id)
    heldout = read_heldout(bundle)
    if document.base_commit is None:
        raise RuntimeError(f"{document.id}: base_commit is missing")

    bare = bare_clone(document.repo, cache)
    checkout(bare, document.base_commit, repo)
    python, python_version = create_environment(
        repo, venv, {parse_check(check).tool for check in document.checks}
    )

    base_checks_code, base_checks_summary = run_stages(
        document.checks, python=python, repo=repo, timeout=runner.command_timeout_s
    )

    for source in heldout_files(bundle, heldout):
        target = repo / source.relative_to(bundle / "files")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    heldout_check = parse_check(heldout.command)
    base_heldout_code, base_heldout_output = _command(
        [heldout_check.tool.value, *heldout_check.args],
        python=python,
        repo=repo,
        timeout=runner.command_timeout_s,
    )
    base_heldout_summary = summarize(base_heldout_output)

    _reset(repo)
    if document.fix_commit is not None:
        reference = runner.workdir / document.id / "reference.patch"
        _write_reference_patch(bare, document.base_commit, document.fix_commit, reference)
    else:
        reference = bundle / "reference.patch"
    code, output = _apply_patch(repo, reference)
    if code != 0:
        raise RuntimeError(f"{document.id}: reference patch does not apply: {summarize(output)}")

    fix_checks_code, fix_checks_summary = run_stages(
        document.checks, python=python, repo=repo, timeout=runner.command_timeout_s
    )
    fix_heldout_code, fix_heldout_output = _command(
        [heldout_check.tool.value, *heldout_check.args],
        python=python,
        repo=repo,
        timeout=runner.command_timeout_s,
    )

    return SanityResult(
        task_id=document.id,
        checked_at=dt.datetime.now(dt.UTC).replace(microsecond=0),
        python_version=python_version,
        tool_versions=_tool_versions(python, repo, document.checks),
        repo=document.repo,
        base_commit=document.base_commit,
        fix_commit=document.fix_commit,
        checks=document.checks,
        heldout_command=heldout.command,
        base_checks=StageResult(exit_code=base_checks_code, summary=base_checks_summary),
        base_heldout=StageResult(exit_code=base_heldout_code, summary=base_heldout_summary),
        fix_checks=StageResult(exit_code=fix_checks_code, summary=fix_checks_summary),
        fix_heldout=StageResult(exit_code=fix_heldout_code, summary=summarize(fix_heldout_output)),
    )


def _selected(corpus: Path, wanted: tuple[str, ...]) -> list[Path]:
    paths = discover_task_paths(corpus)
    if not wanted:
        return paths
    return [path for path in paths if path.parent.name in wanted]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("benchmarks"))
    parser.add_argument("--task", action="append", default=[], help="Task id, repeatable.")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="Keep the work directory.")
    parser.add_argument("--no-write", action="store_true", help="Do not write sanity.json.")
    args = parser.parse_args(argv)

    if shutil.which("uv") is None:
        print("uv is not on PATH", file=sys.stderr)
        return 2

    corpus = args.corpus.resolve()
    paths = _selected(corpus, tuple(args.task))
    if not paths:
        print(f"no tasks under {corpus}", file=sys.stderr)
        return 2

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="fixjury-sanity-"))
    workdir.mkdir(parents=True, exist_ok=True)
    cache = workdir / "repos"
    cache.mkdir(parents=True, exist_ok=True)
    runner = Runner(
        workdir=workdir,
        command_timeout_s=DEFAULT_COMMAND_TIMEOUT_S,
        write_evidence=not args.no_write,
    )
    documents = [read_task(path) for path in paths]

    for document in documents:
        bare_clone(document.repo, cache)

    failures = 0

    def work(document: TaskDocument) -> tuple[str, SanityResult | None, str]:
        try:
            result = check_task(document, corpus, runner, cache)
        except Exception as exc:  # one broken task must not stop the batch
            return document.id, None, f"{type(exc).__name__}: {exc}"
        if runner.write_evidence:
            write_sanity(heldout_path(corpus, document.id) / SANITY_FILENAME, result)
        return document.id, result, ""

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for task_id, result, error in pool.map(work, documents):
            if result is None:
                failures += 1
                print(f"{task_id}: ERROR {error}")
                continue
            status = "ok" if result.ok else "FAIL"
            gaps = "; ".join(result.problems())
            print(
                f"{task_id}: {status} base_checks={result.base_checks.exit_code} "
                f"base_heldout={result.base_heldout.exit_code} "
                f"fix_checks={result.fix_checks.exit_code} "
                f"fix_heldout={result.fix_heldout.exit_code}" + (f" :: {gaps}" if gaps else "")
            )
            if not result.ok:
                failures += 1

    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"{len(documents) - failures}/{len(documents)} tasks sane")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
