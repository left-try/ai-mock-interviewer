"""Public service contracts.

These tests intentionally target the public application boundary. The MVP
implementation is not present yet, so they are expected to fail with an
actionable missing-module error until the corresponding production code exists.
"""

from __future__ import annotations

import importlib

import pytest

from tests.fakes import FakeModel, FakeResumeParser, next_question, report


def app_module(name: str):
    try:
        return importlib.import_module(f"mock_interviewer.{name}")
    except ModuleNotFoundError as exc:
        if exc.name == "mock_interviewer" or (exc.name and exc.name.startswith("mock_interviewer.")):
            pytest.fail(
                "MVP implementation is not present yet. Implement the public contract "
                f"mock_interviewer.{name} before expecting this test to pass.",
                pytrace=False,
            )
        raise


def service_for(*responses):
    service_type = app_module("service").InterviewService
    return service_type(
        model=FakeModel(list(responses)),
        resume_parser=FakeResumeParser(),
    )


@pytest.mark.asyncio
async def test_start_requires_resume_and_returns_first_interviewer_turn():
    service = service_for(next_question("motivation", "Расскажите, почему вас заинтересовал backend?"))
    session = await service.start(resume_text="Я изучаю Python и сделал учебный API.")

    assert session.id
    assert session.status == "active"
    assert session.turns[-1].role == "interviewer"
    assert session.turns[-1].text
    assert session.turns[-1].topic_id == "motivation"


@pytest.mark.asyncio
async def test_start_rejects_blank_resume_text():
    service = service_for()
    with pytest.raises(ValueError, match="resume|резюме|blank|empty"):
        await service.start(resume_text=" \n\t ")


@pytest.mark.asyncio
async def test_start_rejects_oversized_resume_text():
    service = service_for()
    with pytest.raises(ValueError, match="size|length|limit|размер|длин"):
        await service.start(resume_text="x" * (service.MAX_RESUME_CHARS + 1))


@pytest.mark.asyncio
async def test_submit_answer_advances_session_and_records_candidate_turn():
    service = service_for(
        next_question("motivation", "Почему именно backend?"),
        next_question("project", "Какую часть проекта вы реализовали лично?", kind="follow_up"),
    )
    session = await service.start(resume_text="Учебный проект: API на Python")
    updated = await service.submit_answer(session.id, "Я реализовал маршруты и подключил базу данных.")

    assert updated.status == "active"
    assert updated.turns[-2].role == "candidate"
    assert updated.turns[-2].text == "Я реализовал маршруты и подключил базу данных."
    assert updated.turns[-1].role == "interviewer"
    assert updated.turns[-1].topic_id == "project"


@pytest.mark.asyncio
async def test_submit_answer_rejects_blank_answer_without_consuming_a_model_turn():
    model = FakeModel([next_question("motivation", "Почему backend?")])
    service_type = app_module("service").InterviewService
    service = service_type(model=model, resume_parser=FakeResumeParser())
    session = await service.start(resume_text="Учебный API")
    calls_before = len(model.calls)

    with pytest.raises(ValueError, match="answer|blank|empty|ответ|пуст"):
        await service.submit_answer(session.id, " \t\n ")
    assert len(model.calls) == calls_before


@pytest.mark.asyncio
async def test_unknown_session_is_a_controlled_not_found_error():
    service = service_for()
    with pytest.raises(app_module("errors").SessionNotFound):
        await service.submit_answer("missing-session", "Ответ")


@pytest.mark.asyncio
async def test_finish_returns_report_and_marks_session_completed():
    evidence = [
        {
            "criterion": "personal_contribution",
            "source_turn_id": "turn-2",
            "quote": "Я реализовал маршруты и подключил базу данных.",
            "observation": "Назвал конкретный личный вклад.",
        }
    ]
    service = service_for(
        next_question("motivation", "Почему backend?"),
        next_question("personal_contribution", "Какую часть проекта вы реализовали лично?"),
        report(evidence=evidence),
    )
    session = await service.start(resume_text="Учебный API")
    session = await service.submit_answer(
        session.id,
        "Я реализовал маршруты и подключил базу данных.",
    )
    finished = await service.finish(session.id)

    assert finished.status == "completed"
    assert finished.report is not None
    assert finished.report.disclaimer
    assert finished.report.evidence[0]["source_turn_id"] == "turn-2"
    assert finished.report.evidence[0]["quote"] == "Я реализовал маршруты и подключил базу данных."


@pytest.mark.asyncio
async def test_cancelled_session_cannot_be_submitted_or_reported_as_completed():
    service = service_for(next_question("motivation", "Почему backend?"))
    session = await service.start(resume_text="Учебный API")
    cancelled = await service.cancel(session.id)

    assert cancelled.status == "cancelled"
    with pytest.raises(app_module("errors").InvalidSessionTransition):
        await service.submit_answer(session.id, "Ответ")


@pytest.mark.asyncio
async def test_completed_session_rejects_additional_answers():
    service = service_for(
        next_question("motivation", "Почему backend?"),
        report(),
    )
    session = await service.start(resume_text="Учебный API")
    await service.finish(session.id)

    with pytest.raises(app_module("errors").InvalidSessionTransition):
        await service.submit_answer(session.id, "Поздний ответ")


@pytest.mark.asyncio
async def test_resume_upload_delegates_bytes_and_filename_to_parser():
    module = app_module("service")
    parser = FakeResumeParser(text="Опыт: учебный backend-проект")
    service = module.InterviewService(
        model=FakeModel([next_question("motivation", "Почему backend?")]),
        resume_parser=parser,
    )
    session = await service.start_from_upload(b"%PDF-test", filename="resume.pdf")

    assert parser.calls == [b"%PDF-test"]
    assert session.resume_text == "Опыт: учебный backend-проект"


@pytest.mark.asyncio
async def test_resume_parser_error_does_not_start_session_or_call_model():
    model = FakeModel([])
    service_type = app_module("service").InterviewService
    service = service_type(
        model=model,
        resume_parser=FakeResumeParser(error=ValueError("corrupt document")),
    )

    with pytest.raises(app_module("errors").ResumeParseError):
        await service.start_from_upload(b"broken", filename="resume.pdf")
    assert model.calls == []


@pytest.mark.asyncio
async def test_model_failure_is_exposed_as_controlled_provider_error():
    service = service_for(TimeoutError("provider timeout"))
    with pytest.raises(app_module("errors").ModelProviderError):
        await service.start(resume_text="Учебный API")


@pytest.mark.asyncio
async def test_failed_model_call_does_not_leave_a_partially_active_session():
    service = service_for(TimeoutError("provider timeout"))
    with pytest.raises(app_module("errors").ModelProviderError):
        await service.start(resume_text="Учебный API")
    assert await service.list_sessions() == []


def test_voice_tools_do_not_accept_host_generated_model_outputs():
    import inspect
    import sys
    from pathlib import Path

    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    bridge = importlib.import_module("interview_mcp")
    assert set(inspect.signature(bridge.start_interview).parameters) == {"resume_text", "level"}
    assert set(inspect.signature(bridge.start_test_interview).parameters) == {"level"}
    assert set(inspect.signature(bridge.finish_interview).parameters) == {"session_id"}
    assert set(inspect.signature(bridge.record_candidate_answer).parameters) == {
        "session_id", "event_id", "transcript",
    }


def test_live_and_analysis_routes_are_distinct():
    import sys
    from pathlib import Path

    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    bridge = importlib.import_module("interview_mcp")
    assert bridge._plan_client(model_role="fast") is not bridge._plan_client(model_role="analysis")
