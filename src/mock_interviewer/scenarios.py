"""Backend screening variants by candidate experience level."""

from __future__ import annotations


LEVELS = {
    "internship": {
        "label": "Internship",
        "expectations": (
            "Treat this as an internship screening: focus on learning, fundamentals, coursework and personal or "
            "study projects. Do not require commercial experience, production ownership, or independent system design."
        ),
    },
    "junior": {
        "label": "Junior",
        "expectations": (
            "Treat this as a junior screening: look for sound backend fundamentals, a clear personal contribution, "
            "basic testing and debugging habits, and appropriate help-seeking. Do not require broad production "
            "ownership or senior-level architecture."
        ),
    },
    "middle": {
        "label": "Middle",
        "expectations": (
            "Treat this as a middle-level screening: probe independent delivery of backend features, trade-offs, "
            "reliability, testing, production experience where present, and collaboration. Do not require staff- or "
            "senior-level organizational scope."
        ),
    },
}


def normalize_level(level: str) -> str:
    """Return a supported level key or raise a user-facing validation error."""
    normalized = level.strip().casefold() if isinstance(level, str) else ""
    if normalized not in LEVELS:
        raise ValueError(f"Unknown interview level: {level}")
    return normalized


def level_label(level: str) -> str:
    return LEVELS[normalize_level(level)]["label"]


def level_expectations(level: str) -> str:
    return LEVELS[normalize_level(level)]["expectations"]
