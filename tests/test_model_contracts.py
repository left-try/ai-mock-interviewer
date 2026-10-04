"""Schema, failure and evidence validation for all model-facing boundaries."""

from __future__ import annotations

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question, report
from tests.test_public_contracts import app_module


@pytest.mark.parametrize(
    "bad_response",
    [
        None,
        {},
        {"kind": "question", "topic_id": "motivation", "text": ""},
        {"kind": "question", "topic_id": "unknown_topic", "text": "Вопрос?"},
        {"kind": "browser", "topic_id": "motivation", "text": "Откройте сайт"},
        {"kind": "question", "topic_id": "motivation", "text": "Вопрос?", "confidence": 1.5},
        {"kind": "question", "topic_id": "motivation", "text": "Вопрос?" * 1000},
    ],
)
@pytest.mark.asyncio
async def test_invalid_next_turn_structures_are_rejected_before_user_delivery(bad_response):
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel([bad_response]),
        resume_parser=FakeResumeParser(),
    )
    with pytest.raises(app_module("errors").InvalidModelOutput):
        await service.start(resume_text="Учебный API")
    assert await service.list_sessions() == []


@pytest.mark.parametrize("kind", ["question", "follow_up", "repeat"])
@pytest.mark.asyncio
async def test_only_allowlisted_interviewer_turn_kinds_are_accepted(kind):
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel([next_question("motivation", "Почему backend?", kind=kind)]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный API")
    assert session.turns[-1].kind == kind


@pytest.mark.parametrize("recommendation", ["strong_signal", "mixed_signal", "insufficient_data"])
@pytest.mark.asyncio
async def test_report_recommendation_must_be_from_allowlist(recommendation):
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel(
            [next_question("motivation", "Почему backend?"), report(recommendation=recommendation)]
        ),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный API")
    result = await service.finish(session.id)
    assert result.report.recommendation == recommendation


@pytest.mark.asyncio
async def test_invalid_report_recommendation_is_rejected():
    malformed = report(recommendation="hire_immediately")
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel([next_question("motivation", "Почему backend?"), malformed]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный API")
    with pytest.raises(app_module("errors").InvalidReport):
        await service.finish(session.id)


@pytest.mark.asyncio
async def test_report_model_failure_never_marks_session_completed_or_fabricates_a_report():
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel(
            [
                next_question("motivation", "Почему backend?"),
                TimeoutError("provider unavailable"),
            ]
        ),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный API")

    with pytest.raises(app_module("errors").ModelProviderError):
        await service.finish(session.id)
    after_failure = await service.get_session(session.id)
    assert after_failure.status != "completed"
    assert after_failure.report is None


@pytest.mark.asyncio
async def test_provider_error_does_not_leak_credentials_or_raw_exception_to_candidate():
    secret = "sk-test-secret-123"
    service_type = app_module("service").InterviewService
    service = service_type(
        model=FakeModel([RuntimeError(f"Authorization failed with {secret}")]),
        resume_parser=FakeResumeParser(),
    )
    with pytest.raises(app_module("errors").ModelProviderError) as error:
        await service.start(resume_text="Учебный API")

    assert secret not in str(error.value)
    assert "Authorization failed" not in str(error.value)
