"""Regression contracts for the host-driven voice interview loop."""

from __future__ import annotations

import importlib
import asyncio
import json
import sys
import threading
import time
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
    assert "include_transcript=false" in instructions
    assert "report_markdown" in instructions
    assert ".pdf" in instructions
    assert "preparing the final report" in instructions.lower()


@pytest.mark.asyncio
async def test_report_export_does_not_block_mcp_event_loop(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "REPORTS_DIR", tmp_path, raising=False)
    export_thread_ids = []
    event_loop_ticks = []
    original_export = bridge.export_report_files

    def slow_export(*args, **kwargs):
        export_thread_ids.append(threading.get_ident())
        time.sleep(0.08)
        return original_export(*args, **kwargs)

    monkeypatch.setattr(bridge, "export_report_files", slow_export)
    started = await bridge.start_interview(
        resume_text="Student backend project",
        first_turn_json=json.dumps(next_question("motivation", "Why backend?")),
    )
    session_id = started["session_id"]
    loop_thread_id = threading.get_ident()

    async def observe_loop():
        await asyncio.sleep(0.01)
        event_loop_ticks.append(True)

    try:
        result, _ = await asyncio.gather(
            bridge.finish_interview(
                session_id=session_id,
                report_json=json.dumps(
                    {
                        "recommendation": "insufficient_data",
                        "scores": {},
                        "strengths": [],
                        "growth_areas": [],
                        "evidence": [],
                        "uncertainties": ["В тесте нет ответов кандидата."],
                        "disclaimer": "Учебная обратная связь для практики.",
                    }
                ),
            ),
            observe_loop(),
        )

        assert result["ok"] is True
        assert event_loop_ticks == [True]
        assert export_thread_ids and export_thread_ids[0] != loop_thread_id
    finally:
        await bridge.delete_interview(session_id)


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


@pytest.mark.asyncio
async def test_mcp_accepts_final_transcript_tail_before_report_is_created():
    bridge, _ = _voice_modules()
    topics = ["education", "project", "personal_contribution", "teamwork", "challenge", "reflection", "expectations"]
    started = await bridge.start_interview(
        resume_text="Студент, опыт backend-проектов.",
        first_turn_json=json.dumps(next_question("motivation", "Почему backend?")),
    )
    session_id = started["session_id"]

    try:
        for index in range(8):
            await bridge.record_candidate_answer(
                session_id=session_id,
                transcript=f"Ответ {index + 1}",
                event_id=f"answer-{index + 1}",
                next_turn_json=(
                    json.dumps(next_question(topics[index], f"Вопрос {index + 1}"))
                    if index < len(topics)
                    else ""
                ),
            )

        late = await bridge.record_candidate_answer(
            session_id=session_id,
            transcript="И ещё я хочу попробовать разработать полноценный API.",
            event_id="transcript-tail-1",
        )

        assert late["ok"] is True
        assert late["status"] == "awaiting_report"
        assert late["interview_progress"]["candidate_answers"] == 9
        assert late["next_action"] == "finish_interview"
        assert late["candidate_turn"]["text"] == "И ещё я хочу попробовать разработать полноценный API."

        duplicate = await bridge.record_candidate_answer(
            session_id=session_id,
            transcript="И ещё я хочу попробовать разработать полноценный API.",
            event_id="transcript-tail-1",
        )
        assert duplicate["duplicate"] is True
        assert duplicate["interview_progress"]["candidate_answers"] == 9

        completed = await bridge.finish_interview(
            session_id=session_id,
            report_json=json.dumps(
                {
                    "recommendation": "insufficient_data",
                    "scores": {},
                    "strengths": [],
                    "growth_areas": [],
                    "evidence": [],
                    "uncertainties": ["Учебный прогон; в этом тесте оценка не формируется."],
                    "disclaimer": "Учебная обратная связь для практики.",
                }
            ),
        )
        assert completed["status"] == "completed"
        assert "turns" not in completed
        final_status = await bridge.interview_status(session_id=session_id)
        assert final_status["interview_progress"]["candidate_answers"] == 9
        assert final_status["turns"][-1]["text"] == "И ещё я хочу попробовать разработать полноценный API."

        too_late = await bridge.record_candidate_answer(
            session_id=session_id,
            transcript="Это уже после готового отчёта.",
            event_id="post-report-answer",
        )
        assert too_late["ok"] is False
        assert "no longer accepts" in too_late["error"]
    finally:
        await bridge.delete_interview(session_id)


