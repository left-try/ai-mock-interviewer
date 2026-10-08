"""Contracts for the resume-free, explicitly disclosed voice test workflow."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question


class FastModel:
    def __init__(self, response):
        self.response = response

    async def create_structured_response(self, **_kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(value=self.response)


def _bridge():
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return importlib.import_module("interview_mcp")


def test_test_start_has_no_resume_or_attachment_arguments():
    bridge = _bridge()
    start = getattr(bridge, "start_test_interview", None)

    assert callable(start), "MCP must expose a separate start_test_interview entry point"
    parameters = set(inspect.signature(start).parameters)
    assert "resume_text" not in parameters
    assert "resume_path" not in parameters
    assert "attachment" not in parameters
    assert "first_turn_json" not in parameters


@pytest.mark.asyncio
async def test_invalid_fast_turn_keeps_answer_retryable():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    service = service_type(model=FakeModel([next_question("motivation", "Почему backend?")]),
                           resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный проект")
    await service.persist_answer(session.id, "Сохранённый ответ", event_id="invalid-fast")
    service.fast_model = FastModel({"kind": "question", "topic_id": "unknown", "text": "Что дальше?",
                                    "confidence": 0.8, "candidate_facts": [], "covered_topics": [], "open_threads": []})

    with pytest.raises(importlib.import_module("mock_interviewer.errors").InvalidModelOutput):
        await service.generate_next_turn(session.id, "invalid-fast")

    current = await service.get_session(session.id)
    assert current.pending_answer_event_id == "invalid-fast"
    assert current.turns[-1].text == "Сохранённый ответ"


@pytest.mark.asyncio
async def test_fast_turn_cannot_repeat_previous_question():
    service_type = importlib.import_module("mock_interviewer.service").InterviewService
    service = service_type(model=FakeModel([next_question("motivation", "Почему backend?")]),
                           resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный проект")
    await service.persist_answer(session.id, "Ответ", event_id="repeat-fast")
    service.fast_model = FastModel({"kind": "question", "topic_id": "motivation", "text": "Почему backend?",
                                    "confidence": 0.8, "candidate_facts": [], "covered_topics": [], "open_threads": []})

    with pytest.raises(importlib.import_module("mock_interviewer.errors").InvalidModelOutput, match="repeated"):
        await service.generate_next_turn(session.id, "repeat-fast")

    current = await service.get_session(session.id)
    assert current.pending_answer_event_id == "repeat-fast"


def test_test_interview_opening_keeps_synthetic_disclosure():
    opening = _bridge()._opening_script("Почему backend?", test_mode=True)

    assert "синтетическое собеседование" in opening.lower()
    assert "локальном диагностическом журнале" in opening.lower()
    assert "Почему backend?" in opening


@pytest.mark.asyncio
async def test_test_start_marks_run_and_discloses_local_answer_logging_before_question(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    start = getattr(bridge, "start_test_interview", None)
    assert callable(start), "MCP must expose a separate start_test_interview entry point"

    async def routes(*, timing_recorder=None):
        return FastModel({}), FastModel({})
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    result = await start()

    assert result["ok"] is True
    assert result["test_mode"] is True
    assert result["run_id"]
    assert result["turns"][0]["role"] == "interviewer"
    assert "локальн" in result["opening_script"].lower()
    assert "ответ" in result["instruction"].lower()


@pytest.mark.asyncio
async def test_test_start_does_not_call_resume_parser(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    start = getattr(bridge, "start_test_interview", None)
    assert callable(start), "MCP must expose a separate start_test_interview entry point"
    parser_calls = []

    class ParserThatMustNotRun:
        async def extract_text(self, *_args, **_kwargs):
            parser_calls.append(True)
            raise AssertionError("resume parser was called in resume-free test mode")

    original_service = bridge.InterviewService

    class ServiceWithTripwireParser(original_service):
        def __init__(self, *, model, resume_parser=None, **kwargs):
            super().__init__(model=model, resume_parser=ParserThatMustNotRun(), **kwargs)

    monkeypatch.setattr(bridge, "InterviewService", ServiceWithTripwireParser)
    async def routes(*, timing_recorder=None):
        return FastModel({}), FastModel({})
    monkeypatch.setattr(bridge, "_configured_routes", routes)
    result = await start()

    assert result["ok"] is True
    assert parser_calls == []


@pytest.mark.asyncio
async def test_test_start_and_answer_use_server_owned_question_route(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    next_question = FastModel({
        "kind": "question", "topic_id": "project", "text": "Что вы сделали в проекте?",
        "confidence": 0.8, "candidate_facts": [], "covered_topics": ["motivation"], "open_threads": [],
    })

    async def configured_routes(*, timing_recorder=None):
        return next_question, FastModel({
            "scores": {}, "evidence": [], "strengths": [], "growth_areas": [], "uncertainties": [],
        })

    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    started = await bridge.start_test_interview()
    assert started["ok"] and started["test_mode"] is True
    assert "синтетическое собеседование" in started["opening_script"].lower()
    assert started["turns"][-1]["text"] == bridge.INITIAL_INTERVIEW_QUESTION
    assert "first_turn_json" not in inspect.signature(bridge.start_test_interview).parameters

    answer = await bridge.record_candidate_answer(started["session_id"], "answer-1", "Я сделал API магазина.")
    assert answer["ok"] is True
    assert answer["candidate_turn"]["text"] == "Я сделал API магазина."
    assert answer["next_turn"]["text"] == "Что вы сделали в проекте?"
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_test_run_report_is_assembled_locally_and_logged(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path / "runs", raising=False)
    monkeypatch.setattr(bridge, "REPORTS_DIR", tmp_path / "reports", raising=False)

    async def configured_routes(*, timing_recorder=None):
        return FastModel({
            "kind": "question", "topic_id": "project", "text": "Что вы сделали?", "confidence": 0.8,
            "candidate_facts": [], "covered_topics": ["motivation"], "open_threads": [],
        }), FastModel({
            "scores": {"communication": 4}, "evidence": [], "strengths": ["Назвал личный вклад"],
            "growth_areas": [], "uncertainties": [],
        })

    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    started = await bridge.start_test_interview()
    saved = await bridge.record_candidate_answer(started["session_id"], "answer-1", "Я сделал API.")
    assert saved["ok"] is True

    result = await bridge.finish_interview(started["session_id"])
    assert result["ok"] is True
    assert result["interview_complete"] is True
    assert Path(result["report_files"]["pdf_path"]).exists()
    assert Path(result["test_run_log_path"]).exists()
    assert "report_json" not in inspect.signature(bridge.finish_interview).parameters
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_duplicate_answer_event_is_saved_and_logged_once(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)

    async def configured_routes(*, timing_recorder=None):
        return FastModel({
            "kind": "question", "topic_id": "project", "text": "Что вы сделали?", "confidence": 0.8,
            "candidate_facts": [], "covered_topics": [], "open_threads": [],
        }), FastModel({"scores": {}, "evidence": [], "strengths": [], "growth_areas": [], "uncertainties": []})

    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    started = await bridge.start_test_interview()
    args = (started["session_id"], "same-event", "Написал API магазина.")
    first = await bridge.record_candidate_answer(*args)
    replay = await bridge.record_candidate_answer(*args)
    assert first["ok"] and replay["ok"]
    assert replay["duplicate"] is True
    status = await bridge.interview_status(started["session_id"])
    assert sum(turn["role"] == "candidate" for turn in status["turns"]) == 1
    rows = [json.loads(line) for line in Path(started["log_path"]).read_text(encoding="utf-8").splitlines()]
    assert sum(row["event_name"] == "answer_saved" for row in rows) == 1
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_test_log_write_failure_does_not_discard_saved_answer(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)

    async def configured_routes(*, timing_recorder=None):
        return FastModel({
            "kind": "question", "topic_id": "project", "text": "Что вы сделали?", "confidence": 0.8,
            "candidate_facts": [], "covered_topics": [], "open_threads": [],
        }), FastModel({"scores": {}, "evidence": [], "strengths": [], "growth_areas": [], "uncertainties": []})

    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    started = await bridge.start_test_interview()
    _, logger = bridge._test_runs[started["session_id"]]

    def fail_log(*_args, **_kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(logger, "append_event", fail_log)
    result = await bridge.record_candidate_answer(started["session_id"], "answer-log-error", "Мой ответ сохранён.")
    session = await bridge.interview_status(started["session_id"])
    assert result["ok"] is True
    assert session["turns"][-2]["text"] == "Мой ответ сохранён."
    await bridge.delete_interview(started["session_id"])


@pytest.mark.asyncio
async def test_concurrent_test_runs_keep_logs_separate(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)

    async def configured_routes(*, timing_recorder=None):
        return FastModel({
            "kind": "question", "topic_id": "project", "text": "Что вы сделали?", "confidence": 0.8,
            "candidate_facts": [], "covered_topics": ["motivation"], "open_threads": [],
        }), FastModel({"scores": {}, "evidence": [], "strengths": [], "growth_areas": [], "uncertainties": []})

    monkeypatch.setattr(bridge, "_configured_routes", configured_routes)
    first, second = await bridge.start_test_interview(), await bridge.start_test_interview()
    assert first["run_id"] != second["run_id"]
    await bridge.record_candidate_answer(first["session_id"], "a1", "Ответ один")
    await bridge.record_candidate_answer(second["session_id"], "a2", "Ответ два")
    first_log = Path(first["log_path"]).read_text(encoding="utf-8")
    second_log = Path(second["log_path"]).read_text(encoding="utf-8")
    assert "Ответ один" in first_log and "Ответ два" not in first_log
    assert "Ответ два" in second_log and "Ответ один" not in second_log
    await bridge.delete_interview(first["session_id"])
    await bridge.delete_interview(second["session_id"])


def test_clear_tool_removes_only_test_run_logs(tmp_path, monkeypatch):
    bridge = _bridge()
    logs_dir = tmp_path / "test-runs"
    logs_dir.mkdir()
    (logs_dir / "run.jsonl").write_text("{}\n", encoding="utf-8")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / "report.pdf"
    report.write_bytes(b"report")
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", logs_dir, raising=False)

    result = bridge.clear_test_run_logs()

    assert result == {"ok": True, "deleted_logs": 1}
    assert not list(logs_dir.glob("*.jsonl"))
    assert report.exists()


def test_ordinary_resume_interview_is_not_marked_as_test_mode():
    bridge = _bridge()
    assert "синтетическое собеседование" not in bridge._opening_script("Почему backend?", test_mode=False).lower()
