"""Shared Backend Screening coverage and progression rules."""

from __future__ import annotations

REQUIRED_TOPICS = (
    "motivation",
    "education",
    "project",
    "personal_contribution",
    "teamwork",
    "challenge",
    "reflection",
    "expectations",
)

# A compact competency map keeps the interview grounded in signals instead of
# growing an unbounded question bank. These guides inform adaptive probes; they
# are not a checklist to recite to the candidate.
TOPIC_SIGNALS = {
    "motivation": "role relevance, intentionality, concise self-positioning",
    "education": "fundamentals, learning habits, and relevant coursework or personal work",
    "project": "problem, constraints, architecture, decisions, trade-offs, outcomes, and reflection",
    "personal_contribution": "what the candidate personally designed, implemented, proposed, or debugged",
    "teamwork": "communication, disagreement, collaboration, and influence appropriate to level",
    "challenge": "problem solving, accountability, action taken, outcome, and learning",
    "reflection": "calibration, lessons learned, and what the candidate would change",
    "expectations": "role understanding, candidate priorities, and thoughtful questions",
}

FOLLOW_UP_PLAYBOOK = {
    "vague_claim": "Ask for one concrete example or observable detail.",
    "resume_claim": "Select one material resume claim and ask what the candidate personally owned.",
    "technology_choice": "Ask why that option fit the constraints, what alternative was considered, and the trade-off.",
    "team_claim": "Clarify the candidate's own actions separately from what the team did.",
    "impact_claim": "Ask what changed and how the result was observed or measured.",
    "design_claim": "Probe a relevant boundary, failure mode, or changed constraint.",
    "uncertainty": "Allow the candidate to state what they do not know, then assess their reasoning and how they would verify it.",
}

MIN_CANDIDATE_ANSWERS = 8
MAX_CANDIDATE_ANSWERS = 10


def interview_progress(turns, *, pending_answer: bool = False) -> dict:
    """Summarize answer count and topic coverage from an ordered transcript.

    When ``pending_answer`` is true, include the candidate answer about to be
    recorded against the most recent interviewer topic. This lets the MCP
    bridge end the interview without appending an unspoken follow-up question.
    """
    answer_count = 0
    covered: set[str] = set()
    active_topic = None

    for turn in turns:
        role = getattr(turn, "role", None)
        if role == "interviewer":
            active_topic = getattr(turn, "topic_id", None)
        elif role == "candidate":
            answer_count += 1
            if active_topic in REQUIRED_TOPICS:
                covered.add(active_topic)

    if pending_answer:
        answer_count += 1
        if active_topic in REQUIRED_TOPICS:
            covered.add(active_topic)

    covered_topics = [topic for topic in REQUIRED_TOPICS if topic in covered]
    missing_topics = [topic for topic in REQUIRED_TOPICS if topic not in covered]
    ready = answer_count >= MAX_CANDIDATE_ANSWERS or (
        answer_count >= MIN_CANDIDATE_ANSWERS and not missing_topics
    )
    return {
        "candidate_answers": answer_count,
        "minimum_answers": MIN_CANDIDATE_ANSWERS,
        "maximum_answers": MAX_CANDIDATE_ANSWERS,
        "required_topics": list(REQUIRED_TOPICS),
        "covered_topics": covered_topics,
        "missing_topics": missing_topics,
        "ready_to_finish": ready,
    }
