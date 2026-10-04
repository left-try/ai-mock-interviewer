"""Deterministic provider fakes used by the MVP contract tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeModel:
    """Queue-based fake; production code is expected to request structured values.

    Each call records the messages and structured-output schema request. Queued
    values are returned verbatim so malformed-provider responses can be tested.
    An empty queue is a test failure, never a default answer.
    """

    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _queue: deque[Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._queue = deque(self.responses)

    async def ainvoke(self, messages: Any, *, response_schema: Any = None, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "response_schema": response_schema,
                "kwargs": kwargs,
            }
        )
        if not self._queue:
            raise AssertionError("FakeModel exhausted: enqueue the expected model response")
        value = self._queue.popleft()
        if isinstance(value, BaseException):
            raise value
        return value


@dataclass
class FakeResumeParser:
    """Resume parser fake that makes file parsing deterministic and inspectable."""

    text: str | None = "Backend intern; Python project: Study API"
    error: Exception | None = None
    calls: list[bytes] = field(default_factory=list)

    async def extract_text(self, content: bytes, *, filename: str) -> str:
        self.calls.append(content)
        if self.error:
            raise self.error
        if self.text is None:
            raise ValueError("No extractable text")
        return self.text


def next_question(topic: str, text: str, *, kind: str = "question", evidence: list | None = None) -> dict:
    """Build the provider's documented next-turn response contract."""
    return {
        "kind": kind,
        "topic_id": topic,
        "text": text,
        "evidence": evidence or [],
        "confidence": 0.0,
    }


def report(
    *,
    recommendation: str = "mixed_signal",
    evidence: list | None = None,
    scores: dict | None = None,
) -> dict:
    """Build the provider's documented report contract."""
    return {
        "recommendation": recommendation,
        "scores": scores or {},
        "strengths": [],
        "growth_areas": [],
        "evidence": evidence or [],
        "uncertainties": [],
        "disclaimer": "Учебная обратная связь, не решение о найме.",
    }
