import pytest

from mock_interviewer.errors import InvalidReport, InvalidSessionTransition
from mock_interviewer.technical_interview import TechnicalInterviewService


def question(topic_id, text="Расскажите, как это работает?", phase="calibration"):
    return {"kind": "question", "phase": phase, "topic_id": topic_id, "text": text, "evidence": []}


async def calibrated_session(service):
    session = await service.start(first_turn=question("programming_fundamentals"))
    topics = ["data_structures", "runtime_basics", "concurrency_basics"]
    for index in range(4):
        session = await service.persist_answer(session.id, f"answer {index}", event_id=f"cal-{index}")
        if index < 3:
            session = await service.propose_next_turn(
                session.id,
                event_id=f"cal-{index}",
                proposal=question(topics[index]),
            )
    return session


def report_for(session, *, evidence=True):
    candidate = next(turn for turn in session.turns if turn.role == "candidate")
    cited = [{"turn_id": candidate.id, "quote": candidate.text[:20], "criterion_id": "technical_fundamentals"}] if evidence else []
    return {
        "estimated_level": "junior" if evidence else None,
        "confidence": "medium" if evidence else "low",
        "scores": {
            "technical_fundamentals": 3 if evidence else None,
            "runtime_and_execution": None,
            "concurrency_and_memory": None,
            "stack_internals": None,
            "practical_reasoning": None,
        },
        "strengths": [],
        "growth_areas": [],
        "evidence": cited,
        "uncertainties": [] if evidence else ["Недостаточно ответов для оценки уровня."],
        "disclaimer": "Учебная обратная связь, не решение о найме.",
    }


@pytest.mark.asyncio
async def test_start_rejects_invalid_calibration_topic():
    service = TechnicalInterviewService()
    with pytest.raises(ValueError, match="topic"):
        await service.start(first_turn=question("architecture"))


@pytest.mark.asyncio
async def test_start_creates_first_calibration_question():
    service = TechnicalInterviewService()
    session = await service.start(
        first_turn=question("programming_fundamentals", "Что происходит при вызове функции?")
    )
    assert session.phase == "calibration"
    assert session.status == "active"
    assert session.estimated_level is None
    assert session.turns[0].text == "Что происходит при вызове функции?"


@pytest.mark.asyncio
async def test_answer_is_saved_before_proposal_and_retry_is_idempotent():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    saved = await service.persist_answer(session.id, "Функция выполняет инструкции.", event_id="evt-1")
    assert saved.turns[-1].role == "candidate"
    assert saved.pending_answer_event_id == "evt-1"

    with pytest.raises(ValueError, match="topic"):
        await service.propose_next_turn(
            session.id,
            event_id="evt-1",
            proposal={"kind": "question", "phase": "calibration", "topic_id": "architecture", "text": "Почему?"},
        )
    retried = await service.propose_next_turn(
        session.id,
        event_id="evt-1",
        proposal=question("data_structures"),
    )
    assert retried.turns[-1].role == "interviewer"
    duplicate = await service.persist_answer(session.id, "Функция выполняет инструкции.", event_id="evt-1")
    assert len([turn for turn in duplicate.turns if turn.role == "candidate"]) == 1
    with pytest.raises(InvalidSessionTransition, match="different"):
        await service.persist_answer(session.id, "Другой ответ", event_id="evt-1")


@pytest.mark.asyncio
async def test_four_calibration_answers_transition_to_stack_selection():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    topics = ["data_structures", "runtime_basics", "concurrency_basics"]
    for index in range(4):
        saved = await service.persist_answer(session.id, f"answer {index}", event_id=f"evt-{index}")
        if index < 3:
            session = await service.propose_next_turn(
                session.id,
                event_id=f"evt-{index}",
                proposal=question(topics[index]),
            )
        else:
            session = saved
    assert session.phase == "stack_selection"
    assert session.pending_answer_event_id is None
    assert len([turn for turn in session.turns if turn.role == "candidate"]) == 4


