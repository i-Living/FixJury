from __future__ import annotations

import json

import pytest

from fixjury.judge.schema import JudgeResponseError, parse_response
from fixjury.models import Criterion, EvaluationVerdict, FindingSource


def payload(**overrides: object) -> str:
    body: dict[str, object] = {
        "verdict": "ACCEPTED",
        "confidence": 0.84,
        "criteria": [
            {"criterion": criterion.value, "score": 90, "rationale": "looks right"}
            for criterion in Criterion
        ],
        "findings": [],
        "reasoning_summary": "the patch replaces the broken call and adds a test",
        "limitations": [],
        "escalation_required": False,
    }
    body.update(overrides)
    return json.dumps(body)


def test_a_well_formed_reply_is_read() -> None:
    response = parse_response(payload())

    assert response.verdict is EvaluationVerdict.ACCEPTED
    assert response.confidence == 0.84
    assert len(response.criteria) == len(Criterion)


def test_a_fenced_reply_is_read() -> None:
    fenced = "```json\n" + payload() + "\n```"

    assert parse_response(fenced).verdict is EvaluationVerdict.ACCEPTED


def test_prose_around_the_object_is_tolerated() -> None:
    noisy = "Here is my assessment:\n" + payload() + "\nHope this helps."

    assert parse_response(noisy).confidence == 0.84


def test_a_reply_without_json_is_refused() -> None:
    with pytest.raises(JudgeResponseError, match="no JSON object"):
        parse_response("I think the patch is fine.")


def test_invalid_json_is_refused() -> None:
    with pytest.raises(JudgeResponseError, match="not valid JSON"):
        parse_response('{"verdict": "ACCEPTED", }')


def test_a_missing_criterion_is_refused() -> None:
    body = json.loads(payload())
    body["criteria"] = body["criteria"][:-1]

    with pytest.raises(JudgeResponseError, match="no score for"):
        parse_response(json.dumps(body))


def test_a_criterion_scored_twice_is_refused() -> None:
    body = json.loads(payload())
    body["criteria"].append({"criterion": "correctness", "score": 10, "rationale": "twice"})

    with pytest.raises(JudgeResponseError, match="scored twice"):
        parse_response(json.dumps(body))


def test_an_invented_verdict_is_refused() -> None:
    with pytest.raises(JudgeResponseError, match="does not match the judge schema"):
        parse_response(payload(verdict="APPROVED"))


def test_a_confidence_out_of_range_is_refused() -> None:
    with pytest.raises(JudgeResponseError):
        parse_response(payload(confidence=1.4))


def test_a_finding_is_stamped_as_the_judges_opinion_whatever_it_claims() -> None:
    finding = {
        "severity": "critical",
        "category": "security",
        "description": "the query interpolates the id",
        "evidence_ref": "diff:app/db.py:31",
        "impact": "any caller can read another tenant's rows",
        "path": "app/db.py",
        "line": 31,
        "source": "automated",
        "confirmed": True,
    }

    response = parse_response(payload(findings=[finding]))

    assert response.findings[0].source is FindingSource.judge
    assert response.findings[0].confirmed is False


def test_a_finding_without_evidence_is_refused() -> None:
    finding = {
        "severity": "major",
        "category": "correctness",
        "description": "this looks wrong",
        "evidence_ref": "",
        "impact": "the bug stays",
    }

    with pytest.raises(JudgeResponseError):
        parse_response(payload(findings=[finding]))