@pytest.mark.asyncio
async def test_compact_voice_response_returns_only_new_turns_and_progress():
    bridge, _ = _voice_modules()
    started = await bridge.start_interview(
        resume_text="Студент, учебный backend-проект.",
        first_turn_json=json.dumps(next_question("motivation", "Почему backend?")),
    )
    session_id = started["session_id"]

    try:
        result = await bridge.record_candidate_answer(
            session_id=session_id,
            transcript="Хочу развивать backend-навыки.",
            event_id="compact-answer-1",
            next_turn_json=json.dumps(next_question("education", "Что изучали?")),
            include_transcript=False,
        )

        assert result["ok"] is True
        assert "turns" not in result
        assert result["candidate_turn"]["text"] == "Хочу развивать backend-навыки."
        assert result["next_turn"]["text"] == "Что изучали?"
        assert result["interview_progress"]["candidate_answers"] == 1
    finally:
        await bridge.delete_interview(session_id)


@pytest.mark.asyncio
async def test_report_error_names_unknown_evidence_criterion_for_repair():
    bridge, _ = _voice_modules()
    started = await bridge.start_interview(
        resume_text="Student backend project",
        first_turn_json=json.dumps(next_question("motivation", "Why backend?")),
    )
    session_id = started["session_id"]

    try:
        result = await bridge.finish_interview(
            session_id=session_id,
            report_json=json.dumps(
                {
                    "recommendation": "mixed_signal",
                    "scores": {},
                    "strengths": [],
                    "growth_areas": [],
                    "evidence": [
                        {
                            "criterion": "expectations",
                            "source_turn_id": "resume",
                            "quote": "Student backend project",
                            "observation": "The resume mentions a project.",
                        }
                    ],
                    "uncertainties": [],
                    "disclaimer": "Training practice feedback.",
                }
            ),
        )

        assert result["ok"] is False
        assert "expectations" in result["error"]
        assert "Allowed criteria" in result["error"]
    finally:
        await bridge.delete_interview(session_id)


@pytest.mark.asyncio
async def test_finish_returns_visible_markdown_and_exported_report_files(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "REPORTS_DIR", tmp_path, raising=False)
    started = await bridge.start_interview(
        resume_text="Student backend project",
        first_turn_json=json.dumps(next_question("motivation", "Why backend?")),
    )
    session_id = started["session_id"]

    try:
        result = await bridge.finish_interview(
            session_id=session_id,
            report_json=json.dumps(
                {
                    "recommendation": "insufficient_data",
                    "scores": {},
                    "strengths": [],
                    "growth_areas": ["Добавить конкретные примеры."],
                    "evidence": [],
                    "uncertainties": ["В тесте нет ответов кандидата."],
                    "disclaimer": "Учебная обратная связь для практики.",
                }
            ),
        )

        assert result["ok"] is True
        assert result["interview_complete"] is True
        assert "# Отчёт HR-интервью" in result["report_markdown"]
        assert Path(result["report_files"]["markdown_path"]).exists()
        assert Path(result["report_files"]["pdf_path"]).exists()
        assert result["report_files"]["markdown_path"].endswith(".md")
        assert result["report_files"]["pdf_path"].endswith(".pdf")
    finally:
        await bridge.delete_interview(session_id)


@pytest.mark.asyncio
async def test_report_export_failure_preserves_visible_validated_markdown(monkeypatch):
    bridge, _ = _voice_modules()
    export_module = importlib.import_module("mock_interviewer.report_export")

    def fail_export(*_args, **_kwargs):
        raise export_module.ReportExportError("write permission denied")

    monkeypatch.setattr(bridge, "export_report_files", fail_export)
    started = await bridge.start_interview(
        resume_text="Student backend project",
        first_turn_json=json.dumps(next_question("motivation", "Why backend?")),
    )
    session_id = started["session_id"]

    try:
        result = await bridge.finish_interview(
            session_id=session_id,
            report_json=json.dumps(
                {
                    "recommendation": "insufficient_data",
                    "scores": {},
                    "strengths": [],
                    "growth_areas": [],
                    "evidence": [],
                    "uncertainties": ["В тесте нет ответов кандидата."],
                    "disclaimer": "Учебная обратная связь для практики.",
                }
            ),
        )

        assert result["ok"] is True
        assert result["interview_complete"] is True
        assert "# Отчёт HR-интервью" in result["report_markdown"]
        assert result["report_files"] is None
        assert "write permission denied" in result["report_export_error"]
    finally:
        await bridge.delete_interview(session_id)
