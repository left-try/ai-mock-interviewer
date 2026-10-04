"""Regression coverage for cycles, duplicate events and session isolation."""

from __future__ import annotations

import asyncio

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question, report
from tests.test_public_contracts import app_module


def make_service(*responses):
    service_type = app_module("service").InterviewService
    return service_type(model=FakeModel(list(responses)), resume_parser=FakeResumeParser())


@pytest.mark.asyncio
async def test_duplicate_event_id_does_not_append_duplicate_candidate_turn_or_call_model_twice():
    model = FakeModel(
        [
            next_question("motivation", "Почему backend?"),
            next_question("project", "Расскажите о проекте."),
        ]
    )
    service_type = app_module("service").InterviewService
    service = service_type(model=model, resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный API")
    kwargs = {"event_id": "evt-unique-1"}
    once = await service.submit_answer(session.id, "Мне нравится backend.", **kwargs)
    call_count = len(model.calls)
    twice = await service.submit_answer(session.id, "Мне нравится backend.", **kwargs)

    assert len(model.calls) == call_count
    assert len([t for t in twice.turns if t.role == "candidate"]) == 1
    assert twice.id == once.id


@pytest.mark.asyncio
async def test_concurrent_answers_for_same_session_are_serialized_or_one_is_rejected():
    service = make_service(
        next_question("motivation", "Почему backend?"),
        next_question("project", "Расскажите о проекте."),
        next_question("teamwork", "Как работали в команде?"),
    )
    session = await service.start(resume_text="Учебный API")
    results = await asyncio.gather(
        service.submit_answer(session.id, "Ответ A", event_id="evt-a"),
        service.submit_answer(session.id, "Ответ B", event_id="evt-b"),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, app_module("errors").ConcurrentSessionUpdate) for item in results) == 1


@pytest.mark.asyncio
async def test_sessions_are_isolated_and_never_share_resume_or_turns():
    service = make_service(
        next_question("motivation", "Почему backend? для кандидата 1"),
        next_question("motivation", "Почему backend? для кандидата 2"),
    )
    first = await service.start(resume_text="Резюме кандидата alpha")
    second = await service.start(resume_text="Резюме кандидата beta")

    assert first.id != second.id
    assert first.resume_text != second.resume_text
    assert first.turns[0].text != second.turns[0].text


@pytest.mark.asyncio
async def test_graph_does_not_repeat_identical_question_without_progress():
    duplicate = next_question("motivation", "Почему backend?")
    service = make_service(duplicate, duplicate, duplicate)
    session = await service.start(resume_text="Учебный API")
    session = await service.submit_answer(session.id, "Потому что интересно.")

    interviewer_questions = [t.text for t in session.turns if t.role == "interviewer"]
    assert len(interviewer_questions) == len(set(interviewer_questions))
    assert session.status != "active" or len(interviewer_questions) == 1


@pytest.mark.asyncio
async def test_finish_is_idempotent_and_does_not_generate_second_report():
    model = FakeModel([next_question("motivation", "Почему backend?"), report()])
    service_type = app_module("service").InterviewService
    service = service_type(model=model, resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный API")
    first = await service.finish(session.id)
    call_count = len(model.calls)
    second = await service.finish(session.id)

    assert second.report == first.report
    assert len(model.calls) == call_count
