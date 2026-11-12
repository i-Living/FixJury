from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.bench import BenchError, BenchPlan, load_plan

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"


def test_the_plan_defaults_to_the_corpus_index() -> None:
    plan = load_plan(corpus_root=CORPUS, agents=("baseline",))

    assert plan.pairs == len(plan.tasks)
    assert "T-001" in plan.tasks
    assert plan.attempt == 1


def test_explicit_tasks_win_over_the_index() -> None:
    plan = load_plan(corpus_root=CORPUS, agents=("baseline",), tasks=("T-001", "T-002"))

    assert plan.tasks == ("T-001", "T-002")
    assert plan.pairs == 2


def test_every_pair_is_counted() -> None:
    plan = BenchPlan(tasks=("T-001", "T-002", "T-003"), agents=("baseline", "swe-agent"))

    assert plan.pairs == 6


def test_no_agents_is_refused() -> None:
    with pytest.raises(BenchError, match="no agents"):
        load_plan(corpus_root=CORPUS, agents=())


def test_an_unknown_task_is_refused() -> None:
    with pytest.raises(Exception, match="T-999"):
        load_plan(corpus_root=CORPUS, agents=("baseline",), tasks=("T-999",))


def test_the_attempt_is_carried_into_the_plan() -> None:
    plan = load_plan(corpus_root=CORPUS, agents=("baseline",), tasks=("T-001",), attempt=2)

    assert plan.attempt == 2
