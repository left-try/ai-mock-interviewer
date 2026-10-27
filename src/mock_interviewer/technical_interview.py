"""State and evidence contracts for a voice-based technical interview."""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidReport, InvalidSessionTransition, SessionNotFound


LEVELS = {"internship", "junior", "middle"}
TURN_KINDS = {"question", "follow_up", "repeat"}
CALIBRATION_TOPICS = {
    "programming_fundamentals",
    "data_structures",
    "runtime_basics",
    "concurrency_basics",
}
DEEP_DIVE_TOPICS = {
    "runtime_and_memory",
    "concurrency_and_io",
    "framework_lifecycle",
    "data_and_errors",
    "performance_and_tradeoffs",
}
REPORT_CRITERIA = {
    "technical_fundamentals",
    "runtime_and_execution",
    "concurrency_and_memory",
    "stack_internals",
    "practical_reasoning",
}
MAX_ANSWER_CHARS = 8_000
MAX_TURN_TEXT_CHARS = 1_000
MAX_STACK_CHARS = 120
MAX_FOLLOW_UPS_PER_TOPIC = 2
CALIBRATION_ANSWER_COUNT = 4
DEEP_DIVE_ANSWER_LIMIT = 6
MAX_QUOTE_CHARS = 500


@dataclass
class TechnicalTurn:
    id: str
    role: str
    text: str
    phase: str
    topic_id: str | None = None
    kind: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TechnicalInterviewSession:
    id: str
    status: str = "active"
    phase: str = "calibration"
    estimated_level: str | None = None
    stack: str | None = None
    turns: list[TechnicalTurn] = field(default_factory=list)
    pending_answer_event_id: str | None = None
    answer_payloads: dict[str, str] = field(default_factory=dict, repr=False)
    follow_ups: dict[str, int] = field(default_factory=dict, repr=False)
    report: dict[str, Any] | None = None
    version: int = 0


