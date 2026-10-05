"""MCP bridge: Codex Voice proposes turns; LangGraph validates session moves."""

from __future__ import annotations

import json
import asyncio
import threading
from typing import Any

from mock_interviewer.errors import InterviewError
from mock_interviewer.interview_plan import interview_progress
from mock_interviewer.service import InterviewService

from voice_probe import mcp


class HostProposalModel:
    """Adapter for the subscription model already hosting Codex Voice.

    The host supplies a structured proposal as an MCP argument. LangGraph passes
    that proposal through the same validation boundary used by model adapters.
    No separate API key or paid inference endpoint is needed for the voice MVP.
    """

    def __init__(self):
        self._response: Any = None
        self._lock = threading.Lock()
        self.response_lock = asyncio.Lock()

    def provide(self, response: Any) -> None:
        with self._lock:
            self._response = response

    async def ainvoke(self, messages, *, response_schema=None, **kwargs):
        with self._lock:
            response, self._response = self._response, None
        if response is None:
            raise RuntimeError("The voice host did not provide a structured proposal")
        return response


_sessions: dict[str, tuple[InterviewService, HostProposalModel]] = {}
_sessions_lock = threading.RLock()


def _decode(value: str, field: str) -> dict:
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{field} must be a JSON object")
    return data


def _public_turn(turn):
    return {"id": turn.id, "role": turn.role, "text": turn.text, "topic": turn.topic_id, "kind": turn.kind}


def _public_delta(session, *, include_transcript=False):
    candidate = next((turn for turn in reversed(session.turns) if turn.role == "candidate"), None)
    latest = session.turns[-1] if session.turns else None
    result = {
        "candidate_turn": None if candidate is None else _public_turn(candidate),
        "next_turn": _public_turn(latest) if latest is not None and latest.role == "interviewer" else None,
    }
    if include_transcript:
        result["turns"] = [_public_turn(turn) for turn in session.turns]
    return result


def _public_session(session, *, include_transcript=True):
    result = {
        "session_id": session.id,
        "status": session.status,
        "interview_progress": interview_progress(session.turns),
        "report": None if session.report is None else {
            "recommendation": session.report.recommendation,
            "scores": session.report.scores,
            "strengths": session.report.strengths,
            "growth_areas": session.report.growth_areas,
            "evidence": [item if isinstance(item, dict) else item.model_dump() for item in session.report.evidence],
            "uncertainties": session.report.uncertainties,
            "disclaimer": session.report.disclaimer,
        },
    }
    if include_transcript:
        result["turns"] = [_public_turn(turn) for turn in session.turns]
    return result


def _next_action(session, progress):
    if session.status == "awaiting_report" or progress["ready_to_finish"]:
        return "finish_interview"
    return "ask_next_question"


