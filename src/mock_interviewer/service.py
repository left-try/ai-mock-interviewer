"""Application service implementing the stable interview contracts."""

from __future__ import annotations

import asyncio
import copy
import io
import re
import uuid
import zipfile
from typing import Any

from pydantic import BaseModel, Field

from .domain import rules
from .domain.models import InterviewReport, InterviewSession, NextTurn, Turn
from .errors import (
    ConcurrentSessionUpdate, InvalidModelOutput, InvalidReport,
    InvalidSessionTransition, ModelProviderError, ResumeParseError,
    ResumeValidationError, SessionNotFound,
)
from .graph import build_graph
from .prompts import interview_messages, report_messages
from .resume import ResumeParser


class NextTurnSchema(BaseModel):
    kind: str = Field(description="question, follow_up, or repeat")
    topic_id: str = Field(description="One allowlisted interview topic")
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ReportSchema(BaseModel):
    recommendation: str
    scores: dict[str, int | None] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    growth_areas: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    disclaimer: str


class InterviewService:
    MAX_RESUME_BYTES = 2 * 1024 * 1024
    MAX_RESUME_CHARS = 20_000
    MAX_ANSWER_CHARS = 8_000
    MAX_UPLOAD_CHARS = 2_000_000
    MAX_FOLLOW_UPS_PER_TOPIC = 2
    MAX_TURNS = 24
    ALLOWED_TOPICS = {
        "motivation", "education", "project", "personal_contribution", "teamwork",
        "challenge", "reflection", "expectations",
    }
    ALLOWED_KINDS = {"question", "follow_up", "repeat"}
    ALLOWED_RECOMMENDATIONS = {"strong_signal", "mixed_signal", "insufficient_data"}
    ALLOWED_CRITERIA = {
        "self_presentation", "motivation", "personal_contribution",
        "communication", "reflection", "consistency", "teamwork", "challenge",
    }
    DISCLAIMER = "Учебная обратная связь для практики; это не решение о найме."

    def __init__(self, *, model, resume_parser=None, limits=None, rubric=None):
        self.model = model
        self.resume_parser = resume_parser or ResumeParser()
        self.limits = {"max_turns": self.MAX_TURNS, **(limits or {})}
        self.rubric = copy.deepcopy(rubric or {})
        self._sessions: dict[str, InterviewSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._graph = build_graph()

    async def _invoke(self, messages, schema):
        try:
            result = await self._graph.ainvoke({
                "model": self.model, "messages": messages, "response_schema": schema,
            })
            return result["result"]
        except (InvalidModelOutput, InvalidReport):
            raise
        except Exception as exc:
            raise ModelProviderError() from exc

    async def start(self, *, resume_text: str) -> InterviewSession:
        text = self._validate_resume_text(resume_text)
        session = InterviewSession(id=str(uuid.uuid4()), status="active", resume_text=text)
        try:
            value = await self._invoke(interview_messages(text, [], rubric=self.rubric), NextTurnSchema)
            next_turn = self._validate_next_turn(value)
        except (InvalidModelOutput, ModelProviderError):
            raise
        session.turns.append(self._interviewer_turn(next_turn, len(session.turns) + 1))
        self._sessions[session.id] = session
        self._locks[session.id] = asyncio.Lock()
        return self._copy(session)

    async def start_from_upload(self, content: bytes, *, filename: str) -> InterviewSession:
        self._validate_upload(content, filename)
        try:
            text = await self.resume_parser.extract_text(content, filename=filename)
        except ResumeValidationError:
            raise
        except Exception as exc:
            raise ResumeParseError("Resume could not be parsed. Check the file and try again.") from exc
        return await self.start(resume_text=text)

    async def submit_answer(
        self,
        session_id: str,
        text: str,
        *,
        event_id: str | None = None,
        finish_after_answer: bool = False,
        allow_final_correction: bool = False,
    ) -> InterviewSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        if lock.locked():
            raise ConcurrentSessionUpdate("Another update is already being processed for this interview.")
        async with lock:
            session = self._require(session_id)
            if event_id:
                if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
                    raise ValueError("Invalid event_id")
                prior = session.event_results.get(event_id)
                if prior is not None:
                    return self._copy(prior)
            is_final_correction = (
                allow_final_correction
                and session.status == "awaiting_report"
                and session.report is None
            )
            if not rules.can_transition(session.status, "submit_answer") and not is_final_correction:
                raise InvalidSessionTransition("This interview no longer accepts answers.")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Candidate answer must not be blank")
            clean = text.strip()
            if len(clean) > self.MAX_ANSWER_CHARS:
                raise ValueError("Candidate answer exceeds the length limit")
            candidate = Turn(id=self._turn_id(session), role="candidate", text=clean)
            if is_final_correction:
                session.turns.append(candidate)
                session.version += 1
                if event_id:
                    session.event_results[event_id] = self._copy(session)
                return self._copy(session)
            if finish_after_answer:
                session.turns.append(candidate)
                session.status = "awaiting_report"
                session.version += 1
                if event_id:
                    session.event_results[event_id] = self._copy(session)
                return self._copy(session)
            proposed_turns = [*session.turns, candidate]
            if len(proposed_turns) >= int(self.limits["max_turns"]):
                session.turns.append(candidate)
                session.status = "awaiting_report"
                session.version += 1
                if event_id:
                    session.event_results[event_id] = self._copy(session)
                return self._copy(session)
            try:
                value = await self._invoke(
                    interview_messages(session.resume_text, proposed_turns, rubric=self.rubric),
                    NextTurnSchema,
                )
                next_turn = self._validate_next_turn(value)
            except (InvalidModelOutput, ModelProviderError):
                raise
            history = [turn.text for turn in session.turns if turn.role == "interviewer"]
            if rules.is_duplicate_question(next_turn.text, history):
                session.turns.append(candidate)
                session.status = "awaiting_report"
                session.version += 1
                if event_id:
                    session.event_results[event_id] = self._copy(session)
                return self._copy(session)
            if next_turn.kind == "follow_up":
                count = session.follow_ups.get(next_turn.topic_id, 0)
                if count >= self.MAX_FOLLOW_UPS_PER_TOPIC:
                    session.turns.append(candidate)
                    session.status = "awaiting_report"
                    session.version += 1
                    if event_id:
                        session.event_results[event_id] = self._copy(session)
                    return self._copy(session)
                session.follow_ups[next_turn.topic_id] = count + 1
            session.turns.extend((candidate, self._interviewer_turn(next_turn, len(session.turns) + 2)))
            session.version += 1
            if event_id:
                session.event_results[event_id] = self._copy(session)
            return self._copy(session)

    async def finish(self, session_id: str) -> InterviewSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        if lock.locked():
            raise ConcurrentSessionUpdate("Another update is already being processed for this interview.")
        async with lock:
            session = self._require(session_id)
            if session.status == "completed":
                return self._copy(session)
            if not (session.status == "active" or rules.can_transition(session.status, "retry_report")):
                raise InvalidSessionTransition("This interview cannot be completed from its current state.")
            session.status = "awaiting_report"
            try:
                value = await self._invoke(
                    report_messages(session.resume_text, session.turns, rubric=self.rubric), ReportSchema
                )
                report = self._validate_report(value, session)
            except (InvalidReport, ModelProviderError):
                raise
            session.report = report
            session.status = "completed"
            session.version += 1
            return self._copy(session)

    async def cancel(self, session_id: str) -> InterviewSession:
        session = self._require(session_id)
        lock = self._locks[session_id]
        if lock.locked():
            raise ConcurrentSessionUpdate("Another update is already being processed for this interview.")
        async with lock:
            session = self._require(session_id)
            if session.status == "cancelled":
                return self._copy(session)
            if not rules.can_transition(session.status, "cancel"):
                raise InvalidSessionTransition("This interview cannot be cancelled from its current state.")
            session.status = "cancelled"
            session.version += 1
            return self._copy(session)

    async def delete(self, session_id: str) -> None:
        self._require(session_id)
        self._sessions.pop(session_id, None)
        self._locks.pop(session_id, None)

    async def get_session(self, session_id: str) -> InterviewSession:
        return self._copy(self._require(session_id))

    async def get_event_result(self, session_id: str, event_id: str) -> InterviewSession | None:
        session = self._require(session_id)
        result = session.event_results.get(event_id)
        return self._copy(result) if result is not None else None

    async def list_sessions(self) -> list[InterviewSession]:
        return [self._copy(session) for session in self._sessions.values()]

    def _require(self, session_id):
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound("Interview session was not found") from exc

    @staticmethod
    def _copy(session):
        snapshot = copy.deepcopy(session)
        snapshot.event_results = {}
        return snapshot

    def _turn_id(self, session):
        return f"turn-{len(session.turns) + 1}"

    def _interviewer_turn(self, result: NextTurn, sequence: int):
        return Turn(
            id=f"turn-{sequence}", role="interviewer", text=result.text,
            topic_id=result.topic_id, kind=result.kind, evidence=result.evidence,
        )

    def _validate_resume_text(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Resume text must not be blank")
        clean = text.strip()
        if len(clean) > self.MAX_RESUME_CHARS:
            raise ValueError("Resume text exceeds the length limit")
        return clean

    def _validate_upload(self, content, filename):
        if not isinstance(content, bytes) or not content or len(content) > self.MAX_RESUME_BYTES:
            raise ResumeValidationError("Resume file is empty or exceeds the size limit")
        if not isinstance(filename, str) or filename != filename.replace("\\", "/").split("/")[-1]:
            raise ResumeValidationError("Invalid resume filename")
        if filename in {".", ".."} or filename.startswith("."):
            raise ResumeValidationError("Invalid resume filename")
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in {"pdf", "docx"} or filename.lower().endswith((".pdf.exe", ".docx.exe")):
            raise ResumeValidationError("Only PDF and DOCX resumes are supported")
        if suffix == "docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    names = archive.namelist()
                    if "word/document.xml" not in names or len(names) > 2000:
                        raise ValueError
                    if sum(item.file_size for item in archive.infolist()) > self.MAX_UPLOAD_CHARS:
                        raise ValueError
            except Exception as exc:
                raise ResumeValidationError("Invalid DOCX file") from exc

    def _validate_next_turn(self, value):
        data = self._mapping(value)
        try:
            kind, topic, text = data["kind"], data["topic_id"], data["text"]
            evidence = data.get("evidence", [])
            confidence = data.get("confidence", 0.0)
        except Exception as exc:
            raise InvalidModelOutput("Model returned an incomplete interviewer turn") from exc
        if not isinstance(kind, str) or not isinstance(topic, str) or kind not in self.ALLOWED_KINDS or topic not in self.ALLOWED_TOPICS:
            raise InvalidModelOutput("Model returned a disallowed interviewer action")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise InvalidModelOutput("Model returned invalid question text")
        if not isinstance(evidence, list) or not isinstance(confidence, (float, int)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise InvalidModelOutput("Model returned invalid turn evidence or confidence")
        return NextTurn(kind, topic, text.strip(), evidence, float(confidence))

    def _validate_report(self, value, session):
        data = self._mapping(value)
        try:
            recommendation = data["recommendation"]
            scores = data["scores"]
            strengths = data["strengths"]
            growth = data["growth_areas"]
            evidence = data["evidence"]
            uncertainties = data["uncertainties"]
            disclaimer = data["disclaimer"]
        except Exception as exc:
            raise InvalidReport("Model returned an incomplete practice report") from exc
        if not isinstance(recommendation, str) or recommendation not in self.ALLOWED_RECOMMENDATIONS:
            raise InvalidReport("Model returned an unsupported recommendation")
        if not isinstance(scores, dict):
            raise InvalidReport("Report scores must be an object")
        invalid_scores = set(scores) - self.ALLOWED_CRITERIA
        if invalid_scores:
            allowed = ", ".join(sorted(self.ALLOWED_CRITERIA))
            invalid = ", ".join(sorted(invalid_scores))
            raise InvalidReport(f"Unknown scoring criteria: {invalid}. Allowed criteria: {allowed}")
        try:
            for score in scores.values():
                rules.validate_score(score)
        except (ValueError, TypeError) as exc:
            raise InvalidReport("Report contains an invalid score") from exc
        if not all(isinstance(value, list) and all(isinstance(x, str) for x in value) for value in (strengths, growth, uncertainties)):
            raise InvalidReport("Report lists must contain text")
        if not isinstance(disclaimer, str) or not disclaimer.strip() or not any(k in disclaimer.casefold() for k in ("учеб", "practice", "training")):
            raise InvalidReport("Report must include a training disclaimer")
        if not isinstance(evidence, list):
            raise InvalidReport("Report evidence must be a list")
        turn_sources = {turn.id: turn.text for turn in session.turns if turn.role == "candidate"}
        turn_sources["resume"] = session.resume_text
        for item in evidence:
            item = self._mapping(item)
            criterion, source_id, quote = item.get("criterion"), item.get("source_turn_id"), item.get("quote")
            if not isinstance(criterion, str) or criterion not in self.ALLOWED_CRITERIA:
                allowed = ", ".join(sorted(self.ALLOWED_CRITERIA))
                raise InvalidReport(f"Unknown evidence criterion: {criterion}. Allowed criteria: {allowed}")
            if not isinstance(source_id, str) or source_id not in turn_sources:
                raise InvalidReport(f"Unknown evidence source: {source_id}. Use a candidate turn ID from interview_status or 'resume'.")
            if not isinstance(quote, str) or not rules.quote_is_supported(quote, turn_sources[source_id]):
                raise InvalidReport(f"Evidence quote is not present in source {source_id}. Use an exact excerpt from that candidate turn or resume.")
            if not isinstance(item.get("observation"), str) or not item["observation"].strip():
                raise InvalidReport("Report evidence observation is required")
        if recommendation == "insufficient_data":
            scores = {key: None for key in scores}
            if not uncertainties:
                uncertainties = ["Недостаточно ответов для надёжной оценки."]
        return InterviewReport(recommendation, copy.deepcopy(scores), list(strengths), list(growth), copy.deepcopy(evidence), list(uncertainties), disclaimer.strip())

    @staticmethod
    def _mapping(value):
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            result = value.model_dump()
            if isinstance(result, dict):
                return result
        if hasattr(value, "dict"):
            result = value.dict()
            if isinstance(result, dict):
                return result
        raise InvalidModelOutput("Model response must be a structured object")
