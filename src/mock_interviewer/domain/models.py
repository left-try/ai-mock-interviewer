"""Public session/report data objects."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    id: str
    role: str
    text: str
    topic_id: str | None = None
    kind: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InterviewReport:
    recommendation: str
    scores: dict[str, int | None]
    strengths: list[str]
    growth_areas: list[str]
    evidence: list[Any]
    uncertainties: list[str]
    disclaimer: str


@dataclass
class InterviewSession:
    id: str
    status: str
    resume_text: str
    turns: list[Turn] = field(default_factory=list)
    report: InterviewReport | None = None
    event_results: dict[str, "InterviewSession"] = field(default_factory=dict, repr=False)
    follow_ups: dict[str, int] = field(default_factory=dict, repr=False)
    version: int = 0


@dataclass
class NextTurn:
    kind: str
    topic_id: str
    text: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0

