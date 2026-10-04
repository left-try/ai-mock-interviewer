"""Small deterministic unit tests for domain rules (no graph or provider)."""

from __future__ import annotations

import pytest


def rules_module():
    try:
        from mock_interviewer.domain import rules

        return rules
    except ModuleNotFoundError as exc:
        if exc.name == "mock_interviewer" or (exc.name and exc.name.startswith("mock_interviewer.")):
            pytest.fail(
                "MVP implementation is not present yet. Implement mock_interviewer.domain.rules.",
                pytrace=False,
            )
        raise


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_completed_and_cancelled_are_terminal_statuses(status):
    assert rules_module().is_terminal_status(status)


@pytest.mark.parametrize("status", ["active", "awaiting_report"])
def test_nonterminal_lifecycle_statuses_are_not_terminal(status):
    assert not rules_module().is_terminal_status(status)


@pytest.mark.parametrize(
    ("status", "event", "allowed"),
    [
        ("active", "submit_answer", True),
        ("active", "finish", True),
        ("active", "cancel", True),
        ("awaiting_report", "retry_report", True),
        ("awaiting_report", "cancel", True),
        ("completed", "submit_answer", False),
        ("completed", "finish", False),
        ("cancelled", "submit_answer", False),
        ("cancelled", "finish", False),
        ("unknown", "submit_answer", False),
        ("active", "run_shell", False),
    ],
)
def test_lifecycle_transition_table_is_explicit(status, event, allowed):
    assert rules_module().can_transition(status, event) is allowed


@pytest.mark.parametrize("score", [1, 2, 3, 4, 5, None])
def test_score_validation_accepts_documented_five_point_scale_and_missing(score):
    assert rules_module().validate_score(score) == score


@pytest.mark.parametrize("score", [0, 6, -1, 1.5, "5", True])
def test_score_validation_rejects_out_of_range_or_noninteger_values(score):
    with pytest.raises((ValueError, TypeError)):
        rules_module().validate_score(score)


@pytest.mark.parametrize(
    ("quote", "source", "supported"),
    [
        ("Python API", "Built a Python API for a class project", True),
        ("python   api", "Built a Python API for a class project", True),
        ("production Kubernetes", "Built a Python API for a class project", False),
        ("", "Built a Python API for a class project", False),
        ("Python API", "", False),
    ],
)
def test_evidence_quote_must_be_present_in_its_claimed_source(quote, source, supported):
    assert rules_module().quote_is_supported(quote, source) is supported


@pytest.mark.parametrize(
    ("candidate", "history", "duplicate"),
    [
        ("Why backend?", ["Why backend?"], True),
        (" WHY   BACKEND? ", ["Why backend?"], True),
        ("What did you build?", ["Why backend?"], False),
        ("", ["Why backend?"], False),
    ],
)
def test_exact_normalized_duplicate_detection(candidate, history, duplicate):
    assert rules_module().is_duplicate_question(candidate, history) is duplicate
