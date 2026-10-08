"""Bounded per-answer analysis that never blocks live interview turns."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .domain.models import InterviewContextState, Turn

ALLOWED_CRITERIA = frozenset({
    "self_presentation", "motivation", "personal_contribution", "communication",
    "reflection", "consistency", "teamwork", "challenge",
})
EVALUATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scores": {"type": "object", "additionalProperties": {"type": ["integer", "null"]}},
        "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"criterion": {"type": "string"}, "source_turn_id": {"type": "string"},
                           "quote": {"type": "string"}, "observation": {"type": "string"}},
            "required": ["criterion", "source_turn_id", "quote", "observation"]}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "growth_areas": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scores", "evidence", "strengths", "growth_areas", "uncertainties"],
}


@dataclass
class AnswerEvaluation:
    turn_id: str
    scores: dict[str, int | None] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    growth_areas: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)


class BackgroundEvaluationQueue:
    def __init__(self, *, model, max_concurrency: int = 2, timeout: float = 2.0):
        if max_concurrency < 1 or timeout <= 0:
            raise ValueError("max_concurrency and timeout must be positive")
        self.model = model
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: dict[str, dict[str, asyncio.Task]] = {}
        self._results: dict[str, dict[str, AnswerEvaluation]] = {}
        self._failures: dict[str, set[str]] = {}

    def enqueue(self, session_id: str, candidate_turn: Turn, context: InterviewContextState,
                *, question: str = "", rubric: dict | None = None) -> None:
        session_tasks = self._tasks.setdefault(session_id, {})
        if candidate_turn.id in session_tasks or candidate_turn.id in self._results.get(session_id, {}):
            return
        task = asyncio.create_task(self._evaluate_and_store(
            session_id, candidate_turn, context, question=question, rubric=rubric or {},
        ))
        session_tasks[candidate_turn.id] = task

    async def finish_session(self, session_id: str) -> list[AnswerEvaluation]:
        tasks = self._tasks.get(session_id, {})
        outstanding = [task for task in tasks.values() if not task.done()]
        if outstanding:
            done, pending = await asyncio.wait(outstanding, timeout=self.timeout)
            for task in pending:
                task.cancel()
                turn_id = next((key for key, value in tasks.items() if value is task), "unknown")
                self._failures.setdefault(session_id, set()).add(turn_id)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        return sorted(self._results.get(session_id, {}).values(), key=lambda item: _turn_order(item.turn_id))

    async def cancel_session(self, session_id: str) -> None:
        tasks = list(self._tasks.pop(session_id, {}).values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._results.pop(session_id, None)
        self._failures.pop(session_id, None)

    def failures_for(self, session_id: str) -> list[str]:
        return sorted(self._failures.get(session_id, set()), key=_turn_order)

    def pending_count(self, session_id: str) -> int:
        return sum(not task.done() for task in self._tasks.get(session_id, {}).values())

    async def _evaluate_and_store(self, session_id, turn, context, *, question, rubric):
        async with self._semaphore:
            try:
                result = await self.model.create_structured_response(
                    input=_messages(turn, context, question=question, rubric=rubric),
                    schema=EVALUATION_SCHEMA,
                    max_output_tokens=1200,
                )
                value = result.value if hasattr(result, "value") else result
                evaluation = _validate_evaluation(value, turn)
                self._results.setdefault(session_id, {})[turn.id] = evaluation
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failures.setdefault(session_id, set()).add(turn.id)


def _validate_evaluation(value, turn: Turn) -> AnswerEvaluation:
    if not isinstance(value, dict):
        raise ValueError("Evaluation response must be an object")
    raw_scores = value.get("scores") or {}
    if not isinstance(raw_scores, dict):
        raise ValueError("Evaluation scores must be an object")
    scores = {}
    for criterion, score in raw_scores.items():
        if criterion not in ALLOWED_CRITERIA:
            continue
        if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5):
            continue
        scores[criterion] = score
    uncertainties = _string_list(value.get("uncertainties"))
    evidence = []
    for item in value.get("evidence", []):
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        if (item.get("source_turn_id") != turn.id or not isinstance(quote, str) or not quote.strip()
                or quote not in turn.text):
            uncertainties.append(f"Evidence quote for {turn.id} did not match the exact candidate answer and was omitted.")
            continue
        if item.get("criterion") not in ALLOWED_CRITERIA:
            continue
        observation = item.get("observation")
        if not isinstance(observation, str) or not observation.strip():
            continue
        evidence.append({"criterion": item["criterion"], "source_turn_id": turn.id,
                         "quote": quote, "observation": observation.strip()})
    return AnswerEvaluation(
        turn_id=turn.id, scores=scores, evidence=evidence,
        strengths=_string_list(value.get("strengths")),
        growth_areas=_string_list(value.get("growth_areas")), uncertainties=uncertainties,
    )


def _messages(turn, context, *, question, rubric):
    instructions = (
        "Evaluate only this one candidate answer for training feedback. Do not invent evidence or quote from "
        "context. Scores are integers 1..5 or null and use only allowed report criteria. Any evidence quote "
        "must be an exact substring of the candidate_answer_exact and source_turn_id must match its turn id. "
        "Return scores, evidence, strengths, growth_areas, uncertainties."
    )
    payload = {
        "question": question,
        "candidate_turn_id": turn.id,
        "candidate_answer_exact": turn.text,
        "compact_context": {
            "candidate_facts": context.candidate_facts,
            "covered_topics": context.covered_topics,
            "open_threads": context.open_threads,
        },
        "rubric": rubric,
        "allowed_criteria": sorted(ALLOWED_CRITERIA),
    }
    return [{"role": "developer", "content": instructions},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]


def _string_list(value):
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _turn_order(turn_id: str) -> int:
    try:
        return int(turn_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0
