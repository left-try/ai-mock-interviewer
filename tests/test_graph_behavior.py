"""Deterministic behavioral tests for the LangGraph interview controller."""

from __future__ import annotations

import pytest

from tests.fakes import FakeModel, next_question, report
from tests.test_public_contracts import app_module


def build_service(*responses, limits=None):
    service_type = app_module("service").InterviewService
    from tests.fakes import FakeResumeParser

    return service_type(
        model=FakeModel(list(responses)),
        resume_parser=FakeResumeParser(),
        limits=limits,
    )


@pytest.mark.asyncio
async def test_specific_answer_can_route_to_a_resume_grounded_follow_up():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question(
            "project",
            "Вы упомянули Study API. Какую часть реализовали лично?",
            kind="follow_up",
        ),
    )
    session = await service.start(resume_text="Проект Study API на Python")
    session = await service.submit_answer(session.id, "В Study API я писал endpoints и persistence.")

    assert session.turns[-1].kind == "follow_up"
    assert session.turns[-1].topic_id == "project"
    assert "Study API" in session.turns[-1].text


@pytest.mark.asyncio
async def test_generic_answer_causes_bounded_clarification_then_progress():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question("motivation", "Что именно привлекает вас в этой работе?", kind="follow_up"),
        next_question("project", "Расскажите об учебном проекте."),
    )
    session = await service.start(resume_text="Начинающий backend разработчик")
    session = await service.submit_answer(session.id, "Мне нравится программирование.")
    assert session.turns[-1].kind == "follow_up"

    session = await service.submit_answer(session.id, "Нравится решать задачи и видеть результат.")
    assert session.turns[-1].topic_id == "project"
    assert sum(t.kind == "follow_up" for t in session.turns) <= service.MAX_FOLLOW_UPS_PER_TOPIC


@pytest.mark.asyncio
async def test_resume_answer_discrepancy_is_probed_neutrally_not_scored_as_deception():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question(
            "project",
            "В резюме проект указан как командный. Как были распределены задачи?",
            kind="follow_up",
        ),
    )
    session = await service.start(resume_text="Study API — командный учебный проект")
    session = await service.submit_answer(session.id, "Я сделал весь проект сам.")

    question = session.turns[-1].text.lower()
    assert "распредел" in question or "личн" in question or "уточн" in question
    assert not any(word in question for word in ("обман", "лж", "поймал", "мошенн"))


@pytest.mark.asyncio
async def test_candidate_self_correction_is_preserved_as_later_evidence():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question("project", "Какой был ваш личный вклад?", kind="follow_up"),
        next_question("teamwork", "Как вы взаимодействовали с командой?"),
    )
    session = await service.start(resume_text="Учебный проект")
    session = await service.submit_answer(session.id, "Сначала сказал, что делал API один.")
    session = await service.submit_answer(session.id, "Уточню: база была командной, API делал я.")

    candidate_texts = [t.text for t in session.turns if t.role == "candidate"]
    assert len(candidate_texts) == 2
    assert "Уточню" in candidate_texts[-1]
    assert "командной" in candidate_texts[-1]


@pytest.mark.asyncio
async def test_internship_candidate_without_work_history_is_not_penalized_for_lack_of_employment():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question("project", "Расскажите об учебном или личном проекте."),
        report(
            recommendation="mixed_signal",
            evidence=[
                {
                    "criterion": "personal_contribution",
                    "source_turn_id": "turn-2",
                    "quote": "Я сделал API для учебного проекта.",
                    "observation": "Есть опыт учебного проекта.",
                }
            ],
        ),
    )
    session = await service.start(resume_text="Студент, коммерческого опыта нет; учебный API")
    session = await service.submit_answer(session.id, "Коммерческого опыта у меня нет.")
    assert session.turns[-1].topic_id == "project"


@pytest.mark.asyncio
async def test_i_do_not_remember_answer_closes_line_of_questioning_without_pressure():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question("project", "Не помните точную цифру? Тогда перейдём дальше."),
    )
    session = await service.start(resume_text="Учебный проект")
    session = await service.submit_answer(session.id, "Точную цифру не помню.")
    assert session.turns[-1].topic_id == "project"


@pytest.mark.asyncio
async def test_question_repeat_request_does_not_add_scoring_hint():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        next_question("motivation", "Переформулирую: что вам интересно в работе backend-разработчика?", kind="repeat"),
    )
    session = await service.start(resume_text="Учебный API")
    session = await service.submit_answer(session.id, "Повторите, пожалуйста.")

    text = session.turns[-1].text.lower()
    assert session.turns[-1].kind == "repeat"
    assert not any(hint in text for hint in ("лучше ответить", "правильный ответ", "нужно сказать", "скажи, что"))


@pytest.mark.asyncio
async def test_report_is_refused_or_marked_low_confidence_when_evidence_is_insufficient():
    service = build_service(
        next_question("motivation", "Почему backend?"),
        report(recommendation="insufficient_data", evidence=[]),
    )
    session = await service.start(resume_text="Студент")
    finished = await service.finish(session.id)

    assert finished.report.recommendation == "insufficient_data"
    assert finished.report.uncertainties
    assert not finished.report.scores or all(score is None for score in finished.report.scores.values())


@pytest.mark.asyncio
async def test_turn_limit_terminates_graph_instead_of_looping_forever():
    service = build_service(
        *[next_question("motivation", f"Вопрос {i}") for i in range(20)],
        limits={"max_turns": 4},
    )
    session = await service.start(resume_text="Учебный API")
    for _ in range(10):
        if session.status != "active":
            break
        session = await service.submit_answer(session.id, "Короткий ответ")

    assert session.status in {"completed", "awaiting_report"}
    assert len(session.turns) <= 5


@pytest.mark.asyncio
async def test_unknown_model_next_action_is_rejected_without_inventing_a_transition():
    service = build_service({"kind": "run_shell", "text": "do something", "topic_id": "motivation"})
    with pytest.raises(app_module("errors").InvalidModelOutput):
        await service.start(resume_text="Учебный API")
