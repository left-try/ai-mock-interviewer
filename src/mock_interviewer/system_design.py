"""System design interview lifecycle and evidence validation.

This scenario is deliberately separate from Backend Screening. The host authors
each question, while this service owns ordering, persistence, board cursors and
report evidence references.
"""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .errors import InvalidReport, InvalidSessionTransition, SessionNotFound


PHASES = ("discovery", "design", "deep_dive", "wrap_up")
TOPICS = {
    "problem_and_users",
    "requirements",
    "scale_and_estimates",
    "data_and_interfaces",
    "architecture",
    "reliability_and_scaling",
    "tradeoffs_and_adaptation",
    "communication",
}
TURN_KINDS = {"question", "follow_up", "repeat"}
CRITERIA = {
    "problem_understanding",
    "requirements_and_priorities",
    "quantitative_estimates",
    "data_and_interfaces",
    "architecture",
    "scaling_and_reliability",
    "tradeoffs_and_adaptation",
    "communication_and_consistency",
}
LEVELS = {"internship", "junior", "middle"}
SCENARIOS = {"url-shortener"}
URL_SHORTENER_FACTS = {
    "daily_active_users": 100_000_000,
    "redirects_per_user_per_day": 3,
    "redirect_reads_per_link_creation": 100,
    "average_redirect_rps": 3_500,
    "peak_redirect_rps": 20_000,
    "average_link_creations_per_day": 1_000_000,
    "redirect_p95_ms": 100,
    "redirect_p99_ms": 200,
    "redirect_availability": "99.95%",
    "creation_p95_ms": 300,
    "new_link_visibility_seconds": 5,
}
MAX_ANSWER_CHARS = 8_000
MAX_TURN_TEXT_CHARS = 1_000
MAX_BOARD_EVENTS_PER_SESSION = 1_000
MAX_QUOTE_CHARS = 500
MAX_CANDIDATE_ANSWERS = 10
MAX_FOLLOW_UPS_PER_TOPIC = 2


@dataclass
class SystemDesignTurn:
    id: str
    role: str
    text: str
    phase: str
    topic_id: str | None = None
    kind: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""


@dataclass
class SystemDesignSession:
    id: str
    level: str
    scenario_id: str
    board_enabled: bool
    board_url: str | None
    board_failure_reason: str | None = None
    board_was_enabled: bool = False
    status: str = "active"
    phase: str = "discovery"
    turns: list[SystemDesignTurn] = field(default_factory=list)
    board_events: list[dict[str, Any]] = field(default_factory=list)
    board_cursor: int = 0
    board_read_answer_event_id: str | None = None
    answer_payloads: dict[str, str] = field(default_factory=dict, repr=False)
    answer_results: dict[str, "SystemDesignSession"] = field(default_factory=dict, repr=False)
    pending_answer_event_id: str | None = None
    proposal_payloads: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    follow_ups: dict[str, int] = field(default_factory=dict, repr=False)
    report: dict[str, Any] | None = None
    uncertainties: list[str] = field(default_factory=list)
    version: int = 0


