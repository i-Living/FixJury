from __future__ import annotations

import pytest

from fixjury.judge import service as judge_service
from fixjury.judge.service import (
    INDEPENDENCE_NOTE,
    SHARED_SESSION_NOTE,
    JudgeServiceError,
    independence_note,
    judge_configuration,
    require_secure_endpoint,
    setup_from_settings,
)


def test_a_plaintext_endpoint_is_refused() -> None:
    with pytest.raises(JudgeServiceError, match="must be https"):
        require_secure_endpoint("http://opencode.ai/zen/go/v1")


def test_loopback_plaintext_is_allowed_for_a_local_gateway() -> None:
    require_secure_endpoint("http://127.0.0.1:8080/v1")
    require_secure_endpoint("http://localhost:8080/v1")


def test_https_is_the_default_shape() -> None:
    require_secure_endpoint("https://opencode.ai/zen/go/v1")


def test_a_missing_judge_model_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_model", "")

    with pytest.raises(JudgeServiceError, match="no judge model configured"):
        setup_from_settings()


def test_the_judge_cannot_be_the_model_under_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_model", "same-model")
    monkeypatch.setattr(judge_service.settings, "llm_model", "same-model")

    with pytest.raises(JudgeServiceError, match="must differ"):
        setup_from_settings()


def test_a_configured_judge_builds_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_model", "strong-model")
    monkeypatch.setattr(judge_service.settings, "llm_model", "flash-model")
    monkeypatch.setattr(judge_service.settings, "judge_session", "judge-session")

    setup = setup_from_settings()

    assert setup.primary_model == "strong-model"
    assert getattr(setup.provider, "session", "") == "judge-session"


def test_the_independence_note_says_when_the_session_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_session", "")
    assert independence_note() == INDEPENDENCE_NOTE + SHARED_SESSION_NOTE

    monkeypatch.setattr(judge_service.settings, "judge_session", "judge-session")
    assert independence_note() == INDEPENDENCE_NOTE


def test_a_configuration_outside_its_ranges_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_token_budget", -1)

    with pytest.raises(JudgeServiceError, match="configuration is invalid"):
        judge_configuration()


def test_the_configuration_takes_the_configured_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(judge_service.settings, "judge_confidence_threshold", 0.75)
    monkeypatch.setattr(judge_service.settings, "judge_token_budget", 5000)
    monkeypatch.setattr(judge_service.settings, "judge_timeout_s", 120.0)

    config = judge_configuration(mode="adaptive")

    assert config.confidence_threshold == 0.75
    assert config.token_budget == 5000
    assert config.timeout_s == 120
