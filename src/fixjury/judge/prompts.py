"""Versioned prompt files (SPEC §3.2, AGENTS.md).

The prompt is a benchmark input: two evaluations with different prompts are not
comparable. The version is the digest of the file, so editing a word changes the version
without anyone remembering to bump a constant, and the version is stored with every
assessment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

#: The prompt a first assessment uses, and the one an escalated assessment uses.
EVALUATE_PROMPT = "judge_evaluate"
ESCALATE_PROMPT = "judge_escalate"


class PromptError(RuntimeError):
    """The prompt file is missing or empty."""


@dataclass(frozen=True)
class Prompt:
    """A prompt's text and the version that identifies it."""

    name: str
    text: str
    version: str


def load_prompt(name: str, *, directory: Path | None = None) -> Prompt:
    """Read one prompt from ``src/fixjury/prompts/<name>.md``."""

    path = (directory or PROMPT_DIR) / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"{path}: cannot read the judge prompt: {exc}") from exc
    if not text.strip():
        raise PromptError(f"{path}: the judge prompt is empty")
    return Prompt(name=name, text=text, version=_version(text))


def _version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "ESCALATE_PROMPT",
    "EVALUATE_PROMPT",
    "PROMPT_DIR",
    "Prompt",
    "PromptError",
    "load_prompt",
]
