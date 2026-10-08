"""Deterministically aggregate validated per-answer evaluations into a practice report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .background_evaluation import ALLOWED_CRITERIA, AnswerEvaluation
from .domain.models import InterviewReport, Turn

DISCLAIMER = "Учебная обратная связь для практики; это не решение о найме."


def aggregate_report(
    evaluations: Sequence[AnswerEvaluation],
    turns: Sequence[Turn],
    *,
    failures: Sequence[str] = (),
) -> InterviewReport:
    source_turns = {turn.id: turn.text for turn in turns if turn.role == "candidate"}
    ordered = sorted(evaluations, key=lambda item: _turn_order(item.turn_id))
    uncertainties: list[str] = []
    if failures:
        for turn_id in sorted(set(failures), key=_turn_order):
            uncertainties.append(f"Answer evaluation for {turn_id} did not complete.")
    evaluated_ids = {item.turn_id for item in ordered}
    for turn_id in sorted(set(source_turns) - evaluated_ids - set(failures), key=_turn_order):
        uncertainties.append(f"Answer evaluation for {turn_id} is missing.")

    scores_by_criterion: dict[str, list[int]] = defaultdict(list)
    evidence_by_key = {}
    strengths: list[str] = []
    growth_areas: list[str] = []
    for evaluation in ordered:
        uncertainties.extend(evaluation.uncertainties)
        for criterion, score in evaluation.scores.items():
            if criterion in ALLOWED_CRITERIA and isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5:
                scores_by_criterion[criterion].append(score)
        strengths.extend(evaluation.strengths)
        growth_areas.extend(evaluation.growth_areas)
        for item in evaluation.evidence:
            criterion = item.get("criterion")
            source_id = item.get("source_turn_id")
            quote = item.get("quote")
            observation = item.get("observation")
            if (criterion not in ALLOWED_CRITERIA or source_id not in source_turns or not isinstance(quote, str)
                    or not quote or quote not in source_turns[source_id] or not isinstance(observation, str)
                    or not observation.strip()):
                uncertainties.append(f"An unsupported evidence quote for {evaluation.turn_id} was omitted.")
                continue
            key = (criterion, source_id, quote)
            evidence_by_key.setdefault(key, {
                "criterion": criterion, "source_turn_id": source_id,
                "quote": quote, "observation": observation.strip(),
            })

    scores = {criterion: int(sum(values) / len(values) + 0.5)
              for criterion, values in sorted(scores_by_criterion.items()) if values}
    observed = [score for values in scores_by_criterion.values() for score in values]
    if len(source_turns) < 2 or not observed:
        recommendation = "insufficient_data"
        scores = {criterion: None for criterion in scores}
        uncertainties.append("Недостаточно подтверждённых ответов для надёжной общей оценки.")
    elif sum(observed) / len(observed) >= 4:
        recommendation = "strong_signal"
    else:
        recommendation = "mixed_signal"

    evidence = sorted(evidence_by_key.values(), key=lambda item: (
        _turn_order(item["source_turn_id"]), item["criterion"], item["quote"],
    ))
    return InterviewReport(
        recommendation=recommendation,
        scores=scores,
        strengths=_unique(strengths),
        growth_areas=_unique(growth_areas),
        evidence=evidence,
        uncertainties=_unique(uncertainties),
        disclaimer=DISCLAIMER,
    )


def _turn_order(turn_id: str) -> int:
    try:
        return int(turn_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _unique(values):
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))
