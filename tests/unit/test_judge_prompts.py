from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.judge.prompts import (
    ESCALATE_PROMPT,
    EVALUATE_PROMPT,
    PromptError,
    load_prompt,
)


def test_the_shipped_prompts_load_and_carry_their_text() -> None:
    evaluate = load_prompt(EVALUATE_PROMPT)
    escalate = load_prompt(ESCALATE_PROMPT)

    assert "JSON" in evaluate.text
    assert len(evaluate.version) == 12
    assert escalate.text != evaluate.text


def test_the_version_follows_the_text(tmp_path: Path) -> None:
    (tmp_path / "judge_evaluate.md").write_text("first text\n", encoding="utf-8")
    first = load_prompt(EVALUATE_PROMPT, directory=tmp_path)
    (tmp_path / "judge_evaluate.md").write_text("second text\n", encoding="utf-8")
    second = load_prompt(EVALUATE_PROMPT, directory=tmp_path)

    assert first.version != second.version


def test_a_missing_prompt_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="cannot read"):
        load_prompt("absent", directory=tmp_path)


def test_an_empty_prompt_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "empty.md").write_text("   \n", encoding="utf-8")

    with pytest.raises(PromptError, match="empty"):
        load_prompt("empty", directory=tmp_path)
