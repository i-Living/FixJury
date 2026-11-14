from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from fixjury.agents.registry import UnknownAgentError, build_adapter, known_agents
from fixjury.run import RunError, agent_spec_text, checkout_base, config_hash
from fixjury.tasks.loader import read_task

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"


def git(workdir: Path, *argv: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, test repository
        ["git", *argv],  # noqa: S607 - git comes from PATH in tests
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "first")
    return tmp_path


def test_the_spec_text_carries_what_the_agent_must_know() -> None:
    document = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")

    text = agent_spec_text(document)

    assert "T-001" in text
    assert document.title in text
    assert document.repo in text
    assert document.base_commit or "" in text
    first_line = (document.issue.body or "").splitlines()[0]
    assert first_line in text
    for item in document.acceptance:
        assert item in text
    for check in document.checks:
        assert check in text
    for path in document.allowed_paths:
        assert path in text


def test_the_spec_text_is_deterministic() -> None:
    document = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")

    assert agent_spec_text(document) == agent_spec_text(document)


def test_the_spec_text_ends_with_one_newline() -> None:
    document = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")

    text = agent_spec_text(document)

    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def make_hash(
    *,
    model: str = "m",
    image: str = "python:3.12-slim",
    max_steps: int = 30,
    agent_version: str = "1",
) -> str:
    return config_hash(
        task_id="T-001",
        base_commit="a" * 40,
        agent="baseline",
        agent_version=agent_version,
        model=model,
        image=image,
        timeout_s=1200,
        max_steps=max_steps,
        pricing_version="unpriced",
    )


def test_config_hash_is_stable_for_the_same_configuration() -> None:
    assert make_hash() == make_hash()


@pytest.mark.parametrize(
    "changed",
    [
        lambda: make_hash(model="other-model"),
        lambda: make_hash(image="python:3.11-slim"),
        lambda: make_hash(max_steps=10),
        lambda: make_hash(agent_version="2"),
    ],
    ids=["model", "image", "max_steps", "agent_version"],
)
def test_config_hash_changes_with_everything_that_changes_the_comparison(
    changed: Callable[[], str],
) -> None:
    assert changed() != make_hash()


def test_checkout_base_lands_on_the_requested_commit(repo: Path, tmp_path: Path) -> None:
    first = git(repo, "rev-parse", "HEAD")
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(repo, "commit", "--quiet", "-am", "second")
    dest = tmp_path / "checkout"

    checkout_base(str(repo), first, dest)

    assert (dest / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git(dest, "rev-parse", "HEAD") == first


def test_checkout_base_refuses_an_unknown_commit(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(RunError, match="is not in"):
        checkout_base(str(repo), "0" * 40, tmp_path / "checkout")


def test_the_registry_builds_the_baseline_adapter() -> None:
    adapter = build_adapter("baseline")

    assert adapter.name == "baseline"
    assert "baseline" in known_agents()


def test_the_registry_names_the_agents_it_knows() -> None:
    with pytest.raises(UnknownAgentError, match="known agents"):
        build_adapter("no-such-agent")


def test_the_harnesses_are_registered_even_without_an_image() -> None:
    assert "mini" in known_agents()
    assert "openhands" in known_agents()