@pytest.mark.asyncio
async def test_stack_selection_accepts_arbitrary_name_and_starts_deep_dive():
    service = TechnicalInterviewService()
    session = await calibrated_session(service)
    selected = await service.select_stack(session.id, "Elixir / Phoenix + PostgreSQL", event_id="stack-1")
    assert selected.stack == "Elixir / Phoenix + PostgreSQL"
    assert selected.phase == "deep_dive"
    assert selected.pending_answer_event_id == "stack-1"
    selected = await service.propose_next_turn(
        session.id,
        event_id="stack-1",
        proposal=question("runtime_and_memory", phase="deep_dive"),
    )
    assert selected.turns[-1].role == "interviewer"
    with pytest.raises(InvalidSessionTransition):
        await service.select_stack(session.id, "", event_id="stack-2")


@pytest.mark.asyncio
async def test_six_deep_dive_answers_move_to_awaiting_report():
    service = TechnicalInterviewService()
    session = await calibrated_session(service)
    session = await service.select_stack(session.id, "Python + FastAPI", event_id="stack-1")
    topics = ["runtime_and_memory", "concurrency_and_io", "framework_lifecycle", "data_and_errors", "performance_and_tradeoffs"]
    session = await service.propose_next_turn(
        session.id,
        event_id="stack-1",
        proposal=question(topics[0], phase="deep_dive"),
    )
    for index in range(6):
        event_id = f"deep-{index}"
        session = await service.persist_answer(session.id, f"deep answer {index}", event_id=event_id)
        if index < 5:
            session = await service.propose_next_turn(
                session.id,
                event_id=event_id,
                proposal=question(topics[index], phase="deep_dive"),
            )
    assert session.status == "awaiting_report"
    assert session.phase == "wrap_up"


@pytest.mark.asyncio
async def test_report_accepts_supported_level_and_renders_evidence():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    for index, topic in enumerate(("data_structures", "runtime_basics")):
        event_id = f"cal-{index}"
        session = await service.persist_answer(session.id, f"answer {index}", event_id=event_id)
        session = await service.propose_next_turn(
            session.id,
            event_id=event_id,
            proposal=question(topic),
        )
    session = await service.persist_answer(
        session.id,
        "answer 2",
        event_id="cal-2",
        finish_after_answer=True,
    )
    completed = await service.finish(session.id, report=report_for(session))
    markdown = service.render_report_markdown(completed)
    assert "Estimated level:** junior" in markdown
    assert "answer 0" in markdown


@pytest.mark.asyncio
async def test_early_finish_without_evidence_accepts_only_insufficient_report():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    session = await service.persist_answer(session.id, "Не знаю.", event_id="evt-1", finish_after_answer=True)
    with pytest.raises(InvalidReport):
        await service.finish(session.id, report=report_for(session))
    completed = await service.finish(session.id, report=report_for(session, evidence=False))
    assert completed.status == "completed"
    assert completed.report["estimated_level"] is None
    assert completed.report["scores"]["technical_fundamentals"] is None


@pytest.mark.asyncio
async def test_report_rejects_fabricated_quote():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    session = await service.persist_answer(session.id, "The event loop schedules tasks.", event_id="evt-1", finish_after_answer=True)
    proposal = report_for(session)
    proposal["evidence"][0]["quote"] = "A fabricated quote"
    with pytest.raises(InvalidReport, match="quote"):
        await service.finish(session.id, report=proposal)


@pytest.mark.asyncio
async def test_cancel_and_delete_clear_session():
    service = TechnicalInterviewService()
    session = await service.start(first_turn=question("programming_fundamentals"))
    cancelled = await service.cancel(session.id)
    assert cancelled.status == "cancelled"
    await service.delete(session.id)
    with pytest.raises(Exception):
        await service.get_session(session.id)


