"""Prompt contracts for competency-led interviewing and evidence-based reports."""

from mock_interviewer.domain.models import Turn
from mock_interviewer.prompts import interview_messages, report_messages


def test_interviewer_prompt_maps_topics_to_signals_and_adaptive_probes():
    system_prompt = interview_messages("Python API project", [])[0]["content"]

    assert "Candidate level: Internship" in system_prompt
    assert "what the candidate personally owned" in system_prompt
    assert "how the result was observed or measured" in system_prompt
    assert "reasons behind decisions, trade-offs, observable impact" in system_prompt
    for topic in (
        "motivation",
        "education",
        "project",
        "personal_contribution",
        "teamwork",
        "challenge",
        "reflection",
        "expectations",
    ):
        assert f"- {topic}:" in system_prompt


def test_report_prompt_scores_criteria_independently_and_requests_actionable_feedback():
    candidate_turn = Turn(id="answer-1", role="candidate", text="I implemented the API routes.")
    system_prompt, user_prompt = report_messages(
        "Python API project", [candidate_turn]
    )

    assert "do not collapse the report into a single overall score" in system_prompt["content"]
    assert "specific, actionable coaching" in system_prompt["content"]
    assert "self_presentation" in user_prompt["content"]
    assert "Clear ownership of the candidate's own decisions and actions." in user_prompt["content"]
