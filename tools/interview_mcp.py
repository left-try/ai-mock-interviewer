"""MCP bridge: Codex Voice proposes turns; LangGraph validates session moves."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import webbrowser
import uuid
from pathlib import Path
from typing import Any, Literal

from voice_probe import mcp

from mock_interviewer.errors import InterviewError
from mock_interviewer.interview_plan import interview_progress
from mock_interviewer.report_export import (
    ReportExportError,
    default_reports_dir,
    export_report_files,
    render_report_markdown,
)
from mock_interviewer.service import InterviewService
from mock_interviewer.scenarios import level_label, normalize_level
from mock_interviewer.test_profile import SYNTHETIC_BACKEND_PROFILES
from mock_interviewer.test_run_log import TestRunLog, default_test_runs_dir
from mock_interviewer.test_run_summary import summarize_events


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
REPORTS_DIR = default_reports_dir()
TEST_RUNS_DIR = default_test_runs_dir()
_test_runs: dict[str, tuple[str, TestRunLog]] = {}
_test_last_call_end: dict[str, float] = {}
_test_run_ended: set[str] = set()
_test_logged_answers: dict[str, set[str]] = {}
_plan_auth_instance = None
_plan_clients = {}
_route_tasks: dict[str, asyncio.Task] = {}

INITIAL_INTERVIEW_QUESTION = "Расскажите о backend-проекте или задаче, которые лучше всего показывают ваш уровень и вклад."


def _plan_auth():
    global _plan_auth_instance
    if _plan_auth_instance is None:
        from mock_interviewer.subscription_auth import ChatGPTPlanAuth
        _plan_auth_instance = ChatGPTPlanAuth()
    return _plan_auth_instance


def _plan_client(*, model_role="fast", timing_recorder=None):
    from mock_interviewer.subscription_inference import ChatGPTPlanClient
    if timing_recorder is not None:
        return ChatGPTPlanClient(auth=_plan_auth(), model_role=model_role, timing_recorder=timing_recorder)
    if model_role not in _plan_clients:
        _plan_clients[model_role] = ChatGPTPlanClient(auth=_plan_auth(), model_role=model_role)
    return _plan_clients[model_role]


async def _configured_routes(*, timing_recorder=None):
    from mock_interviewer.model_settings import ConfiguredModelRoute, InterviewModelSettings
    started = time.perf_counter()
    auth = _plan_auth()
    status = auth.status()
    if not status.connected or not status.account_id:
        raise ValueError("Connect ChatGPT plan access and configure interview models before starting")
    fast_client = _plan_client(model_role="fast", timing_recorder=timing_recorder)
    await fast_client.list_models()
    analysis_client = _plan_client(model_role="analysis", timing_recorder=timing_recorder)
    # The model catalog is account-scoped; reuse this lookup for both routes.
    analysis_client._models = dict(fast_client._models)
    analysis_client._catalog_account = status.account_id
    settings = InterviewModelSettings.load(account_id=status.account_id, client=fast_client)
    if timing_recorder is not None:
        timing_recorder("model_setup", duration_ms=(time.perf_counter() - started) * 1000)
    return (
        ConfiguredModelRoute(fast_client, model=settings.fast_model, effort=settings.fast_effort),
        ConfiguredModelRoute(analysis_client, model=settings.analysis_model, effort=settings.analysis_effort),
    )


async def _prepare_session_routes(session_id: str):
    task = _route_tasks.get(session_id)
    if task is None:
        raise ValueError("Interview model routes are unavailable; restart the interview")
    if task.done() and task.exception() is not None:
        task = asyncio.create_task(_configured_routes())
        _route_tasks[session_id] = task
    fast_route, analysis_route = await task
    with _sessions_lock:
        pair = _sessions.get(session_id)
    if pair is None:
        raise ValueError("Interview session was not found")
    service, _ = pair
    from mock_interviewer.background_evaluation import BackgroundEvaluationQueue
    service.fast_model = fast_route
    service.background_evaluation_queue = BackgroundEvaluationQueue(model=analysis_route)
    return service


@mcp.tool()
async def list_chatgpt_plan_models() -> dict:
    """List models available to the connected ChatGPT subscription account."""
    try:
        models = await _plan_client().list_models()
        return {"ok": True, "models": [
            {"slug": model.slug, "display_name": model.display_name,
             "reasoning_efforts": list(model.reasoning_efforts) if model.reasoning_efforts is not None else None}
            for model in models
        ]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def configure_interview_models(
    fast_model: str, fast_effort: str, analysis_model: str, analysis_effort: str,
) -> dict:
    """Choose the live interview model and the background analysis model."""
    try:
        client = _plan_client()
        models = await client.list_models()
        from mock_interviewer.model_settings import InterviewModelSettings
        status = _plan_auth().status()
        if not status.connected or not status.account_id:
            raise ValueError("Connect a ChatGPT account before configuring interview models")
        settings = InterviewModelSettings(fast_model, fast_effort, analysis_model, analysis_effort).validate(client)
        settings.save(account_id=status.account_id)
        return {"ok": True, "account_id": status.account_id,
                "fast_model": settings.fast_model, "fast_effort": settings.fast_effort,
                "analysis_model": settings.analysis_model, "analysis_effort": settings.analysis_effort,
                "available_model_count": len(models)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def connect_chatgpt_plan() -> dict:
    """Connect a ChatGPT account for plan-based inference using browser consent."""
    auth = _plan_auth()
    attempt = auth.begin_login()
    try:
        opened = await asyncio.to_thread(webbrowser.open, attempt.authorization_url, 1, True)
        if not opened:
            auth.cancel_login(attempt)
            return {"ok": False, "error": "Could not open the system browser for ChatGPT sign-in"}
        status = await auth.wait_for_callback(attempt)
        return {"ok": True, "connected": status.connected, "account_id": status.account_id,
                "email": status.email, "scopes": list(status.scopes)}
    except Exception as exc:
        auth.cancel_login(attempt)
        result = {"ok": False, "connected": False, "error": str(exc)}
        if getattr(exc, "stage", None):
            result["stage"] = exc.stage
        return result


@mcp.tool()
async def chatgpt_plan_status() -> dict:
    """Return the connected ChatGPT plan account status without secret values."""
    try:
        status = _plan_auth().status()
        return {"ok": True, "connected": status.connected, "account_id": status.account_id,
                "email": status.email, "scopes": list(status.scopes), "expires_at": status.expires_at}
    except Exception:
        return {"ok": False, "connected": False, "error": "The saved ChatGPT account status is unavailable"}


@mcp.tool()
async def disconnect_chatgpt_plan() -> dict:
    """Remove saved ChatGPT plan credentials from the operating system credential store."""
    try:
        _plan_auth().disconnect()
        return {"ok": True, "connected": False}
    except Exception:
        return {"ok": False, "error": "Could not remove the saved ChatGPT credentials"}


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
        "test_mode": session.test_mode,
        "level": session.level,
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


def _opening_script(question: str, *, test_mode: bool, level: str = "internship") -> str:
    level = normalize_level(level)
    level_name = level_label(level)
    disclosure = (
        f"Это синтетическое собеседование Backend Screening уровня {level_name}; распознанные ответы "
        "сохраняются в локальном диагностическом журнале. "
        if test_mode
        else f"Это тренировочное интервью Backend Screening уровня {level_name}. "
    )
    return (
        f"Здравствуйте! {disclosure}Представим, что мы — продуктовая команда онлайн-магазина и развиваем "
        f"API каталога, заказов и оплаты. Поговорим о вашем опыте и инженерных решениях. Начнём: {question}"
    )


def _next_action(session, progress):
    if session.status == "awaiting_report" or progress["ready_to_finish"]:
        return "finish_interview"
    return "ask_next_question"


def _test_log_for(session_id: str) -> tuple[str, TestRunLog] | None:
    with _sessions_lock:
        return _test_runs.get(session_id)


def _append_test_event(session_id: str, event_name: str, *, duration_ms=None, details=None) -> str | None:
    entry = _test_log_for(session_id)
    if entry is None:
        return None
    run_id, logger = entry
    try:
        logger.append_event(
            run_id,
            event_name,
            session_id=session_id,
            duration_ms=duration_ms,
            details=details,
        )
        return None
    except (OSError, TypeError, ValueError) as exc:
        # Transcript/session state remains authoritative if local diagnostics fail.
        return f"Local test log could not be written: {exc}"


def _begin_test_call(session_id: str) -> None:
    if _test_log_for(session_id) is None:
        return
    now = time.perf_counter()
    with _sessions_lock:
        previous_end = _test_last_call_end.get(session_id)
    if previous_end is not None:
        _append_test_event(session_id, "client_gap", duration_ms=max(0.0, (now - previous_end) * 1000))


def _complete_test_call(session_id: str, started_at: float) -> None:
    _append_test_event(session_id, "mcp_tool_call", duration_ms=max(0.0, (time.perf_counter() - started_at) * 1000))
    with _sessions_lock:
        _test_last_call_end[session_id] = time.perf_counter()


def _fail_test_call(session_id: str, started_at: float, stage: str, exc: Exception) -> None:
    if _test_log_for(session_id) is None:
        return
    _append_test_event(
        session_id,
        "operation_failed",
        details={"stage": stage, "error_category": type(exc).__name__},
    )
    _complete_test_call(session_id, started_at)


@mcp.tool()
async def start_interview(resume_text: str, level: Literal["internship", "junior", "middle"]) -> dict:
    """Start Backend Screening at the selected internship, junior, or middle level."""
    try:
        level = normalize_level(level)
        model = HostProposalModel()
        service = InterviewService(model=model)
        session = await service.start(
            resume_text=resume_text, initial_question=INITIAL_INTERVIEW_QUESTION, level=level,
        )
        with _sessions_lock:
            _sessions[session.id] = (service, model)
        _route_tasks[session.id] = asyncio.create_task(_configured_routes())
        return {
            "ok": True,
            **_public_session(session),
            "opening_script": _opening_script(session.turns[-1].text, test_mode=False, level=level),
            "next_action": "ask_next_question",
            "instruction": (
                "Speak opening_script as one opening; the first question is already included, so do not "
                "repeat next_turn separately. After each finalized answer, if useful, say only a brief "
                "neutral transition such as 'Спасибо за ответ. Секунду.' Then call "
                "While the tool runs, say one concise, answer-specific listening reaction that does not "
                "claim analysis is complete. Then call record_candidate_answer once with the exact "
                "transcript and a new event_id. The server saves the answer before generating one "
                "adaptive question. After success, ask the returned next_turn exactly once. Never "
                "repeat or paraphrase it in a neighboring message. If the result says retry_saved_answer, "
                "retry with the same event_id and transcript. "
                "Follow interview_progress and next_action. "
                "At finish_interview, clearly tell the candidate the interview is complete, "
                "then call finish_interview and give its locally assembled practice report. Do not "
                "expose scoring criteria or internal notes."
            ),
        }
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def start_test_interview(level: Literal["internship", "junior", "middle"]) -> dict:
    """Start a resume-free synthetic Backend Screening at the selected level."""
    started_at = time.perf_counter()
    try:
        level = normalize_level(level)
        run_id = str(uuid.uuid4())
        logger = TestRunLog(root=TEST_RUNS_DIR)
        logger.append_event(run_id, "run_started", details={"test_mode": True})
        timing_recorder = lambda name, **meta: logger.append_timing_event(run_id, name, **meta)
        model = HostProposalModel()
        service = InterviewService(model=model)
        session = await service.start(
            resume_text=SYNTHETIC_BACKEND_PROFILES[level], test_mode=True,
            initial_question=INITIAL_INTERVIEW_QUESTION, level=level,
        )
        with _sessions_lock:
            _sessions[session.id] = (service, model)
            _test_runs[session.id] = (run_id, logger)
            _test_run_ended.discard(session.id)
            _test_logged_answers[session.id] = set()
        _route_tasks[session.id] = asyncio.create_task(
            _configured_routes(timing_recorder=timing_recorder)
        )
        log_warning = _append_test_event(session.id, "turn_ready", duration_ms=(time.perf_counter() - started_at) * 1000)
        if log_warning:
            await service.delete(session.id)
            with _sessions_lock:
                _sessions.pop(session.id, None)
                _test_runs.pop(session.id, None)
                _test_logged_answers.pop(session.id, None)
            return {"ok": False, "error": log_warning}
        duration = (time.perf_counter() - started_at) * 1000
        log_warning = _append_test_event(session.id, "mcp_tool_call", duration_ms=duration) or log_warning
        with _sessions_lock:
            _test_last_call_end[session.id] = time.perf_counter()
        result = {
            "ok": True,
            **_public_session(session),
            "test_mode": True,
            "opening_script": _opening_script(session.turns[-1].text, test_mode=True, level=level),
            "run_id": run_id,
            "log_path": str((Path(TEST_RUNS_DIR) / f"{run_id}.jsonl").resolve()),
            "next_action": "ask_next_question",
            "instruction": (
                "Произнеси opening_script целиком: он содержит раскрытие тестового режима и первый вопрос. "
                "После каждого завершённого ответа произнеси одну короткую, конкретную реакцию на его "
                "содержание, пока выполняется вызов, но не утверждай, что анализ уже завершён. Затем "
                "вызови record_candidate_answer один раз с точной транскрипцией и новым event_id. Сервер "
                "сохраняет ответ и сам выбирает один следующий вопрос. После результата задай returned "
                "next_turn ровно один раз; никогда не повторяй вопрос. При retry_saved_answer повтори "
                "тот же event_id и transcript. "
                "Не раскрывай критерии оценки."
            ),
        }
        if log_warning:
            result["diagnostic_warning"] = log_warning
        return result
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def save_candidate_answer(
    session_id: str,
    transcript: str,
    event_id: str,
    include_transcript: bool = False,
) -> dict:
    """Save one finalized voice answer before preparing a following question."""
    started_at = time.perf_counter()
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        _begin_test_call(session_id)
        previous = await service.get_event_result(session_id, event_id)
        save_started = time.perf_counter()
        session = await service.persist_answer(session_id, transcript, event_id=event_id)
        save_duration = (time.perf_counter() - save_started) * 1000
        log_warning = None
        with _sessions_lock:
            answer_already_logged = event_id in _test_logged_answers.get(session_id, set())
        if not answer_already_logged:
            log_warning = _append_test_event(
                session_id,
                "answer_saved",
                duration_ms=save_duration,
                details={"event_id": event_id, "answer_text": transcript.strip()},
            )
            if log_warning is None and _test_log_for(session_id) is not None:
                with _sessions_lock:
                    _test_logged_answers.setdefault(session_id, set()).add(event_id)
        progress = interview_progress(session.turns)
        next_action = "finish_interview" if session.status == "awaiting_report" or progress["ready_to_finish"] else "propose_next_turn"
        result = {
            "ok": True,
            "duplicate": previous is not None,
            "status": session.status,
            "interview_progress": progress,
            "answer_event_id": event_id,
            "next_action": next_action,
            "instruction": (
                "Кандидатский ответ сохранён. Коротко подтверди сохранение, но не включай и не "
                "переформулируй следующий вопрос в подтверждении. "
                "Если next_action равен propose_next_turn, сформулируй один следующий вопрос и вызови "
                "propose_next_turn для возвращённого answer_event_id; только после успешного вызова "
                "задай возвращённый next_turn ровно один раз. Если next_action равен finish_interview, "
                "не задавай вопрос и подготовь отчёт."
            ),
        }
        result.update(_public_delta(session, include_transcript=include_transcript))
        if include_transcript:
            result.update(_public_session(session))
        if log_warning:
            result["diagnostic_warning"] = log_warning
        _complete_test_call(session_id, started_at)
        return result
    except (InterviewError, ValueError) as exc:
        _fail_test_call(session_id, started_at, "answer_save", exc)
        return {"ok": False, "error": str(exc)}


async def propose_next_turn(
    session_id: str,
    answer_event_id: str,
    next_turn_json: str,
) -> dict:
    """Validate and store the next interviewer turn after its answer is durable."""
    started_at = time.perf_counter()
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        _begin_test_call(session_id)
        proposal = _decode(next_turn_json, "next_turn_json")
        before = await service.get_session(session_id)
        accepted_before = answer_event_id in before.proposal_payloads
        validation_started = time.perf_counter()
        session = await service.propose_next_turn(session_id, answer_event_id, proposal)
        validation_duration = (time.perf_counter() - validation_started) * 1000
        progress = interview_progress(session.turns)
        warning = None
        if not accepted_before:
            warning = _append_test_event(
                session_id,
                "question_proposal_accepted",
                details={"answer_event_id": answer_event_id},
            )
            _append_test_event(
                session_id,
                "proposal_validation",
                duration_ms=validation_duration,
                details={"answer_event_id": answer_event_id},
            )
        result = {
            "ok": True,
            "status": session.status,
            "interview_progress": progress,
            "next_action": "finish_interview" if progress["ready_to_finish"] else "ask_next_question",
            "instruction": (
                "If next_action is ask_next_question, ask the returned next_turn exactly once. "
                "Do not repeat or rephrase it in another message."
            ),
        }
        result.update(_public_delta(session, include_transcript=False))
        _complete_test_call(session_id, started_at)
        if warning:
            result["diagnostic_warning"] = warning
        return result
    except (InterviewError, ValueError) as exc:
        _fail_test_call(session_id, started_at, "proposal_validation", exc)
        return {"ok": False, "error": str(exc)}


async def _legacy_record_candidate_answer(
    session_id: str,
    transcript: str,
    event_id: str,
    next_turn_json: str = "",
    include_transcript: bool = True,
) -> dict:
    """Record one finalized answer and validate its next question in a single voice round-trip."""
    started_at = time.perf_counter()
    _begin_test_call(session_id)
    answer_saved = False
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
                proposal_pending = previous.pending_answer_event_id == event_id
                result = {
                    "ok": True,
                    "duplicate": True,
                    "next_action": "propose_next_turn" if proposal_pending else _next_action(previous, prior_progress),
                    "interview_progress": prior_progress,
                }
                if proposal_pending:
                    result.update({
                        "answer_saved": True,
                        "answer_event_id": event_id,
                        "instruction": "The answer is already saved. Retry only propose_next_turn with this answer_event_id.",
                    })
                result.update(_public_delta(previous, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(previous))
                return result
            before = await service.get_session(session_id)
            if before.status == "awaiting_report":
                save_started = time.perf_counter()
                session = await service.submit_answer(
                    session_id,
                    transcript,
                    event_id=event_id,
                    allow_final_correction=True,
                )
                answer_saved = True
                _append_test_event(
                    session_id,
                    "answer_saved",
                    duration_ms=(time.perf_counter() - save_started) * 1000,
                    details={"event_id": event_id, "answer_text": transcript.strip()},
                )
                progress = interview_progress(session.turns)
                result = {
                    "ok": True,
                    "status": session.status,
                    "interview_progress": progress,
                    "next_action": "finish_interview",
                    "instruction": (
                        "The final transcript update was stored. Do not ask another question. "
                        "Tell the candidate the questions are complete and you are preparing "
                        "the final report, then call finish_interview. Announce the interview "
                        "as fully complete only after that tool succeeds."
                    ),
                }
                result.update(_public_delta(session, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(session))
                return result
            anticipated_progress = interview_progress(before.turns, pending_answer=True)
            if anticipated_progress["ready_to_finish"]:
                save_started = time.perf_counter()
                session = await service.submit_answer(
                    session_id,
                    transcript,
                    event_id=event_id,
                    finish_after_answer=True,
                )
                answer_saved = True
                _append_test_event(
                    session_id,
                    "answer_saved",
                    duration_ms=(time.perf_counter() - save_started) * 1000,
                    details={"event_id": event_id, "answer_text": transcript.strip()},
                )
                result = {
                    "ok": True,
                    "status": session.status,
                    "interview_progress": interview_progress(session.turns),
                    "next_action": "finish_interview",
                    "instruction": (
                        "The required coverage or answer limit has been reached. Do not ask "
                        "another question. Tell the candidate the questions are complete and "
                        "you are preparing the final report, then call finish_interview. Only "
                        "after it succeeds, say the interview is complete and present the report."
                    ),
                }
                result.update(_public_delta(session, include_transcript=include_transcript))
                if include_transcript:
                    result.update(_public_session(session))
                return result
            if not next_turn_json:
                raise ValueError("next_turn_json is required until the interview is ready to finish")
            proposal = _decode(next_turn_json, "next_turn_json")
            save_started = time.perf_counter()
            session = await service.persist_answer(session_id, transcript, event_id=event_id)
            answer_saved = True
            _append_test_event(
                session_id,
                "answer_saved",
                duration_ms=(time.perf_counter() - save_started) * 1000,
                details={"event_id": event_id, "answer_text": transcript.strip()},
            )
            validation_started = time.perf_counter()
            session = await service.propose_next_turn(session_id, event_id, proposal)
            validation_duration = (time.perf_counter() - validation_started) * 1000
            _append_test_event(
                session_id,
                "question_proposal_accepted",
                details={"answer_event_id": event_id},
            )
            _append_test_event(
                session_id,
                "proposal_validation",
                duration_ms=validation_duration,
                details={"answer_event_id": event_id},
            )
            progress = interview_progress(session.turns)
        result = {
            "ok": True,
            "status": session.status,
            "interview_progress": progress,
            "next_action": _next_action(session, progress),
            "instruction": (
                "The transcript was stored once and the proposal validated. Use the returned "
                "candidate_turn and next_turn only; do not request or restate the full transcript. "
                "Ask next_turn exactly once and only when "
                "next_action is ask_next_question. If next_action is finish_interview, tell "
                "the candidate the questions are complete and you are preparing the final "
                "report, then call finish_interview; announce full completion only after it succeeds."
            ),
        }
        result.update(_public_delta(session, include_transcript=include_transcript))
        if include_transcript:
            result.update(_public_session(session))
        return result
    except (InterviewError, ValueError) as exc:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        pending = False
        if answer_saved and pair is not None:
            current = await pair[0].get_session(session_id)
            pending = current.pending_answer_event_id == event_id
        _append_test_event(
            session_id,
            "operation_failed",
            details={
                "stage": "question_proposal" if pending else "answer_recording",
                "error_category": type(exc).__name__,
            },
        )
        result = {"ok": False, "error": str(exc)}
        if pending:
            result.update({
                "answer_saved": True,
                "answer_event_id": event_id,
                "next_action": "propose_next_turn",
                "instruction": "The answer is already saved. Retry only propose_next_turn with this answer_event_id.",
            })
        return result
    except Exception as exc:
        pending = False
        if saved:
            try:
                current = await pair[0].get_session(session_id)
                pending = current.pending_answer_event_id == event_id
            except Exception:
                pass
        _fail_test_call(session_id, started_at, "fast_turn_generation", exc)
        result = {"ok": False, "error": "Interview processing failed; the saved answer can be retried."}
        if pending:
            result.update({
                "answer_saved": True, "answer_event_id": event_id,
                "next_action": "retry_saved_answer",
                "instruction": "The answer is saved. Retry record_candidate_answer with the same event_id and transcript.",
            })
        return result
    finally:
        _complete_test_call(session_id, started_at)


@mcp.tool()
async def record_candidate_answer(session_id: str, event_id: str, transcript: str) -> dict:
    """Save the exact recognized answer, then request one adaptive subscription-model turn."""
    started_at = time.perf_counter()
    _begin_test_call(session_id)
    saved = False
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, _ = pair
        before = await service.get_session(session_id)
        progress_before = interview_progress(before.turns, pending_answer=True)
        finish_after = bool(progress_before["ready_to_finish"])
        save_started = time.perf_counter()
        session = await service.persist_answer(
            session_id, transcript, event_id=event_id, finish_after_answer=finish_after,
        )
        saved = True
        duplicate = event_id in before.event_payloads
        if not duplicate:
            _append_test_event(
                session_id, "answer_saved", duration_ms=(time.perf_counter() - save_started) * 1000,
                details={"event_id": event_id, "answer_text": transcript.strip()},
            )
            with _sessions_lock:
                _test_logged_answers.setdefault(session_id, set()).add(event_id)
        service = await _prepare_session_routes(session_id)
        if not duplicate and session.turns and session.turns[-1].role == "candidate":
            candidate_turn = session.turns[-1]
            previous_question = next(
                (turn.text for turn in reversed(session.turns[:-1]) if turn.role == "interviewer"), "",
            )
            service.background_evaluation_queue.enqueue(
                session_id, candidate_turn, session.context_state, question=previous_question,
                rubric=service.rubric,
            )
        progress = interview_progress(session.turns)
        if finish_after:
            result = {
                "ok": True, "duplicate": duplicate, "answer_saved": True,
                "status": session.status, "interview_progress": progress,
                "next_action": "finish_interview",
                "instruction": "The answer is saved and interview questions are complete. Call finish_interview.",
            }
            result.update(_public_delta(session, include_transcript=False))
            _complete_test_call(session_id, started_at)
            return result

        turn_started = time.perf_counter()
        fast_result = await service.generate_next_turn(session_id, event_id)
        duration = (time.perf_counter() - turn_started) * 1000
        _append_test_event(session_id, "turn_ready", duration_ms=duration)
        session = fast_result.session
        progress = interview_progress(session.turns)
        result = {
            "ok": True, "duplicate": duplicate, "answer_saved": True,
            "status": session.status, "interview_progress": progress,
            "next_action": "finish_interview" if progress["ready_to_finish"] else "ask_next_question",
            "instruction": "Ask the returned next_turn exactly once. Do not repeat or paraphrase it.",
        }
        result.update(_public_delta(session, include_transcript=False))
        _complete_test_call(session_id, started_at)
        return result
    except (InterviewError, ValueError) as exc:
        pending = False
        if saved:
            try:
                current = await pair[0].get_session(session_id)
                pending = current.pending_answer_event_id == event_id
            except Exception:
                pass
        _fail_test_call(session_id, started_at, "fast_turn_generation", exc)
        result = {"ok": False, "error": str(exc)}
        if pending:
            result.update({
                "answer_saved": True, "answer_event_id": event_id,
                "next_action": "retry_saved_answer",
                "instruction": "The answer is saved. Retry record_candidate_answer with the same event_id and transcript.",
            })
        return result
    except Exception as exc:
        pending = False
        if saved:
            try:
                current = await pair[0].get_session(session_id)
                pending = current.pending_answer_event_id == event_id
            except Exception:
                pass
        _fail_test_call(session_id, started_at, "fast_turn_generation", exc)
        result = {"ok": False, "error": "Interview processing failed; the saved answer can be retried."}
        if pending:
            result.update({
                "answer_saved": True, "answer_event_id": event_id,
                "next_action": "retry_saved_answer",
                "instruction": "The answer is saved. Retry record_candidate_answer with the same event_id and transcript.",
            })
        return result


@mcp.tool()
async def finish_interview(session_id: str) -> dict:
    """Finish the interview from progressively collected background evaluations."""
    tool_started = time.perf_counter()
    try:
        with _sessions_lock:
            pair = _sessions.get(session_id)
        if pair is None:
            raise ValueError("Interview session was not found")
        service, model = pair
        service = await _prepare_session_routes(session_id)
        test_run = _test_log_for(session_id)
        test_run_summary = None
        diagnostic_warning = None
        _begin_test_call(session_id)
        validation_started = time.perf_counter()
        session = await service.finish(session_id)
        validation_duration = (time.perf_counter() - validation_started) * 1000
        if test_run:
            _append_test_event(session_id, "background_wait", duration_ms=validation_duration)
            _append_test_event(session_id, "report_validation", duration_ms=0)
        markdown_started = time.perf_counter()
        report_markdown = render_report_markdown(session.report, session.turns, level=session.level)
        markdown_duration = (time.perf_counter() - markdown_started) * 1000
        if test_run:
            _append_test_event(session_id, "markdown_render", duration_ms=markdown_duration)
        pdf_started = time.perf_counter()
        try:
            files = await asyncio.to_thread(
                export_report_files,
                session.report,
                session.turns,
                session.id,
                output_dir=REPORTS_DIR,
                markdown=report_markdown,
                level=session.level,
            )
            report_files = {"markdown_path": files["markdown_path"], "pdf_path": files["pdf_path"]}
            export_error = None
        except ReportExportError as exc:
            report_files = None
            export_error = str(exc)
            if test_run:
                _append_test_event(session_id, "pdf_export_failed", details={"error_category": "report_export"})
        pdf_duration = (time.perf_counter() - pdf_started) * 1000
        if test_run:
            _append_test_event(session_id, "pdf_export", duration_ms=pdf_duration)
            with _sessions_lock:
                first_finish = session_id not in _test_run_ended
                if first_finish:
                    _test_run_ended.add(session_id)
            _complete_test_call(session_id, tool_started)
            run_end_warning = None
            if first_finish:
                run_end_warning = _append_test_event(
                    session_id,
                    "run_ended",
                    details={"status": session.status, "export_succeeded": report_files is not None},
                )
            run_id, _ = test_run
            log_path = str((Path(TEST_RUNS_DIR) / f"{run_id}.jsonl").resolve())
            diagnostic_warning = run_end_warning
            try:
                test_run_summary = summarize_events(
                    Path(log_path).read_text(encoding="utf-8").splitlines()
                )
            except (OSError, UnicodeError) as exc:
                diagnostic_warning = diagnostic_warning or f"Local test timing summary could not be read: {exc}"
        else:
            log_path = None
        return {
            "ok": True,
            **_public_session(session, include_transcript=False),
            "interview_complete": session.status == "completed",
            "report_markdown": report_markdown,
            "report_files": report_files,
            "report_export_error": export_error,
            "test_run_log_path": log_path,
            "test_run_summary": test_run_summary,
            "diagnostic_warning": diagnostic_warning,
            "instruction": (
                "Tell the candidate clearly in their language that the interview is complete "
                "(for example, say ‘Интервью завершено’), display report_markdown visibly in "
                "the chat, and provide the Markdown and PDF report_files. If report_export_error "
                "is present, still display the validated Markdown report and clearly explain "
                "that the file exports could not be saved. If recommendation is insufficient_data, "
                "explain that evidence was limited. If test_run_summary is present, summarize its "
                "timings and state that client_gap includes speech, recognition, and client work, "
                "not model thinking time. Do not claim a hiring decision."
            ),
        }
    except (InterviewError, ValueError) as exc:
        _fail_test_call(session_id, tool_started, "report_validation", exc)
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
            route_task = _route_tasks.pop(session_id, None)
            is_test_run = session_id in _test_runs
        if is_test_run:
            _append_test_event(session_id, "run_ended", details={"status": "cancelled"})
            with _sessions_lock:
                _test_runs.pop(session_id, None)
                _test_last_call_end.pop(session_id, None)
                _test_run_ended.discard(session_id)
                _test_logged_answers.pop(session_id, None)
        if route_task is not None and not route_task.done():
            route_task.cancel()
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
        route_task = _route_tasks.pop(session_id, None)
        if route_task is not None and not route_task.done():
            route_task.cancel()
        service, _ = pair
        await service.delete(session_id)
        with _sessions_lock:
            _test_runs.pop(session_id, None)
            _test_last_call_end.pop(session_id, None)
            _test_run_ended.discard(session_id)
            _test_logged_answers.pop(session_id, None)
        return {"ok": True, "deleted": True}
    except (InterviewError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def run_server():
    TestRunLog(root=TEST_RUNS_DIR).prune_expired_logs()
    mcp.run(transport="stdio")


@mcp.tool()
def clear_test_run_logs() -> dict:
    """Delete local test-interview logs without touching reports or voice-probe logs."""
    try:
        deleted = TestRunLog(root=TEST_RUNS_DIR).clear_test_run_logs()
        return {"ok": True, "deleted_logs": deleted}
    except OSError as exc:
        return {"ok": False, "error": f"Could not clear test-run logs: {exc}"}


if __name__ == "__main__":
    run_server()