class SystemDesignInterviewService:
    """In-memory interview sessions with persistence-first answer handling."""

    DISCLAIMER = "Учебная обратная связь для практики систем-дизайна; это не решение о найме."

    def __init__(self, *, board_base_url: str = "http://127.0.0.1:8765"):
        self.board_base_url = board_base_url.rstrip("/")
        self._sessions: dict[str, SystemDesignSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(
        self,
        *,
        level: str,
        scenario_id: str = "url-shortener",
        board_consent: bool,
        board_failure_reason: str | None = None,
    ) -> SystemDesignSession:
        if level not in LEVELS:
            raise ValueError("level must be internship, junior, or middle")
        if scenario_id not in SCENARIOS:
            raise ValueError("unknown system design scenario")
        if not isinstance(board_consent, bool):
            raise ValueError("board_consent must be a boolean")
        session_id = str(uuid.uuid4())
        session = SystemDesignSession(
            id=session_id,
            level=level,
            scenario_id=scenario_id,
            board_enabled=board_consent,
            board_url=(
                f"{self.board_base_url}/?session_id={session_id}" if board_consent else None
            ),
            board_failure_reason=board_failure_reason,
            board_was_enabled=board_consent,
            uncertainties=([
                f"Excalidraw was requested but unavailable ({board_failure_reason}); the session is text/voice only."
            ] if board_failure_reason else []),
        )
        session.turns.append(SystemDesignTurn(
            id="turn-1",
            role="interviewer",
            phase="discovery",
            topic_id="problem_and_users",
            kind="question",
            text="Спроектируйте сервис сокращения ссылок для веб- и мобильных клиентов. Начните с того, как вы подойдёте к задаче.",
            created_at=_now(),
        ))
        self._sessions[session.id] = session
        self._locks[session.id] = asyncio.Lock()
        return self._copy(session)

    async def persist_answer(
        self,
        session_id: str,
        transcript: str,
        *,
        event_id: str,
        finish_after_answer: bool = False,
    ) -> SystemDesignSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        async with lock:
            session = self._require(session_id)
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
                raise ValueError("event_id must be a non-empty string of at most 128 characters")
            if not isinstance(transcript, str) or not transcript.strip():
                raise ValueError("candidate answer must not be blank")
            clean = transcript.strip()
            if len(clean) > MAX_ANSWER_CHARS:
                raise ValueError("candidate answer exceeds the length limit")
            prior = session.answer_results.get(event_id)
            if prior is not None:
                if session.answer_payloads[event_id] != clean:
                    raise InvalidSessionTransition("An event_id cannot be reused for different answer text.")
                return self._copy(session)
            if session.status != "active":
                raise InvalidSessionTransition("This interview no longer accepts answers.")
            if session.pending_answer_event_id is not None:
                raise InvalidSessionTransition("The previous answer needs board capture and a question proposal.")
            turn = SystemDesignTurn(
                id=f"turn-{len(session.turns) + 1}",
                role="candidate",
                text=clean,
                phase=session.phase,
                created_at=_now(),
            )
            session.turns.append(turn)
            session.answer_payloads[event_id] = clean
            session.version += 1
            session.pending_answer_event_id = event_id
            answer_count = sum(turn.role == "candidate" for turn in session.turns)
            if finish_after_answer or session.phase == "wrap_up" or answer_count >= MAX_CANDIDATE_ANSWERS:
                session.status = "awaiting_report"
            session.answer_results[event_id] = self._copy(session)
            return self._copy(session)

    async def read_board_changes(
        self,
        session_id: str,
        *,
        after_cursor: int,
        source_events: list[dict[str, Any]],
    ) -> tuple[SystemDesignSession, list[dict[str, Any]]]:
        session = self._require(session_id)
        lock = self._locks[session_id]
        async with lock:
            session = self._require(session_id)
            if not session.board_enabled:
                return self._copy(session), []
            if session.status not in {"active", "awaiting_report"} or session.pending_answer_event_id is None:
                raise InvalidSessionTransition("Save a candidate answer before reading whiteboard changes.")
            if isinstance(after_cursor, bool) or not isinstance(after_cursor, int) or after_cursor < 0:
                raise ValueError("after_cursor must be a non-negative integer")
            if after_cursor > session.board_cursor:
                raise ValueError("after_cursor is ahead of this interview's whiteboard cursor")
            if len(session.board_events) + len(source_events) > MAX_BOARD_EVENTS_PER_SESSION:
                session.uncertainties.append("Whiteboard event limit reached; later board changes were not assessed.")
                raise ValueError("whiteboard event limit reached")

            known = {event["event_id"] for event in session.board_events}
            added: list[dict[str, Any]] = []
            candidate_turn = next(
                turn for turn in reversed(session.turns) if turn.role == "candidate"
            )
            for raw in source_events:
                event_id = raw.get("event_id")
                sequence = raw.get("sequence")
                if event_id in known:
                    continue
                if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
                    raise ValueError("whiteboard event has an invalid event_id")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= session.board_cursor:
                    raise ValueError("whiteboard event sequence is invalid")
                normalized = {
                    "event_id": event_id,
                    "session_id": session.id,
                    "source": "excalidraw",
                    "captured_at": raw.get("captured_at"),
                    "sequence": sequence,
                    "cursor": sequence,
                    "kind": raw.get("kind"),
                    "payload": copy.deepcopy(raw.get("payload", {})),
                    "related_turn_id": candidate_turn.id,
                }
                session.board_events.append(normalized)
                added.append(normalized)
                known.add(event_id)
            if added:
                added.sort(key=lambda item: item["sequence"])
                session.board_cursor = added[-1]["sequence"]
                # A gap is a limitation in the observation history, not candidate evidence.
                sequences = [event["sequence"] for event in session.board_events]
                if sequences and sequences != list(range(1, sequences[-1] + 1)):
                    session.uncertainties.append("The whiteboard event history has a sequence gap.")
                session.version += 1
            session.board_read_answer_event_id = session.pending_answer_event_id
            return self._copy(session), added

    async def disable_board(self, session_id: str, *, reason: str) -> SystemDesignSession:
        if reason not in {"candidate_request", "capture_failure", "unsupported_host"}:
            raise ValueError("unsupported board disable reason")
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            if session.status not in {"active", "awaiting_report"}:
                raise InvalidSessionTransition("Whiteboard observation can no longer be changed.")
            session.board_enabled = False
            session.board_url = None
            session.board_failure_reason = None
            session.uncertainties.append(
                f"Whiteboard observation was disabled ({reason}) before the interview ended; later visual evidence is unavailable."
            )
            session.version += 1
            return self._copy(session)

    async def propose_next_turn(
        self,
        session_id: str,
        answer_event_id: str,
        proposal: dict[str, Any],
    ) -> SystemDesignSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        async with lock:
            session = self._require(session_id)
            if answer_event_id in session.proposal_payloads:
                if session.proposal_payloads[answer_event_id] != proposal:
                    raise InvalidSessionTransition("An answer event cannot be paired with a different question proposal.")
                return self._copy(session)
            if session.pending_answer_event_id != answer_event_id:
                raise InvalidSessionTransition("No saved answer is waiting for this question proposal.")
            if session.status != "active":
                raise InvalidSessionTransition("This interview cannot accept a question proposal now.")
            if session.board_enabled and session.board_read_answer_event_id != answer_event_id:
                raise InvalidSessionTransition("Read this answer's whiteboard changes before proposing the next question.")
            if not isinstance(proposal, dict):
                raise ValueError("next_turn_json must be a JSON object")
            kind = proposal.get("kind")
            phase = proposal.get("phase")
            topic_id = proposal.get("topic_id")
            text = proposal.get("text")
            if kind not in TURN_KINDS:
                raise ValueError("kind must be question, follow_up, or repeat")
            if phase not in PHASES:
                raise ValueError("phase is not allowed")
            if topic_id not in TOPICS:
                raise ValueError("topic_id is not allowed")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TURN_TEXT_CHARS:
                raise ValueError("question text must be non-empty and at most 1000 characters")
            if PHASES.index(phase) < PHASES.index(session.phase):
                raise InvalidSessionTransition("System design interview phases cannot move backwards.")
            if PHASES.index(phase) > PHASES.index(session.phase) + 1:
                raise InvalidSessionTransition("A question may advance the interview by only one phase.")
            answer_count = sum(turn.role == "candidate" for turn in session.turns)
            if answer_count >= MAX_CANDIDATE_ANSWERS - 1 and phase != "wrap_up":
                raise InvalidSessionTransition("The final planned question must be a wrap-up question.")
            evidence = proposal.get("evidence", [])
            if not isinstance(evidence, list) or len(evidence) > 8:
                raise ValueError("evidence must be a list of at most 8 references")
            known_ids = {turn.id for turn in session.turns}
            for reference in evidence:
                if not isinstance(reference, dict) or reference.get("turn_id") not in known_ids:
                    raise ValueError("next-turn evidence must reference an existing turn_id")
            question_key = _normalize_question(text)
            if question_key in {_normalize_question(t.text) for t in session.turns if t.role == "interviewer"}:
                raise InvalidSessionTransition("The proposed interviewer question repeats an earlier question.")
            if kind == "follow_up" and session.follow_ups.get(topic_id, 0) >= MAX_FOLLOW_UPS_PER_TOPIC:
                raise InvalidSessionTransition("The follow-up limit for this topic has been reached.")
            if kind == "follow_up":
                session.follow_ups[topic_id] = session.follow_ups.get(topic_id, 0) + 1
            session.phase = phase
            session.turns.append(SystemDesignTurn(
                id=f"turn-{len(session.turns) + 1}",
                role="interviewer",
                text=text.strip(),
                phase=phase,
                topic_id=topic_id,
                kind=kind,
                evidence=copy.deepcopy(evidence),
                created_at=_now(),
            ))
            session.pending_answer_event_id = None
            session.board_read_answer_event_id = None
            session.proposal_payloads[answer_event_id] = copy.deepcopy(proposal)
            session.version += 1
            session.answer_results[answer_event_id] = self._copy(session)
            return self._copy(session)

    async def finish(self, session_id: str, proposal: dict[str, Any]) -> SystemDesignSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        async with lock:
            session = self._require(session_id)
            if session.status == "completed":
                return self._copy(session)
            if session.status != "awaiting_report":
                raise InvalidSessionTransition("Finish the interview only after an explicit stop or wrap-up answer.")
            if session.pending_answer_event_id is not None and session.board_enabled and session.board_read_answer_event_id != session.pending_answer_event_id:
                raise InvalidSessionTransition("Read the final answer's whiteboard changes before finishing.")
            report = self._validate_report(proposal, session)
            if session.board_failure_reason:
                report["uncertainties"].append("Excalidraw observation could not be started; visual architecture evidence is unavailable.")
            elif not session.board_enabled and not session.board_events and not session.board_was_enabled:
                report["uncertainties"].append("Excalidraw observation was not enabled; visual architecture evidence is unavailable.")
            elif not session.board_events:
                if session.board_was_enabled:
                    report["uncertainties"].append("No whiteboard changes were received; conclusions rely on the conversation only.")
            report["uncertainties"] = list(dict.fromkeys(report["uncertainties"] + session.uncertainties))
            session.report = report
            session.status = "completed"
            session.pending_answer_event_id = None
            session.board_read_answer_event_id = None
            session.version += 1
            return self._copy(session)

    async def cancel(self, session_id: str) -> SystemDesignSession:
        session = self._require(session_id)
        async with self._locks[session_id]:
            session = self._require(session_id)
            if session.status == "completed":
                raise InvalidSessionTransition("A completed interview cannot be cancelled.")
            session.status = "cancelled"
            session.version += 1
            return self._copy(session)

    async def delete(self, session_id: str) -> None:
        self._require(session_id)
        self._sessions.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def get_session(self, session_id: str) -> SystemDesignSession:
        return self._copy(self._require(session_id))

    async def get_answer_result(self, session_id: str, event_id: str) -> SystemDesignSession | None:
        result = self._require(session_id).answer_results.get(event_id)
        return self._copy(result) if result is not None else None

    def _validate_report(self, value: dict[str, Any], session: SystemDesignSession) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise InvalidReport("System design report must be an object.")
        summary = value.get("summary")
        criteria = value.get("criteria")
        strengths = value.get("strengths", [])
        growth_areas = value.get("growth_areas", [])
        uncertainties = value.get("uncertainties", [])
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 2_000:
            raise InvalidReport("Report summary must be non-empty and at most 2000 characters.")
        if not isinstance(criteria, list) or len(criteria) != len(CRITERIA):
            raise InvalidReport("Report must include every system design criterion exactly once.")
        if not all(isinstance(items, list) and len(items) <= 10 and all(isinstance(item, str) and len(item) <= 500 for item in items) for items in (strengths, growth_areas, uncertainties)):
            raise InvalidReport("Report lists must contain at most 10 concise text items.")
        candidate_text = " ".join(turn.text for turn in session.turns if turn.role == "candidate")
        report_prose = " ".join([
            summary,
            *(str(item.get("assessment", "")) for item in criteria if isinstance(item, dict)),
            *(text for group in (strengths, growth_areas, uncertainties) for text in group if isinstance(text, str)),
        ])
        if _is_russian_dominant(candidate_text) and not _is_russian_dominant(report_prose):
            raise InvalidReport("Отчёт для русскоязычного интервью должен быть написан по-русски.")
        turns = {turn.id: turn for turn in session.turns}
        board_events = {event["event_id"]: event for event in session.board_events}
        validated = []
        seen = set()
        for item in criteria:
            if not isinstance(item, dict):
                raise InvalidReport("Each criterion must be an object.")
            criterion_id = item.get("criterion_id")
            score = item.get("score")
            assessment = item.get("assessment")
            evidence = item.get("evidence", [])
            if criterion_id not in CRITERIA or criterion_id in seen:
                raise InvalidReport("Report contains an unknown or duplicate criterion.")
            seen.add(criterion_id)
            if score is not None and (isinstance(score, bool) or not isinstance(score, int) or score < 1 or score > 5):
                raise InvalidReport("Criterion scores must be integers from 1 to 5 or null.")
            if not isinstance(assessment, str) or not assessment.strip() or len(assessment) > 800:
                raise InvalidReport("Each criterion needs a concise assessment.")
            if not isinstance(evidence, list) or len(evidence) > 8:
                raise InvalidReport("Each criterion may cite at most 8 evidence items.")
            checked = []
            for ref in evidence:
                if not isinstance(ref, dict) or ref.get("source_type") not in {"turn", "board_event"}:
                    raise InvalidReport("Evidence must reference a turn or whiteboard event.")
                source_type = ref["source_type"]
                source_id = ref.get("source_id")
                if source_type == "turn":
                    turn = turns.get(source_id)
                    quote = ref.get("quote")
                    if turn is None or turn.role != "candidate":
                        raise InvalidReport("Evidence turn must be an existing candidate answer.")
                    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS or quote not in turn.text:
                        raise InvalidReport("Evidence quote must appear exactly in the cited candidate answer.")
                    checked.append({"source_type": "turn", "source_id": source_id, "quote": quote})
                else:
                    if source_id not in board_events:
                        raise InvalidReport("Evidence whiteboard event does not exist in this session.")
                    note = ref.get("note")
                    if not isinstance(note, str) or not note.strip() or len(note) > 500:
                        raise InvalidReport("Whiteboard evidence needs a short description of the observed event.")
                    checked.append({
                        "source_type": "board_event",
                        "source_id": source_id,
                        "sequence": board_events[source_id]["sequence"],
                        "note": note,
                    })
            if score is not None and not checked:
                raise InvalidReport("A scored criterion needs at least one evidence reference.")
            validated.append({
                "criterion_id": criterion_id,
                "score": score,
                "assessment": assessment.strip(),
                "evidence": checked,
            })
        if seen != CRITERIA:
            raise InvalidReport("Report must include every system design criterion exactly once.")
        return {
            "summary": summary.strip(),
            "criteria": validated,
            "strengths": [item.strip() for item in strengths],
            "growth_areas": [item.strip() for item in growth_areas],
            "uncertainties": [item.strip() for item in uncertainties],
            "disclaimer": self.DISCLAIMER,
        }

    def _require(self, session_id: str) -> SystemDesignSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound("System design interview session was not found") from exc

    @staticmethod
    def _copy(session: SystemDesignSession) -> SystemDesignSession:
        return copy.deepcopy(session)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _normalize_question(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _is_russian_dominant(value: str) -> bool:
    cyrillic = sum("а" <= char.casefold() <= "я" or char.casefold() == "ё" for char in value)
    latin = sum("a" <= char.casefold() <= "z" for char in value)
    return cyrillic >= 12 and cyrillic > latin


def public_system_design_turn(turn: SystemDesignTurn) -> dict[str, Any]:
    return {
        "id": turn.id,
        "role": turn.role,
        "text": turn.text,
        "phase": turn.phase,
        "topic": turn.topic_id,
        "kind": turn.kind,
    }


def render_system_design_report(session: SystemDesignSession) -> str:
    if session.report is None:
        raise InvalidReport("System design report has not been finalized.")
    names = {
        "problem_understanding": "Понимание задачи",
        "requirements_and_priorities": "Требования и приоритеты",
        "quantitative_estimates": "Количественные оценки",
        "data_and_interfaces": "Данные и интерфейсы",
        "architecture": "Архитектура",
        "scaling_and_reliability": "Масштабирование и надёжность",
        "tradeoffs_and_adaptation": "Компромиссы и адаптация",
        "communication_and_consistency": "Коммуникация и согласованность",
    }
    lines = [
        "# Отчёт: System Design интервью",
        "",
        f"Уровень: {session.level}",
        "",
        session.report["summary"],
        "",
        "## Критерии",
    ]
    for item in session.report["criteria"]:
        score = "Недостаточно данных" if item["score"] is None else f"{item['score']} / 5"
        lines.extend([f"### {names[item['criterion_id']]} — {score}", item["assessment"]])
        for ref in item["evidence"]:
            if ref["source_type"] == "turn":
                lines.append(f"- Доказательство, {ref['source_id']}: «{ref['quote']}»")
            else:
                lines.append(f"- Наблюдение по доске: {ref['note']}")
        lines.append("")
    if session.report["strengths"]:
        lines.extend(["## Сильные стороны", *[f"- {item}" for item in session.report["strengths"]], ""])
    if session.report["growth_areas"]:
        lines.extend(["## Что улучшить", *[f"- {item}" for item in session.report["growth_areas"]], ""])
    if session.report["uncertainties"]:
        lines.extend(["## Ограничения оценки", *[f"- {item}" for item in session.report["uncertainties"]], ""])
    lines.extend([session.report["disclaimer"], ""])
    return "\n".join(lines)
