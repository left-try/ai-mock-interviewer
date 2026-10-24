from __future__ import annotations

import pytest

from mock_interviewer.errors import InvalidReport, InvalidSessionTransition
from mock_interviewer.system_design import CRITERIA, SystemDesignInterviewService


@pytest.mark.asyncio
async def test_start_defaults_to_text_only_when_candidate_does_not_consent():
    service = SystemDesignInterviewService()

    session = await service.start(level="middle", board_consent=False)

    assert session.status == "active"
    assert session.phase == "discovery"
    assert session.board_enabled is False
    assert session.board_url is None
    assert session.turns[0].role == "interviewer"
    assert session.turns[0].phase == "discovery"


@pytest.mark.asyncio
async def test_answer_is_persisted_idempotently_and_conflicting_replay_is_rejected():
    service = SystemDesignInterviewService()
    session = await service.start(level="junior", board_consent=False)

    first = await service.persist_answer(
        session.id, " Я начну с требований и аудитории. ", event_id="answer-1"
    )
    replay = await service.persist_answer(
        session.id, "Я начну с требований и аудитории.", event_id="answer-1"
    )

    assert len([turn for turn in first.turns if turn.role == "candidate"]) == 1
    assert replay.version == first.version
    assert replay.turns[-1].text == "Я начну с требований и аудитории."
    with pytest.raises(InvalidSessionTransition, match="reused"):
        await service.persist_answer(session.id, "Другое содержание", event_id="answer-1")


@pytest.mark.asyncio
async def test_board_changes_must_be_read_before_next_question_and_are_linked_to_answer():
    service = SystemDesignInterviewService()
    session = await service.start(level="middle", board_consent=True)
    await service.persist_answer(session.id, "Короткие ссылки читаются часто.", event_id="answer-1")
    proposal = {
        "kind": "question",
        "phase": "design",
        "topic_id": "requirements",
        "text": "Какие требования к созданию и перенаправлению ссылок важны?",
    }

    with pytest.raises(InvalidSessionTransition, match="whiteboard changes"):
        await service.propose_next_turn(session.id, "answer-1", proposal)

    board_event = {
        "event_id": "board-1",
        "sequence": 1,
        "kind": "node_added",
        "payload": {"node_id": "api", "label": "API"},
    }
    updated, added = await service.read_board_changes(
        session.id, after_cursor=0, source_events=[board_event]
    )
    proposed = await service.propose_next_turn(session.id, "answer-1", proposal)

    assert added[0]["related_turn_id"] == "turn-2"
    assert updated.board_cursor == 1
    assert proposed.phase == "design"
    assert proposed.pending_answer_event_id is None


def _valid_report(*, turn_id: str, quote: str) -> dict:
    return {
        "summary": "Собеседник последовательно разобрал основные части задачи.",
        "criteria": [
            {
                "criterion_id": criterion,
                "score": 3,
                "assessment": "Есть подтверждение в ответе кандидата.",
                "evidence": [
                    {"source_type": "turn", "source_id": turn_id, "quote": quote}
                ],
            }
            for criterion in sorted(CRITERIA)
        ],
        "strengths": [],
        "growth_areas": [],
        "uncertainties": [],
    }


@pytest.mark.asyncio
async def test_report_requires_exact_quote_from_candidate_answer():
    service = SystemDesignInterviewService()
    session = await service.start(level="middle", board_consent=False)
    session = await service.persist_answer(
        session.id, "Я использую PostgreSQL для метаданных.", event_id="answer-final", finish_after_answer=True
    )

    report = _valid_report(turn_id="turn-2", quote="В ответе этого не было")
    with pytest.raises(InvalidReport, match="quote"):
        await service.finish(session.id, report)

    report = _valid_report(turn_id="turn-2", quote="PostgreSQL для метаданных")
    finished = await service.finish(session.id, report)

    assert finished.status == "completed"
    assert len(finished.report["criteria"]) == len(CRITERIA)
    assert finished.report["disclaimer"] == service.DISCLAIMER

