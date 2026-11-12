"""Agent adapters: the baseline plus room for external harnesses."""

from __future__ import annotations

from fixjury.agents.base import AgentAdapter, AgentResult, ModelCall, RunRequest
from fixjury.agents.baseline import BaselineAdapter, run_baseline

__all__ = [
    "AgentAdapter",
    "AgentResult",
    "BaselineAdapter",
    "ModelCall",
    "RunRequest",
    "run_baseline",
]
