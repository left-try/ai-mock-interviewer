"""Regression contracts for the host-driven voice interview loop."""

from __future__ import annotations

import importlib
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question
from mock_interviewer.background_evaluation import BackgroundEvaluationQueue


class FastTurnFake:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.timing_recorder = None

    async def create_structured_response(self, **kwargs):
        self.calls.append(kwargs)
        if self.timing_recorder:
            self.timing_recorder("fast_model_ttft", duration_ms=3.0, model_role="fast", model="fake", reasoning_effort="low")
            self.timing_recorder("fast_model_completion", duration_ms=8.0, model_role="fast", model="fake", reasoning_effort="low")
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(value=value, usage={})


def _voice_modules():
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return importlib.import_module("interview_mcp"), importlib.import_module("voice_probe")


@pytest.mark.asyncio
async def test_start_uses_subscription_fast_model():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    fast_model = FastTurnFake([{
        "kind": "question", "topic_id": "motivation", "text": "Почему backend?", "confidence": 0.8,
        "candidate_facts": [], "covered_topics": [], "open_threads": [],
    }])
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model)

    session = await service.start(resume_text="Я изучаю Python.")

    assert session.turns[-1].text == "Почему backend?"
    assert len(fast_model.calls) == 1


@pytest.mark.asyncio
async def test_selected_level_changes_live_interviewer_expectations():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    fast_model = FastTurnFake([{
        "kind": "question", "topic_id": "project", "text": "Как вы принимали решения?", "confidence": 0.8,
        "candidate_facts": [], "covered_topics": [], "open_threads": [],
    }])
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model)

    session = await service.start(resume_text="Опыт backend", level="middle")

    assert session.level == "middle"
    prompt = fast_model.calls[0]["input"][0]["content"]
    assert "Middle" in prompt
    assert "independent delivery" in prompt


@pytest.mark.asyncio
async def test_start_interview_uses_selected_screening_level_for_session_and_opening():
    bridge, _ = _voice_modules()
    async def configured_routes(*, timing_recorder=None):
        return FastTurnFake([]), FastTurnFake([])
    old_routes = bridge._configured_routes
    bridge._configured_routes = configured_routes
    try:
        started = await bridge.start_interview(
            resume_text="Работаю backend-разработчиком три года.", level="middle",
        )
        assert started["ok"] is True
        assert started["level"] == "middle"
        assert "Middle" in started["opening_script"]
        assert "screening" in started["opening_script"].lower()
        await bridge.delete_interview(started["session_id"])
    finally:
        bridge._configured_routes = old_routes


@pytest.mark.asyncio
async def test_start_interview_rejects_unknown_screening_level():
    bridge, _ = _voice_modules()

    started = await bridge.start_interview(resume_text="Опыт backend", level="senior")

    assert started == {"ok": False, "error": "Unknown interview level: senior"}


@pytest.mark.asyncio
async def test_fast_turn_uses_compact_state_without_rewriting_transcript():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    fast_model = FastTurnFake([
        {"kind": "question", "topic_id": "motivation", "text": "Почему backend?", "confidence": 0.8,
         "candidate_facts": [], "covered_topics": [], "open_threads": []},
        {"kind": "question", "topic_id": "project", "text": "Что вы реализовали лично?", "confidence": 0.7,
         "candidate_facts": ["Изучает FastAPI"], "covered_topics": ["motivation"], "open_threads": ["Опыт проекта"]},
    ])
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model)
    session = await service.start(resume_text="Backend практика")
    exact_answer = "Ну, эээ, я делал API для магазина."
    await service.persist_answer(session.id, exact_answer, event_id="answer-compact")
    updated = await service.generate_next_turn(session.id, "answer-compact")

    assert updated.session.turns[-2].text == exact_answer
    assert updated.session.turns[-1].text == "Что вы реализовали лично?"
    assert updated.state.candidate_facts == ["Изучает FastAPI"]
    payload = json.dumps(fast_model.calls[-1]["input"], ensure_ascii=False)
    assert exact_answer in payload
    assert "полная история" not in payload.lower()


