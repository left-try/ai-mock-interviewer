from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mock_interviewer.background_evaluation import BackgroundEvaluationQueue
from mock_interviewer.domain.models import InterviewContextState, Turn


class EvaluationModel:
    def __init__(self, values, *, delay=0, fail=False):
        self.values = values
        self.delay = delay
        self.fail = fail
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()

    async def create_structured_response(self, **_kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("provider unavailable")
            return SimpleNamespace(value=self.values.pop(0))
        finally:
            self.active -= 1


def _evaluation(turn_id, quote):
    return {
        "scores": {"communication": 4}, "strengths": ["Concrete example"],
        "growth_areas": [], "uncertainties": [],
        "evidence": [{"criterion": "communication", "source_turn_id": turn_id,
                      "quote": quote, "observation": "Explained the implementation."}],
    }


@pytest.mark.asyncio
async def test_background_evaluation_runs_without_blocking_next_question():
    model = EvaluationModel([_evaluation("turn-2", "I wrote the API")], delay=0.05)
    queue = BackgroundEvaluationQueue(model=model)
    turn = Turn("turn-2", "candidate", "I wrote the API")

    queue.enqueue("session", turn, InterviewContextState(), question="What did you build?")
    await model.started.wait()

    assert model.active == 1
    assert queue.pending_count("session") == 1
    results = await queue.finish_session("session")
    assert results[0].turn_id == "turn-2"


@pytest.mark.asyncio
async def test_out_of_order_evaluations_are_sorted_by_turn():
    model = EvaluationModel([_evaluation("turn-4", "second"), _evaluation("turn-2", "first")])
    queue = BackgroundEvaluationQueue(model=model)
    queue.enqueue("session", Turn("turn-2", "candidate", "first"), InterviewContextState())
    queue.enqueue("session", Turn("turn-4", "candidate", "second"), InterviewContextState())

    results = await queue.finish_session("session")

    assert [item.turn_id for item in results] == ["turn-2", "turn-4"]


@pytest.mark.asyncio
async def test_failed_evaluation_does_not_fail_interview():
    queue = BackgroundEvaluationQueue(model=EvaluationModel([], fail=True))
    queue.enqueue("session", Turn("turn-2", "candidate", "Answer"), InterviewContextState())

    results = await queue.finish_session("session")

    assert results == []
    assert queue.failures_for("session") == ["turn-2"]


@pytest.mark.asyncio
async def test_cancel_cleans_up_pending_tasks():
    model = EvaluationModel([_evaluation("turn-2", "Answer")], delay=30)
    queue = BackgroundEvaluationQueue(model=model)
    queue.enqueue("session", Turn("turn-2", "candidate", "Answer"), InterviewContextState())
    await model.started.wait()

    await queue.cancel_session("session")

    assert queue.pending_count("session") == 0
    assert queue.failures_for("session") == []


@pytest.mark.asyncio
async def test_evidence_quote_must_match_exact_candidate_turn():
    queue = BackgroundEvaluationQueue(model=EvaluationModel([_evaluation("turn-2", "invented quote")]))
    queue.enqueue("session", Turn("turn-2", "candidate", "The original answer"), InterviewContextState())

    result = (await queue.finish_session("session"))[0]

    assert result.evidence == []
    assert any("quote" in item.lower() for item in result.uncertainties)


@pytest.mark.asyncio
async def test_background_evaluation_respects_concurrency_limit():
    model = EvaluationModel([_evaluation(f"turn-{i}", "answer") for i in range(2, 8)], delay=0.01)
    queue = BackgroundEvaluationQueue(model=model, max_concurrency=2)
    for i in range(2, 8):
        queue.enqueue("session", Turn(f"turn-{i}", "candidate", "answer"), InterviewContextState())

    results = await queue.finish_session("session")

    assert len(results) == 6
    assert model.max_active <= 2
