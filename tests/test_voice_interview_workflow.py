"""Regression contracts for the host-driven voice interview loop."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question


def _voice_modules():
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return importlib.import_module("interview_mcp"), importlib.import_module("voice_probe")


def test_voice_mcp_server_explains_per_answer_logging_and_automatic_finish():
    _, voice_probe = _voice_modules()

    instructions = voice_probe.mcp.instructions

    assert "record_candidate_answer" in instructions
    assert "every finalized candidate answer" in instructions.lower()
    assert "next_action" in instructions
    assert "finish_interview" in instructions


async def test_interview_progress_requires_topic_coverage_and_at_least_eight_answers():
    plan = importlib.import_module("mock_interviewer.interview_plan")
    Turn = importlib.import_module("mock_interviewer.domain.models").Turn
    topics = list(plan.REQUIRED_TOPICS)

    seven_answers = [
        item
        for index, topic in enumerate(topics[:7], start=1)
        for item in (
            Turn(f"q{index}", "interviewer", f"Question {index}", topic_id=topic),
            Turn(f"a{index}", "candidate", f"Answer {index}"),
        )
    ]
    progress = plan.interview_progress(seven_answers, pending_answer=True)

    assert progress["candidate_answers"] == 8
    assert progress["missing_topics"] == [topics[7]]
    assert progress["ready_to_finish"] is False


def test_interview_progress_finishes_at_ten_answers_and_reports_missing_topics():
    plan = importlib.import_module("mock_interviewer.interview_plan")
    Turn = importlib.import_module("mock_interviewer.domain.models").Turn
    turns = [
        item
        for index in range(1, 11)
        for item in (
            Turn(f"q{index}", "interviewer", f"Question {index}", topic_id="motivation"),
            Turn(f"a{index}", "candidate", f"Answer {index}"),
        )
    ]

    progress = plan.interview_progress(turns)

    assert progress["candidate_answers"] == 10
    assert progress["missing_topics"]
    assert progress["ready_to_finish"] is True


@pytest.mark.asyncio
async def test_final_answer_is_saved_without_adding_an_unasked_question():
    service_module = importlib.import_module("mock_interviewer.service")
    service = service_module.InterviewService(
        model=FakeModel([next_question("motivation", "Почему backend?")]),
        resume_parser=FakeResumeParser(),
    )
    session = await service.start(resume_text="Студент, учебный backend-проект")

    finished_line = await service.submit_answer(
        session.id,
        "Хочу развиваться в backend.",
        event_id="final-answer",
        finish_after_answer=True,
    )

    assert finished_line.status == "awaiting_report"
    assert finished_line.turns[-1].role == "candidate"
    assert not any(turn.role == "interviewer" for turn in finished_line.turns[2:])


@pytest.mark.asyncio
async def test_mcp_logs_each_answer_then_signals_finish_after_full_topic_coverage():
    bridge, _ = _voice_modules()
    topics = [
        "education",
        "project",
        "personal_contribution",
        "teamwork",
        "challenge",
        "reflection",
        "expectations",
    ]
    started = await bridge.start_interview(
        resume_text="Студент, опыт учебного API и командного проекта.",
        first_turn_json=json.dumps(
            next_question("motivation", "Почему вас заинтересовал backend?")
        ),
    )
    assert started["ok"] is True
    session_id = started["session_id"]

    try:
        for index in range(8):
            next_turn_json = ""
            if index < len(topics):
                next_turn_json = json.dumps(
                    next_question(topics[index], f"Вопрос по теме {topics[index]}")
                )
            result = await bridge.record_candidate_answer(
                session_id=session_id,
                transcript=f"Ответ кандидата номер {index + 1}",
                event_id=f"answer-{index + 1}",
                next_turn_json=next_turn_json,
            )
            assert result["ok"] is True
            if index < 7:
                assert result["interview_progress"]["candidate_answers"] == index + 1
                assert result["next_action"] == "ask_next_question"
            if index == 0:
                duplicate = await bridge.record_candidate_answer(
                    session_id=session_id,
                    transcript="Ответ кандидата номер 1",
                    event_id="answer-1",
                    next_turn_json="invalid on purpose; retries must not consume it",
                )
                assert duplicate["duplicate"] is True
                assert duplicate["interview_progress"]["candidate_answers"] == 1

        assert result["interview_progress"]["candidate_answers"] == 8
        assert result["interview_progress"]["missing_topics"] == []
        assert result["next_action"] == "finish_interview"
        assert result["turns"][-1]["role"] == "candidate"

        completed = await bridge.finish_interview(
            session_id=session_id,
            report_json=json.dumps(
                {
                    "recommendation": "insufficient_data",
                    "scores": {},
                    "strengths": [],
                    "growth_areas": [],
                    "evidence": [],
                    "uncertainties": ["Недостаточно проверяемых примеров для оценки."],
                    "disclaimer": "Учебная обратная связь для практики, не решение о найме.",
                }
            ),
        )
        assert completed["status"] == "completed"
        assert completed["interview_complete"] is True
        assert "интервью завершено" in completed["instruction"].lower()
    finally:
        await bridge.delete_interview(session_id)