@mcp.tool()
async def start_interview(resume_text: str, first_turn_json: str) -> dict:
    """Start Backend Internship HR practice. Log every finalized answer using record_candidate_answer; follow the returned interview progress through automatic completion."""
    try:
        proposal = _decode(first_turn_json, "first_turn_json")
        model = HostProposalModel()
        model.provide(proposal)
        service = InterviewService(model=model)
        session = await service.start(resume_text=resume_text)
        with _sessions_lock:
            _sessions[session.id] = (service, model)
        return {
            "ok": True,
            **_public_session(session),
            "next_action": "ask_next_question",
            "instruction": (
                "Ask the opening question. After every finalized candidate answer, call "
                "record_candidate_answer exactly once before asking anything else; use a "
                "new event_id for each answer. Follow interview_progress and next_action. "
                "At finish_interview, clearly tell the candidate the interview is complete, "
                "then call finish_interview and give its validated practice report. Do not "
                "expose scoring criteria or internal notes."
            ),
        }
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def record_candidate_answer(
    session_id: str,
    transcript: str,
    event_id: str,
    next_turn_json: str = "",
    include_transcript: bool = True,
) -> dict:
    """Record one finalized answer; the result says whether to ask another question or finish."""
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, model = pair
        # Duplicate event IDs are checked by the service before any proposal is consumed.
        async with model.response_lock:
            previous = await service.get_event_result(session_id, event_id)
            if previous is not None:
                prior_progress = interview_progress(previous.turns)
                result = {
                    "ok": True,
                    "duplicate": True,
                    "next_action": _next_action(previous, prior_progress),
                    "interview_progress": prior_progress,
                }
                result.update(_public_delta(previous, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(previous))
                return result
            before = await service.get_session(session_id)
            if before.status == "awaiting_report":
                session = await service.submit_answer(
                    session_id,
                    transcript,
                    event_id=event_id,
                    allow_final_correction=True,
                )
                progress = interview_progress(session.turns)
                result = {
                    "ok": True,
                    "status": session.status,
                    "interview_progress": progress,
                    "next_action": "finish_interview",
                    "instruction": (
                        "The final transcript update was stored. Do not ask another question. "
                        "Call finish_interview and only announce completion after it succeeds."
                    ),
                }
                result.update(_public_delta(session, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(session))
                return result
            anticipated_progress = interview_progress(before.turns, pending_answer=True)
            if anticipated_progress["ready_to_finish"]:
                session = await service.submit_answer(
                    session_id,
                    transcript,
                    event_id=event_id,
                    finish_after_answer=True,
                )
                result = {
                    "ok": True,
                    "status": session.status,
                    "interview_progress": interview_progress(session.turns),
                    "next_action": "finish_interview",
                    "instruction": (
                        "The required coverage or answer limit has been reached. Do not ask "
                        "another question. Call finish_interview first; only after it succeeds, "
                        "tell the candidate clearly that the interview is complete and present "
                        "its report."
                    ),
                }
                result.update(_public_delta(session, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(session))
                return result
            if not next_turn_json:
                raise ValueError("next_turn_json is required until the interview is ready to finish")
            proposal = _decode(next_turn_json, "next_turn_json")
            model.provide(proposal)
            session = await service.submit_answer(session_id, transcript, event_id=event_id)
            progress = interview_progress(session.turns)
        result = {
            "ok": True,
            "status": session.status,
            "interview_progress": progress,
            "next_action": _next_action(session, progress),
            "instruction": (
                "The transcript was stored once. Use the returned candidate_turn and next_turn "
                "only; do not request or restate the full transcript. Ask next_turn only when "
                "next_action is ask_next_question. If next_action is finish_interview, call "
                "finish_interview first, then announce completion only after that call succeeds."
            ),
        }
        result.update(_public_delta(session, include_transcript=include_transcript))
        if include_transcript:
            result.update(_public_session(session))
        return result
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def finish_interview(session_id: str, report_json: str) -> dict:
    """Finish the interview and validate its evidence-based training report."""
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, model = pair
        async with model.response_lock:
            model.provide(_decode(report_json, "report_json"))
            session = await service.finish(session_id)
        return {
            "ok": True,
            **_public_session(session, include_transcript=False),
            "interview_complete": session.status == "completed",
            "instruction": (
                "Tell the candidate clearly in their language that the interview is complete "
                "(for example, say ‘Интервью завершено’), then present the "
                "validated report as practice feedback. If recommendation is insufficient_data, "
                "explain that evidence was limited. Do not claim a hiring decision."
            ),
        }
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def interview_status(session_id: str) -> dict:
    """Read the interview transcript and status for the active session."""
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        return {"ok": True, **_public_session(await service.get_session(session_id))}
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def cancel_interview(session_id: str) -> dict:
    """Cancel an interview and discard its in-memory session state."""
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        session = await service.cancel(session_id)
        await service.delete(session_id)
        with _sessions_lock:
            _sessions.pop(session_id, None)
        return {"ok": True, "status": session.status, "deleted": True}
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def delete_interview(session_id: str) -> dict:
    """Permanently remove an interview and its resume/transcript from memory."""
    try:
        with _sessions_lock:
            pair = _sessions.pop(session_id, None)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        await service.delete(session_id)
        return {"ok": True, "deleted": True}
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def run_server():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
