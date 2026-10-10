"""Contracts for saving voice answers before preparing the next question."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from mock_interviewer.errors import InvalidModelOutput, InvalidSessionTransition
from tests.fakes import FakeModel, FakeResumeParser, next_question


@pytest.mark.asyncio
async def test_submit_answer_persists_without_invoking_model_or_appending_question():
    service_module = importlib.import_module("mock_interviewer.service")
    model = FakeModel([
        next_question("motivation", "Почему backend?"),
        next_question("education", "Что вы изучали?"),
    ])
    service = service_module.InterviewService(model=model, resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный backend-проект")
    call_count_after_start = len(model.calls)

    saved = await service.persist_answer(
        session.id, "Хочу развивать backend.", event_id="answer-1"
    )

    assert len(model.calls) == call_count_after_start
    assert saved.turns[-1].role == "candidate"
    assert saved.turns[-1].text == "Хочу развивать backend."
    assert saved.status == "active"
    assert not any(turn.role == "interviewer" for turn in saved.turns[2:])
    assert getattr(saved, "pending_answer_event_id", None) == "answer-1"


@pytest.mark.asyncio
async def test_duplicate_answer_event_returns_one_saved_candidate_turn():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([
            next_question("motivation", "Почему backend?"),
            next_question("education", "Что вы изучали?"),
        ]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")

    first = await service.persist_answer(session.id, "Ответ", event_id="same-event")
    replay = await service.persist_answer(session.id, "Ответ", event_id="same-event")

    assert [turn.text for turn in replay.turns if turn.role == "candidate"] == ["Ответ"]
    assert replay.version == first.version
    assert getattr(replay, "pending_answer_event_id", None) == "same-event"


@pytest.mark.asyncio
async def test_reusing_answer_event_id_with_different_text_is_rejected():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([
            next_question("motivation", "Почему backend?"),
            next_question("education", "Что вы изучали?"),
        ]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")
    await service.persist_answer(session.id, "Оригинальный ответ", event_id="answer-1")

    with pytest.raises(InvalidSessionTransition):
        await service.persist_answer(session.id, "Изменённый текст", event_id="answer-1")

    current = await service.get_session(session.id)
    assert [turn.text for turn in current.turns if turn.role == "candidate"] == ["Оригинальный ответ"]


@pytest.mark.asyncio
async def test_final_transcript_tail_remains_recordable_once_before_report():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([next_question("motivation", "Почему backend?")]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")
    await service.submit_answer(
        session.id, "Основной ответ", event_id="answer-1", finish_after_answer=True
    )

    corrected = await service.submit_answer(
        session.id,
        "Основной ответ с важным уточнением.",
        event_id="transcript-tail-1",
        allow_final_correction=True,
    )
    replay = await service.submit_answer(
        session.id,
        "Основной ответ с важным уточнением.",
        event_id="transcript-tail-1",
        allow_final_correction=True,
    )

    assert corrected.status == "awaiting_report"
    assert [turn.text for turn in replay.turns if turn.role == "candidate"] == [
        "Основной ответ", "Основной ответ с важным уточнением."
    ]
    assert replay.version == corrected.version


@pytest.mark.asyncio
async def test_invalid_proposal_keeps_answer_saved_and_retryable():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([
            next_question("motivation", "Почему backend?"),
            next_question("education", "Что вы изучали?"),
        ]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")
    await service.persist_answer(session.id, "Ответ", event_id="answer-1")
    propose = getattr(service, "propose_next_turn", None)

    assert callable(propose), "InterviewService must expose a retryable next-turn proposal operation"
    with pytest.raises(InvalidModelOutput):
        await propose(session.id, "answer-1", {"kind": "question", "topic_id": "unknown", "text": "X"})

    after_invalid = await service.get_session(session.id)
    assert len([turn for turn in after_invalid.turns if turn.role == "candidate"]) == 1
    assert getattr(after_invalid, "pending_answer_event_id", None) == "answer-1"

    proposed = await propose(
        session.id,
        "answer-1",
        next_question("education", "Что вы изучали?"),
    )
    assert proposed.turns[-1].role == "interviewer"
    assert proposed.turns[-1].text == "Что вы изучали?"
    assert getattr(proposed, "pending_answer_event_id", None) is None


@pytest.mark.asyncio
async def test_new_answer_is_rejected_while_prior_answer_has_no_question_proposal():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([
            next_question("motivation", "Почему backend?"),
            next_question("education", "Что вы изучали?"),
        ]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")
    await service.persist_answer(session.id, "Первый ответ", event_id="answer-1")

    with pytest.raises(InvalidSessionTransition):
        await service.persist_answer(session.id, "Второй ответ", event_id="answer-2")
    with pytest.raises(InvalidSessionTransition):
        await service.submit_answer(session.id, "Обход через совместимый API", event_id="answer-legacy")

    current = await service.get_session(session.id)
    assert [turn.text for turn in current.turns if turn.role == "candidate"] == ["Первый ответ"]


@pytest.mark.asyncio
async def test_replaying_next_turn_proposal_does_not_duplicate_question():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([
            next_question("motivation", "Почему backend?"),
            next_question("education", "Что вы изучали?"),
        ]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Учебный backend-проект")
    await service.persist_answer(session.id, "Ответ", event_id="answer-1")
    propose = getattr(service, "propose_next_turn", None)
    assert callable(propose), "InterviewService must expose idempotent next-turn proposals"
    question = next_question("education", "Что вы изучали?")

    first = await propose(session.id, "answer-1", question)
    replay = await propose(session.id, "answer-1", question)

    assert [turn.text for turn in replay.turns if turn.role == "interviewer"] == [
        "Почему backend?", "Что вы изучали?"
    ]
    assert replay.version == first.version


def test_mcp_exposes_answer_only_and_next_turn_proposal_tools():
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    bridge = importlib.import_module("interview_mcp")
    answer_parameters = bridge.save_candidate_answer.__annotations__

    assert "next_turn_json" not in answer_parameters, (
        "save_candidate_answer must persist before the host generates its next question"
    )
    assert callable(getattr(bridge, "propose_next_turn", None))