@pytest.mark.asyncio
async def test_fast_turn_returns_one_adaptive_question():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    fast_model = FastTurnFake([
        {"kind": "question", "topic_id": "motivation", "text": "Почему backend?", "confidence": 0.8,
         "candidate_facts": [], "covered_topics": [], "open_threads": []},
        {"kind": "follow_up", "topic_id": "project", "text": "Как вы проверили это изменение?", "confidence": 0.8,
         "candidate_facts": ["Проверял по логам"], "covered_topics": ["project"], "open_threads": []},
    ])
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model)
    session = await service.start(resume_text="Backend практика")
    await service.persist_answer(session.id, "Проверил по логам", event_id="answer-followup")

    result = await service.generate_next_turn(session.id, "answer-followup")

    assert result.next_turn.text == "Как вы проверили это изменение?"
    assert [turn.text for turn in result.session.turns if turn.role == "interviewer"] == [
        "Почему backend?", "Как вы проверили это изменение?",
    ]


@pytest.mark.asyncio
async def test_subscription_voice_tool_saves_answer_then_returns_one_question(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    fast_model = FastTurnFake([
        {"kind": "question", "topic_id": "project", "text": "Что вы сделали в проекте?", "confidence": 0.8,
         "candidate_facts": ["интересуется backend"], "covered_topics": ["motivation"], "open_threads": []},
    ])

    async def configured_routes(*, timing_recorder=None):
        fast_model.timing_recorder = timing_recorder
        return fast_model, fast_model
    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    started = await bridge.start_test_interview(level="internship")
    result = await bridge.record_candidate_answer(started["session_id"], "voice-event-1", "Мне интересен backend.")

    assert result["ok"] is True
    assert result["candidate_turn"]["text"] == "Мне интересен backend."
    assert result["next_turn"]["text"] == "Что вы сделали в проекте?"
    assert set(__import__("inspect").signature(bridge.record_candidate_answer).parameters) == {
        "session_id", "event_id", "transcript",
    }
    assert "acknowledgment" not in result
    events = [json.loads(row) for row in Path(started["log_path"]).read_text(encoding="utf-8").splitlines()]
    assert "fast_model_ttft" in [event["event_name"] for event in events]
    assert "turn_ready" in [event["event_name"] for event in events]
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_interview_start_returns_while_subscription_routes_warm_in_background(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    release_routes = asyncio.Event()

    async def slow_route_setup(*, timing_recorder=None):
        await release_routes.wait()
        return FastTurnFake([]), FastTurnFake([])

    monkeypatch.setattr(bridge, "_configured_routes", slow_route_setup)
    started = await asyncio.wait_for(bridge.start_test_interview(level="internship"), timeout=0.1)

    assert started["ok"] is True
    assert "продуктовая команда" in started["opening_script"].lower()
    assert started["turns"][-1]["text"] == bridge.INITIAL_INTERVIEW_QUESTION
    task = bridge._route_tasks[started["session_id"]]
    assert not task.done()
    release_routes.set()
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_finish_waits_for_pending_evaluations_and_marks_failures():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    fast_model = FastTurnFake([{
        "kind": "question", "topic_id": "motivation", "text": "Почему backend?", "confidence": 0.8,
        "candidate_facts": [], "covered_topics": [], "open_threads": [],
    }])
    evaluation_model = FastTurnFake([{
        "scores": {"communication": 4}, "strengths": ["Named personal work"], "growth_areas": [],
        "uncertainties": [], "evidence": [{"criterion": "communication", "source_turn_id": "turn-2",
                                               "quote": "I wrote code.", "observation": "Specific example."}],
    }])
    queue = BackgroundEvaluationQueue(model=evaluation_model, timeout=1)
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model,
                           background_evaluation_queue=queue)
    session = await service.start(resume_text="Student backend project")
    await service.persist_answer(session.id, "I wrote code.", event_id="report-answer", finish_after_answer=True)

    finished = await service.finish(session.id)

    assert finished.status == "completed"
    assert finished.report.evidence[0]["quote"] == "I wrote code."
    assert finished.report.scores == {"communication": None}
    assert not any("report model" in item.lower() for item in finished.report.uncertainties)


@pytest.mark.asyncio
async def test_finish_local_report_marks_failed_background_analysis():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService

    class FailedEvaluation:
        async def create_structured_response(self, **_kwargs):
            raise RuntimeError("analysis unavailable")

    fast_model = FastTurnFake([{
        "kind": "question", "topic_id": "motivation", "text": "Почему backend?", "confidence": 0.8,
        "candidate_facts": [], "covered_topics": [], "open_threads": [],
    }])
    queue = BackgroundEvaluationQueue(model=FailedEvaluation(), timeout=1)
    service = service_type(model=FakeModel([]), resume_parser=FakeResumeParser(), fast_model=fast_model,
                           background_evaluation_queue=queue)
    session = await service.start(resume_text="Student backend project")
    await service.persist_answer(session.id, "Answer", event_id="failed-report-answer", finish_after_answer=True)

    finished = await service.finish(session.id)

    assert finished.status == "completed"
    assert finished.report.recommendation == "insufficient_data"
    assert any("turn-2" in item for item in finished.report.uncertainties)


def test_voice_server_instructions_require_one_reaction_and_one_question():
    bridge, voice_probe = _voice_modules()
    instructions = voice_probe.mcp.instructions.lower()
    assert "specific listening reaction" in instructions
    assert "repeat" in instructions or "повтор" in instructions
    assert "record_candidate_answer" in instructions


@pytest.mark.asyncio
async def test_start_returns_local_company_intro_without_waiting_for_model(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    release = asyncio.Event()
    async def slow_routes(*, timing_recorder=None):
        await release.wait()
        return FastTurnFake([]), FastTurnFake([])
    monkeypatch.setattr(bridge, "_configured_routes", slow_routes)
    result = await asyncio.wait_for(bridge.start_test_interview(level="internship"), timeout=0.1)
    assert result["ok"] is True
    assert "онлайн-магазина" in result["opening_script"].lower()
    assert result["turns"][-1]["text"] == bridge.INITIAL_INTERVIEW_QUESTION
    release.set()
    await bridge.delete_interview(result["session_id"])


@pytest.mark.asyncio
async def test_mcp_answer_saves_transcript_then_returns_one_fast_question(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    fast = FastTurnFake([{"kind": "question", "topic_id": "project", "text": "Какую часть API вы сделали сами?",
                          "confidence": 0.8, "candidate_facts": [], "covered_topics": ["motivation"], "open_threads": []}])
    analysis = FastTurnFake([{"scores": {}, "evidence": [], "strengths": [], "growth_areas": [], "uncertainties": []}])
    async def routes(*, timing_recorder=None):
        fast.timing_recorder = timing_recorder
        return fast, analysis
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    started = await bridge.start_test_interview(level="internship")
    result = await bridge.record_candidate_answer(started["session_id"], "answer-1", "Я написал API.")
    assert result["ok"] is True
    assert result["candidate_turn"]["text"] == "Я написал API."
    assert result["next_turn"]["text"] == "Какую часть API вы сделали сами?"
    assert "acknowledgment" not in result
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_report_is_assembled_locally_and_exported_without_host_json(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "REPORTS_DIR", tmp_path, raising=False)
    async def routes(*, timing_recorder=None):
        return FastTurnFake([]), FastTurnFake([])
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    started = await bridge.start_interview(resume_text="Учебный проект на FastAPI", level="internship")
    result = await bridge.finish_interview(started["session_id"])
    assert result["ok"] is True
    assert result["interview_complete"] is True
    assert Path(result["report_files"]["pdf_path"]).exists()
    assert "# Отчёт Backend Screening — Internship" in result["report_markdown"]
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_report_export_remains_off_event_loop(tmp_path, monkeypatch):
    bridge, _ = _voice_modules()
    monkeypatch.setattr(bridge, "REPORTS_DIR", tmp_path, raising=False)
    original_export = bridge.export_report_files
    thread_ids, ticks = [], []
    def slow_export(*args, **kwargs):
        thread_ids.append(threading.get_ident())
        time.sleep(0.04)
        return original_export(*args, **kwargs)
    async def routes(*, timing_recorder=None):
        return FastTurnFake([]), FastTurnFake([])
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    monkeypatch.setattr(bridge, "export_report_files", slow_export)
    started = await bridge.start_interview(resume_text="Student backend project", level="internship")
    loop_thread = threading.get_ident()
    async def tick():
        await asyncio.sleep(0.005)
        ticks.append(True)
    result, _ = await asyncio.gather(bridge.finish_interview(started["session_id"]), tick())
    assert result["ok"] is True and ticks == [True]
    assert thread_ids and thread_ids[0] != loop_thread
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_report_export_failure_keeps_visible_markdown(monkeypatch):
    bridge, _ = _voice_modules()
    def fail_export(*_args, **_kwargs):
        raise bridge.ReportExportError("write permission denied")
    async def routes(*, timing_recorder=None):
        return FastTurnFake([]), FastTurnFake([])
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    monkeypatch.setattr(bridge, "export_report_files", fail_export)
    started = await bridge.start_interview(resume_text="Student backend project", level="internship")
    result = await bridge.finish_interview(started["session_id"])
    assert result["ok"] is True
    assert result["report_files"] is None
    assert "write permission denied" in result["report_export_error"]
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
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
