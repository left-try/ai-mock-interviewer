"""Contracts for the resume-free, explicitly disclosed voice test workflow."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest


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
    assert "first_turn_json" in parameters


@pytest.mark.asyncio
async def test_test_start_marks_run_and_discloses_local_answer_logging_before_question(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    start = getattr(bridge, "start_test_interview", None)
    assert callable(start), "MCP must expose a separate start_test_interview entry point"

    result = await start(first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}')

    assert result["ok"] is True
    assert result["test_mode"] is True
    assert result["run_id"]
    assert result["turns"][0]["role"] == "interviewer"
    assert "локальн" in result["instruction"].lower()
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
    result = await start(first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}')

    assert result["ok"] is True
    assert parser_calls == []


@pytest.mark.asyncio
async def test_synthetic_profile_is_not_accepted_as_report_evidence(tmp_path, monkeypatch):
    from mock_interviewer.errors import InvalidReport

    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    started = await bridge.start_test_interview(
        first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}'
    )
    service, _ = bridge._sessions[started["session_id"]]
    session = await service.get_session(started["session_id"])
    profile_quote = "Делал учебный REST API на Go с PostgreSQL."

    assert session.test_mode is True
    with pytest.raises(InvalidReport, match="Unknown evidence source"):
        service._validate_report(
            {
                "recommendation": "mixed_signal",
                "scores": {},
                "strengths": [],
                "growth_areas": [],
                "evidence": [{
                    "criterion": "motivation",
                    "source_turn_id": "resume",
                    "quote": profile_quote,
                    "observation": "This detail came only from the fictional profile.",
                }],
                "uncertainties": [],
                "disclaimer": "Учебная обратная связь для практики.",
            },
            session,
        )


@pytest.mark.asyncio
async def test_answer_recording_saves_once_before_proposal_and_logs_lifecycle(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    start = getattr(bridge, "start_test_interview", None)
    propose = getattr(bridge, "propose_next_turn", None)
    assert callable(start), "MCP must expose a separate start_test_interview entry point"
    assert callable(propose), "MCP must expose a separate propose_next_turn tool"
    started = await start(first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}')
    session_id = started["session_id"]

    answer = await bridge.save_candidate_answer(
        session_id=session_id,
        transcript="Хочу развиваться в backend.",
        event_id="answer-1",
        include_transcript=True,
    )
    assert answer["ok"] is True
    assert answer["next_action"] == "propose_next_turn"
    assert answer["turns"][-1]["role"] == "candidate"
    assert sum(turn["role"] == "candidate" for turn in answer["turns"]) == 1

    question = await propose(
        session_id=session_id,
        answer_event_id="answer-1",
        next_turn_json='{"kind":"question","topic_id":"education","text":"Что вы изучали?"}',
    )
    assert question["ok"] is True
    assert question["next_turn"]["text"] == "Что вы изучали?"

    event_rows = [line for file in tmp_path.glob("*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
    import json
    events = [json.loads(row) for row in event_rows]
    names = [event["event_name"] for event in events]
    assert names.count("answer_saved") == 1
    assert names.index("answer_saved") < names.index("question_proposal_accepted")


@pytest.mark.asyncio
async def test_duplicate_answer_event_does_not_duplicate_test_log_event(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    start = getattr(bridge, "start_test_interview", None)
    assert callable(start), "MCP must expose a separate start_test_interview entry point"
    started = await start(first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}')
    args = {
        "session_id": started["session_id"], "transcript": "Ответ один раз",
        "event_id": "answer-1", "include_transcript": True,
    }
    await bridge.save_candidate_answer(**args)
    duplicate = await bridge.save_candidate_answer(**args)

    assert duplicate["duplicate"] is True
    assert sum(turn["role"] == "candidate" for turn in duplicate["turns"]) == 1
    rows = [line for file in tmp_path.glob("*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
    import json
    events = [json.loads(row) for row in rows]
    assert sum(event["event_name"] == "answer_saved" for event in events) == 1


@pytest.mark.asyncio
async def test_early_stop_produces_report_files_and_complete_diagnostic_log(tmp_path, monkeypatch):
    bridge = _bridge()
    logs_dir = tmp_path / "test-runs"
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", logs_dir, raising=False)
    monkeypatch.setattr(bridge, "REPORTS_DIR", reports_dir, raising=False)
    started = await bridge.start_test_interview(
        first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}'
    )
    session_id = started["session_id"]
    saved = await bridge.save_candidate_answer(
        session_id=session_id,
        transcript="Хочу научиться проектировать API.",
        event_id="answer-early-stop",
    )
    assert saved["ok"] is True

    result = await bridge.finish_interview(
        session_id=session_id,
        report_json=(
            '{"recommendation":"insufficient_data","scores":{},"strengths":[],'
            '"growth_areas":[],"evidence":[],"uncertainties":["Интервью остановлено рано."],'
            '"disclaimer":"Учебная обратная связь для практики."}'
        ),
    )

    assert result["ok"] is True
    assert result["interview_complete"] is True
    assert "Недостаточно данных" in result["report_markdown"]
    assert Path(result["report_files"]["markdown_path"]).exists()
    assert Path(result["report_files"]["pdf_path"]).exists()
    assert Path(result["test_run_log_path"]).exists()
    assert result["test_run_summary"]["event_counts"]["run_ended"] == 1
    assert result["test_run_summary"]["metrics"]["mcp_tool_call_duration_ms"]["count"] >= 2
    events = [
        json.loads(line)
        for line in Path(result["test_run_log_path"]).read_text(encoding="utf-8").splitlines()
    ]
    names = [event["event_name"] for event in events]
    for required in (
        "run_started", "answer_saved", "report_validation", "markdown_render",
        "pdf_export", "run_ended",
    ):
        assert names.count(required) == 1
    assert names.index("answer_saved") < names.index("report_validation") < names.index("run_ended")
    assert "client_gap" in names


@pytest.mark.asyncio
async def test_test_log_write_failure_does_not_discard_a_saved_answer(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    started = await bridge.start_test_interview(
        first_turn_json='{"kind":"question","topic_id":"motivation","text":"Почему backend?"}'
    )
    session_id = started["session_id"]
    _, logger = bridge._test_runs[session_id]

    def fail_append(*_args, **_kwargs):
        raise OSError("disk is read-only")

    monkeypatch.setattr(logger, "append_event", fail_append)
    result = await bridge.save_candidate_answer(
        session_id=session_id,
        transcript="Этот ответ должен сохраниться.",
        event_id="answer-log-error",
        include_transcript=True,
    )

    assert result["ok"] is True
    assert result["candidate_turn"]["text"] == "Этот ответ должен сохраниться."
    assert "diagnostic_warning" in result
    status = await bridge.interview_status(session_id=session_id)
    assert status["turns"][-1]["text"] == "Этот ответ должен сохраниться."


@pytest.mark.asyncio
async def test_concurrent_test_runs_keep_answers_in_separate_logs(tmp_path, monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", tmp_path, raising=False)
    opening = '{"kind":"question","topic_id":"motivation","text":"Почему backend?"}'
    first = await bridge.start_test_interview(first_turn_json=opening)
    second = await bridge.start_test_interview(first_turn_json=opening)

    await bridge.save_candidate_answer(
        session_id=first["session_id"], transcript="Ответ первого прогона", event_id="answer-1"
    )
    await bridge.save_candidate_answer(
        session_id=second["session_id"], transcript="Ответ второго прогона", event_id="answer-2"
    )

    first_events = [json.loads(row) for row in Path(first["log_path"]).read_text(encoding="utf-8").splitlines()]
    second_events = [json.loads(row) for row in Path(second["log_path"]).read_text(encoding="utf-8").splitlines()]
    first_answers = [event["details"]["answer_text"] for event in first_events if event["event_name"] == "answer_saved"]
    second_answers = [event["details"]["answer_text"] for event in second_events if event["event_name"] == "answer_saved"]

    assert first_answers == ["Ответ первого прогона"]
    assert second_answers == ["Ответ второго прогона"]


def test_clear_tool_removes_only_test_run_logs(tmp_path, monkeypatch):
    bridge = _bridge()
    logs = tmp_path / "test-runs"
    reports = tmp_path / "reports"
    logs.mkdir()
    reports.mkdir()
    log_file = logs / "run.jsonl"
    report_file = reports / "report.pdf"
    log_file.write_text("{}\n", encoding="utf-8")
    report_file.write_bytes(b"report")
    monkeypatch.setattr(bridge, "TEST_RUNS_DIR", logs, raising=False)

    result = bridge.clear_test_run_logs()

    assert result == {"ok": True, "deleted_logs": 1}
    assert not log_file.exists()
    assert report_file.exists()


def test_ordinary_resume_interview_is_not_marked_as_test_mode():
    bridge = _bridge()
    assert "test_mode" not in inspect.signature(bridge.start_interview).parameters
