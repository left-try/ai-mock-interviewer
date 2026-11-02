from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _bridge():
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return importlib.import_module("interview_mcp")


def q(phase, topic, text):
    return json.dumps({"kind": "question", "phase": phase, "topic_id": topic, "text": text, "evidence": []})


def report(turn_id, quote):
    return {
        "estimated_level": "junior",
        "confidence": "medium",
        "scores": {
            "technical_fundamentals": 3,
            "runtime_and_execution": None,
            "concurrency_and_memory": None,
            "stack_internals": None,
            "practical_reasoning": None,
        },
        "strengths": ["Объясняет базовые понятия."],
        "growth_areas": [],
        "evidence": [{"turn_id": turn_id, "quote": quote, "criterion_id": "technical_fundamentals"}],
        "uncertainties": [],
        "disclaimer": "Учебная обратная связь для практики; это не решение о найме.",
    }


def test_voice_skill_contract_covers_calibration_stack_deep_dive_and_report():
    root = Path(__file__).resolve().parents[1]
    skill = (root / ".agents/skills/technical-stack-interviewer/SKILL.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Codex Voice" in skill
    normalized = skill.lower()
    assert normalized.index("start with calibration") < normalized.index("estimate the candidate's level") < normalized.index("choose a stack") < normalized.index("start the deep dive")
    assert "any backend stack" in skill.lower()
    assert "one question at a time" in skill.lower()
    assert "same `event_id`" in skill
    assert "exact quote" in skill.lower()
    assert "cancel_technical_interview" in skill
    assert "delete_technical_interview" in skill
    assert "::codex-realtime-inline{}" in skill
    assert "technical-stack-interviewer" in readme


@pytest.mark.asyncio
async def test_mcp_workflow_calibrates_selects_stack_and_finishes_with_evidence():
    bridge = _bridge()
    started = await bridge.start_technical_interview(
        first_turn_json=q("calibration", "programming_fundamentals", "Что происходит при вызове функции?")
    )
    assert started["ok"] is True
    assert "техническое интервью" in started["opening_script"].lower()
    assert "что происходит при вызове функции?" in started["opening_script"].lower()
    session_id = started["session_id"]

    topics = ["data_structures", "runtime_basics", "concurrency_basics"]
    for index in range(4):
        result = await bridge.record_technical_interview_answer(
            session_id=session_id,
            transcript=f"Ответ калибровки {index}",
            event_id=f"cal-{index}",
            next_turn_json=(q("calibration", topics[index], f"Вопрос калибровки {index}") if index < 3 else ""),
        )
        if index == 0:
            duplicate = await bridge.record_technical_interview_answer(
                session_id=session_id,
                transcript="Ответ калибровки 0",
                event_id="cal-0",
                next_turn_json=q("calibration", topics[0], "Вопрос калибровки 0"),
            )
            assert duplicate["ok"] is True
            assert duplicate["duplicate"] is True
            assert duplicate["next_action"] == "ask_next_question"
            assert duplicate["next_turn"]["text"] == "Вопрос калибровки 0"
    assert result["next_action"] == "select_stack"
    assert result["phase"] == "stack_selection"
    assert result["next_turn"] is None

    invalid_stack_question = await bridge.select_technical_stack(
        session_id=session_id,
        stack="Elixir / Phoenix + PostgreSQL",
        event_id="stack-1",
        first_turn_json=q("calibration", "runtime_basics", "Question in the wrong phase?"),
    )
    assert invalid_stack_question["ok"] is False
    assert invalid_stack_question["answer_saved"] is True
    selected = await bridge.select_technical_stack(
        session_id=session_id,
        stack="Elixir / Phoenix + PostgreSQL",
        event_id="stack-1",
        first_turn_json=q("deep_dive", "runtime_and_memory", "Как устроена память в этом стеке?"),
    )
    assert selected["ok"] is True
    assert selected["stack"] == "Elixir / Phoenix + PostgreSQL"

    finished = await bridge.record_technical_interview_answer(
        session_id=session_id,
        transcript="Остановимся здесь.",
        event_id="deep-1",
        finish_after_answer=True,
    )
    assert finished["next_action"] == "finish_interview"
    status = await bridge.technical_interview_status(session_id)
    calibration_answer = next(turn for turn in status["turns"] if turn["role"] == "candidate" and turn["phase"] == "calibration")
    invalid_report = await bridge.finish_technical_interview(
        session_id=session_id,
        report_json=json.dumps(report(calibration_answer["id"], "цитаты нет")),
    )
    assert invalid_report["ok"] is False
    completed = await bridge.finish_technical_interview(
        session_id=session_id,
        report_json=json.dumps(report(calibration_answer["id"], calibration_answer["text"])),
    )
    assert completed["ok"] is True
    assert completed["interview_complete"] is True
    assert "Оценённый уровень" in completed["report_markdown"]


@pytest.mark.asyncio
async def test_mcp_retries_only_invalid_question_after_answer_saved():
    bridge = _bridge()
    started = await bridge.start_technical_interview(
        first_turn_json=q("calibration", "programming_fundamentals", "Опишите понятие.")
    )
    session_id = started["session_id"]
    failed = await bridge.record_technical_interview_answer(
        session_id=session_id,
        transcript="Ответ уже сохранён.",
        event_id="retry-1",
        next_turn_json=q("calibration", "architecture", "Недопустимая тема?"),
    )
    assert failed["ok"] is False
    assert failed["answer_saved"] is True
    retried = await bridge.record_technical_interview_answer(
        session_id=session_id,
        transcript="Ответ уже сохранён.",
        event_id="retry-1",
        next_turn_json=q("calibration", "data_structures", "Расскажите про структуру данных."),
    )
    assert retried["ok"] is True
    status = await bridge.technical_interview_status(session_id)
    assert sum(turn["role"] == "candidate" for turn in status["turns"]) == 1
    assert status["turns"][-1]["text"] == "Расскажите про структуру данных."


@pytest.mark.asyncio
async def test_mcp_early_finish_requires_insufficient_evidence_and_cancel_deletes():
    bridge = _bridge()
    started = await bridge.start_technical_interview(
        first_turn_json=q("calibration", "programming_fundamentals", "Что такое процесс?")
    )
    session_id = started["session_id"]
    await bridge.record_technical_interview_answer(
        session_id=session_id,
        transcript="Не знаю.",
        event_id="early-1",
        finish_after_answer=True,
    )
    weak_report = {
        "estimated_level": None,
        "confidence": "low",
        "scores": {
            "technical_fundamentals": None,
            "runtime_and_execution": None,
            "concurrency_and_memory": None,
            "stack_internals": None,
            "practical_reasoning": None,
        },
        "strengths": [],
        "growth_areas": [],
        "evidence": [],
        "uncertainties": ["Недостаточно ответов для оценки уровня."],
        "disclaimer": "Учебная обратная связь; это не решение о найме.",
    }
    result = await bridge.finish_technical_interview(session_id, json.dumps(weak_report))
    assert result["ok"] is True
    assert "Недостаточно данных" in result["report_markdown"]

    another = await bridge.start_technical_interview(
        first_turn_json=q("calibration", "programming_fundamentals", "Ещё один вопрос?")
    )
    cancelled = await bridge.cancel_technical_interview(another["session_id"])
    assert cancelled["ok"] is True
    assert cancelled["deleted"] is True
    assert (await bridge.technical_interview_status(another["session_id"]))["ok"] is False

    deletable = await bridge.start_technical_interview(
        first_turn_json=q("calibration", "programming_fundamentals", "Удаляемая сессия?")
    )
    deleted = await bridge.delete_technical_interview(deletable["session_id"])
    assert deleted == {"ok": True, "deleted": True}
