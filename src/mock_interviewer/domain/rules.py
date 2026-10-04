"""Pure validation and lifecycle rules."""

import re
import unicodedata


TRANSITIONS = {
    ("active", "submit_answer"), ("active", "finish"), ("active", "cancel"),
    ("awaiting_report", "retry_report"), ("awaiting_report", "cancel"),
}


def is_terminal_status(status: str) -> bool:
    return status in {"completed", "cancelled"}


def can_transition(status: str, event: str) -> bool:
    return (status, event) in TRANSITIONS


def validate_score(score):
    if score is None:
        return None
    if isinstance(score, bool) or not isinstance(score, int):
        raise TypeError("Score must be an integer from 1 to 5 or None")
    if not 1 <= score <= 5:
        raise ValueError("Score must be from 1 to 5")
    return score


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def quote_is_supported(quote: str, source: str) -> bool:
    needle, haystack = _normalize(quote), _normalize(source)
    return bool(needle and haystack and needle in haystack)


def is_duplicate_question(candidate: str, history) -> bool:
    normalized = _normalize(candidate)
    return bool(normalized and any(normalized == _normalize(item) for item in history))