class TechnicalInterviewService:
    """In-memory sessions with answer-first writes and evidence-checked reports."""

    def __init__(self):
        self._sessions: dict[str, TechnicalInterviewSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, *, first_turn: dict[str, Any]) -> TechnicalInterviewSession:
        turn = self._validate_turn(first_turn, phase="calibration")
        session = TechnicalInterviewSession(id=str(uuid.uuid4()))
        session.turns.append(self._interviewer_turn(session, turn))
        self._sessions[session.id] = session
        self._locks[session.id] = asyncio.Lock()
        return self._copy(session)

    async def persist_answer(
        self,
        session_id: str,
        text: str,
        *,
        event_id: str,
        finish_after_answer: bool = False,
    ) -> TechnicalInterviewSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            clean = self._validate_answer(text)
            self._validate_event_id(event_id)
            prior = session.answer_payloads.get(event_id)
            if prior is not None:
                if prior != clean:
                    raise InvalidSessionTransition(
                        "An event_id cannot be reused for different answer text."
                    )
                return self._copy(session)
            if session.status != "active":
                raise InvalidSessionTransition("This interview no longer accepts answers.")
            if session.pending_answer_event_id is not None:
                raise InvalidSessionTransition(
                    "The previous saved answer needs a question proposal or early finish."
                )
            if session.phase not in {"calibration", "deep_dive"}:
                raise InvalidSessionTransition("The current interview phase does not accept an answer.")

            session.answer_payloads[event_id] = clean
            session.turns.append(TechnicalTurn(
                id=self._turn_id(session),
                role="candidate",
                text=clean,
                phase=session.phase,
                topic_id=self._active_topic(session),
            ))
            session.version += 1

            if finish_after_answer:
                session.phase = "wrap_up"
                session.status = "awaiting_report"
            elif session.phase == "calibration" and self._answer_count(session, "calibration") >= CALIBRATION_ANSWER_COUNT:
                session.phase = "stack_selection"
            elif session.phase == "deep_dive" and self._answer_count(session, "deep_dive") >= DEEP_DIVE_ANSWER_LIMIT:
                session.phase = "wrap_up"
                session.status = "awaiting_report"
            else:
                session.pending_answer_event_id = event_id
            return self._copy(session)

    async def select_stack(
        self,
        session_id: str,
        stack: str,
        *,
        event_id: str,
    ) -> TechnicalInterviewSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            self._validate_event_id(event_id)
            clean = stack.strip() if isinstance(stack, str) else ""
            prior = session.answer_payloads.get(event_id)
            if prior is not None:
                if prior != clean:
                    raise InvalidSessionTransition(
                        "An event_id cannot be reused for different answer text."
                    )
                return self._copy(session)
            if session.status != "active" or session.phase != "stack_selection":
                raise InvalidSessionTransition("The interview is not waiting for a stack selection.")
            if not clean:
                raise ValueError("stack must not be blank")
            if len(clean) > MAX_STACK_CHARS:
                raise ValueError(f"stack exceeds the {MAX_STACK_CHARS} character limit")
            session.stack = clean
            session.answer_payloads[event_id] = clean
            session.turns.append(TechnicalTurn(
                id=self._turn_id(session),
                role="candidate",
                text=clean,
                phase="stack_selection",
                topic_id="stack_selection",
                kind="stack_selection",
            ))
            session.phase = "deep_dive"
            session.pending_answer_event_id = event_id
            session.version += 1
            return self._copy(session)

    async def propose_next_turn(
        self,
        session_id: str,
        *,
        event_id: str,
        proposal: dict[str, Any],
    ) -> TechnicalInterviewSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            if session.status != "active":
                raise InvalidSessionTransition("This interview no longer accepts a question proposal.")
            if session.pending_answer_event_id != event_id:
                raise InvalidSessionTransition("The proposal does not match the pending answer event.")
            turn = self._validate_turn(proposal, phase=session.phase, existing_turns=session.turns)
            if turn["kind"] == "follow_up":
                count = session.follow_ups.get(turn["topic_id"], 0)
                if count >= MAX_FOLLOW_UPS_PER_TOPIC:
                    raise InvalidSessionTransition("The follow-up limit for this topic has been reached.")
                session.follow_ups[turn["topic_id"]] = count + 1
            session.turns.append(self._interviewer_turn(session, turn))
            session.pending_answer_event_id = None
            session.version += 1
            return self._copy(session)

    async def get_session(self, session_id: str) -> TechnicalInterviewSession:
        return self._copy(self._require(session_id))

    async def finish(
        self,
        session_id: str,
        *,
        report: dict[str, Any],
    ) -> TechnicalInterviewSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            if session.status != "awaiting_report":
                raise InvalidSessionTransition("The interview is not ready for a report.")
            validated = self._validate_report(report, session)
            session.report = validated
            session.estimated_level = validated["estimated_level"]
            session.status = "completed"
            session.version += 1
            return self._copy(session)

    async def cancel(self, session_id: str) -> TechnicalInterviewSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            if session.status == "completed":
                raise InvalidSessionTransition("A completed interview cannot be cancelled.")
            session.status = "cancelled"
            session.phase = "wrap_up"
            session.version += 1
            return self._copy(session)

    async def delete(self, session_id: str) -> None:
        self._require(session_id)
        async with self._locks[session_id]:
            self._sessions.pop(session_id, None)
            self._locks.pop(session_id, None)

    @staticmethod
    def _copy(session: TechnicalInterviewSession) -> TechnicalInterviewSession:
        return copy.deepcopy(session)

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
            raise ValueError("event_id must be a non-empty string of at most 128 characters")

    @staticmethod
    def _validate_answer(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("candidate answer must not be blank")
        clean = text.strip()
        if len(clean) > MAX_ANSWER_CHARS:
            raise ValueError("candidate answer exceeds the length limit")
        return clean

    def _validate_turn(
        self,
        proposal: dict[str, Any],
        *,
        phase: str,
        existing_turns: list[TechnicalTurn] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(proposal, dict):
            raise ValueError("turn proposal must be an object")
        if proposal.get("phase") != phase:
            raise ValueError(f"phase must be {phase}")
        if phase not in {"calibration", "deep_dive"}:
            raise ValueError("a question can only be proposed during an interview question phase")
        kind = proposal.get("kind")
        if kind not in TURN_KINDS:
            raise ValueError("kind must be question, follow_up, or repeat")
        topic_id = proposal.get("topic_id")
        allowed_topics = CALIBRATION_TOPICS if phase == "calibration" else DEEP_DIVE_TOPICS
        if topic_id not in allowed_topics:
            raise ValueError(f"unknown topic for {phase} phase")
        text = proposal.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("question text must not be blank")
        clean = text.strip()
        if len(clean) > MAX_TURN_TEXT_CHARS:
            raise ValueError("question text exceeds the length limit")
        evidence = proposal.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("turn evidence must be a list")
        existing_ids = {turn.id for turn in existing_turns or []}
        for item in evidence:
            if not isinstance(item, dict) or item.get("turn_id") not in existing_ids:
                raise ValueError("turn evidence must reference an existing turn")
        return {
            "kind": kind,
            "phase": phase,
            "topic_id": topic_id,
            "text": clean,
            "evidence": copy.deepcopy(evidence),
        }

    def _interviewer_turn(self, session: TechnicalInterviewSession, proposal: dict[str, Any]) -> TechnicalTurn:
        return TechnicalTurn(
            id=self._turn_id(session),
            role="interviewer",
            text=proposal["text"],
            phase=proposal["phase"],
            topic_id=proposal["topic_id"],
            kind=proposal["kind"],
            evidence=copy.deepcopy(proposal.get("evidence", [])),
        )

    @staticmethod
    def _turn_id(session: TechnicalInterviewSession) -> str:
        return f"turn-{len(session.turns) + 1}"

    @staticmethod
    def _active_topic(session: TechnicalInterviewSession) -> str | None:
        return next((turn.topic_id for turn in reversed(session.turns) if turn.role == "interviewer"), None)

    @staticmethod
    def _answer_count(session: TechnicalInterviewSession, phase: str) -> int:
        return sum(turn.role == "candidate" and turn.phase == phase for turn in session.turns)

    def _require(self, session_id: str) -> TechnicalInterviewSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFound("Technical interview session was not found")
        return session

    def _validate_report(self, report: dict[str, Any], session: TechnicalInterviewSession) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise InvalidReport("report must be an object")
        estimated_level = report.get("estimated_level")
        if estimated_level is not None and estimated_level not in LEVELS:
            raise InvalidReport("estimated_level must be internship, junior, middle, or null")
        confidence = report.get("confidence")
        if confidence not in {"low", "medium", "high"}:
            raise InvalidReport("confidence must be low, medium, or high")
        raw_scores = report.get("scores")
        if not isinstance(raw_scores, dict) or set(raw_scores) != REPORT_CRITERIA:
            raise InvalidReport("scores must contain every supported technical criterion")
        scores: dict[str, int | None] = {}
        for criterion, score in raw_scores.items():
            if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5):
                raise InvalidReport(f"score for {criterion} must be 1–5 or null")
            scores[criterion] = score

        evidence = report.get("evidence")
        if not isinstance(evidence, list):
            raise InvalidReport("evidence must be a list")
        candidate_turns = {
            turn.id: turn
            for turn in session.turns
            if turn.role == "candidate" and turn.phase in {"calibration", "deep_dive"}
        }
        supported: dict[str, set[str]] = {criterion: set() for criterion in REPORT_CRITERIA}
        clean_evidence = []
        for item in evidence:
            if not isinstance(item, dict):
                raise InvalidReport("evidence entries must be objects")
            turn_id = item.get("turn_id")
            quote = item.get("quote")
            criterion = item.get("criterion_id")
            turn = candidate_turns.get(turn_id)
            if turn is None or not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
                raise InvalidReport("evidence must cite a candidate answer with a short exact quote")
            if quote not in turn.text:
                raise InvalidReport("evidence quote is not an exact substring of the cited answer")
            if criterion not in REPORT_CRITERIA:
                raise InvalidReport("evidence must identify a supported criterion")
            supported[criterion].add(turn_id)
            clean_evidence.append({"turn_id": turn_id, "quote": quote, "criterion_id": criterion})
        for criterion, score in scores.items():
            if score is not None and not supported[criterion]:
                raise InvalidReport(f"a scored {criterion} criterion needs candidate-answer evidence")

        calibration_count = self._answer_count(session, "calibration")
        if calibration_count < 3:
            if estimated_level is not None or confidence != "low" or any(score is not None for score in scores.values()):
                raise InvalidReport("insufficient calibration evidence requires a null level, low confidence, and null scores")
        elif estimated_level is None and confidence != "low":
            raise InvalidReport("a null estimated_level requires low confidence")
        if estimated_level is None and not report.get("uncertainties"):
            raise InvalidReport("an unassessed level must include an uncertainty")
        if estimated_level is not None and not any(
            turn_id in candidate_turns and candidate_turns[turn_id].phase == "calibration"
            for refs in supported.values()
            for turn_id in refs
        ):
            raise InvalidReport("an estimated level needs evidence from a calibration answer")

        for field_name in ("strengths", "growth_areas", "uncertainties"):
            value = report.get(field_name)
            if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
                raise InvalidReport(f"{field_name} must be a list of strings")
        disclaimer = report.get("disclaimer")
        if not isinstance(disclaimer, str) or not disclaimer.strip():
            raise InvalidReport("disclaimer must not be blank")
        return {
            "estimated_level": estimated_level,
            "confidence": confidence,
            "scores": scores,
            "strengths": list(report["strengths"]),
            "growth_areas": list(report["growth_areas"]),
            "evidence": clean_evidence,
            "uncertainties": list(report["uncertainties"]),
            "disclaimer": disclaimer.strip(),
        }

    @staticmethod
    def _escape_markdown(value: str) -> str:
        flattened = value.replace("\r", " ").replace("\n", " ")
        return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", r"\\\1", flattened)

    @staticmethod
    def render_report_markdown(session: TechnicalInterviewSession) -> str:
        report = session.report
        if report is None:
            raise InvalidSessionTransition("The interview report is not available yet.")
        candidate_text = " ".join(turn.text for turn in session.turns if turn.role == "candidate")
        cyrillic = sum("\u0400" <= char <= "\u04ff" for char in candidate_text)
        latin = sum(char.isascii() and char.isalpha() for char in candidate_text)
        russian = cyrillic > latin
        labels = {
            "technical_fundamentals": ("Технические основы", "Technical fundamentals"),
            "runtime_and_execution": ("Выполнение и runtime", "Runtime and execution"),
            "concurrency_and_memory": ("Конкурентность и память", "Concurrency and memory"),
            "stack_internals": ("Внутреннее устройство стека", "Stack internals"),
            "practical_reasoning": ("Практическое рассуждение", "Practical reasoning"),
        }
        labels_by_key = {key: value[0 if russian else 1] for key, value in labels.items()}
        level = report["estimated_level"] or ("Недостаточно данных" if russian else "Insufficient data")
        confidence = report["confidence"]
        scores = "\n".join(
            f"- {labels_by_key[key]}: {value if value is not None else ('недостаточно данных' if russian else 'insufficient data')}"
            for key, value in report["scores"].items()
        )
        evidence = "\n".join(
            f"- «{TechnicalInterviewService._escape_markdown(item['quote'])}» — "
            f"{labels_by_key[item['criterion_id']]} ({item['turn_id']})"
            for item in report["evidence"]
        ) or ("- Недостаточно ответов для подтверждённой оценки." if russian else "- Not enough answers for a supported assessment.")
        strengths = "\n".join(f"- {TechnicalInterviewService._escape_markdown(item)}" for item in report["strengths"]) or ("- Нет достаточных данных." if russian else "- Not enough evidence.")
        growth = "\n".join(f"- {TechnicalInterviewService._escape_markdown(item)}" for item in report["growth_areas"]) or ("- Нет достаточных данных." if russian else "- Not enough evidence.")
        uncertainties = "\n".join(f"- {TechnicalInterviewService._escape_markdown(item)}" for item in report["uncertainties"]) or ("- Нет." if russian else "- None.")
        if russian:
            return (
                "# Отчёт технического интервью\n\n"
                f"**Стек:** {TechnicalInterviewService._escape_markdown(session.stack) if session.stack else 'не выбран'}  \n"
                f"**Оценённый уровень:** {level}  \n"
                f"**Уверенность:** {confidence}\n\n"
                f"## Оценка\n{scores}\n\n"
                f"## Сильные стороны\n{strengths}\n\n"
                f"## Зоны роста\n{growth}\n\n"
                f"## Свидетельства из ответов\n{evidence}\n\n"
                f"## Неопределённость\n{uncertainties}\n\n"
                f"_{TechnicalInterviewService._escape_markdown(report['disclaimer'])}_"
            )
        return (
            "# Technical interview report\n\n"
            f"**Stack:** {TechnicalInterviewService._escape_markdown(session.stack) if session.stack else 'not selected'}  \n"
            f"**Estimated level:** {level}  \n"
            f"**Confidence:** {confidence}\n\n"
            f"## Scores\n{scores}\n\n"
            f"## Strengths\n{strengths}\n\n"
            f"## Growth areas\n{growth}\n\n"
            f"## Evidence from answers\n{evidence}\n\n"
            f"## Uncertainties\n{uncertainties}\n\n"
            f"_{TechnicalInterviewService._escape_markdown(report['disclaimer'])}_"
        )
