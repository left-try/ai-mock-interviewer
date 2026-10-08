from __future__ import annotations

from mock_interviewer.domain.models import Turn
from mock_interviewer.report_aggregation import aggregate_report
from mock_interviewer.background_evaluation import AnswerEvaluation


def _evaluation(turn_id, *, score=4, quote="I built the API", strengths=None):
    return AnswerEvaluation(
        turn_id=turn_id,
        scores={"communication": score},
        evidence=[{"criterion": "communication", "source_turn_id": turn_id, "quote": quote,
                   "observation": "Gave a specific technical example."}],
        strengths=strengths or ["Clear example"], growth_areas=[], uncertainties=[],
    )


def test_report_aggregation_is_deterministic():
    turns = [Turn("turn-4", "candidate", "I built the API"), Turn("turn-2", "candidate", "I built the API")]
    evaluations = [_evaluation("turn-4"), _evaluation("turn-2")]

    first = aggregate_report(evaluations, turns)
    second = aggregate_report(list(reversed(evaluations)), turns)

    assert first == second
    assert [item["source_turn_id"] for item in first.evidence] == ["turn-2", "turn-4"]


def test_scores_average_only_observed_criteria():
    report = aggregate_report([_evaluation("turn-2", score=3), _evaluation("turn-4", score=4)], [
        Turn("turn-2", "candidate", "I built the API"), Turn("turn-4", "candidate", "I built the API"),
    ])

    assert report.scores == {"communication": 4}


def test_duplicate_evidence_is_removed():
    report = aggregate_report([_evaluation("turn-2"), _evaluation("turn-2")], [
        Turn("turn-2", "candidate", "I built the API"),
    ])

    assert len(report.evidence) == 1


def test_incomplete_evaluations_add_uncertainty():
    report = aggregate_report([_evaluation("turn-2")], [
        Turn("turn-2", "candidate", "I built the API"), Turn("turn-4", "candidate", "I tested it"),
    ], failures=["turn-4"])

    assert any("turn-4" in note for note in report.uncertainties)


def test_report_disclaimer_is_preserved():
    report = aggregate_report([_evaluation("turn-2")], [Turn("turn-2", "candidate", "I built the API")])

    assert report.disclaimer == "Учебная обратная связь для практики; это не решение о найме."


def test_evidence_quote_must_be_exactly_in_the_candidate_turn():
    report = aggregate_report([_evaluation("turn-2", quote="fabricated")], [
        Turn("turn-2", "candidate", "I built the API"),
    ])

    assert report.evidence == []
    assert any("quote" in note.lower() for note in report.uncertainties)
