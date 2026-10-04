"""Temporary MCP server for checking Codex Desktop Voice -> MCP delivery.

This probe is deliberately independent of the interview application. It accepts
only synthetic transcript text, records ordered turns locally, and provides a
read/reset path so a manual smoke test can verify what reached the MCP server.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer


SERVER_NAME = "interviewer-voice-probe"
DATA_DIR = Path(__file__).resolve().parents[1] / ".voice-probe"
EVENTS_FILE = DATA_DIR / "events.jsonl"

mcp = MCPServer(SERVER_NAME, version="0.1.0")
_lock = threading.RLock()


def _read_events() -> list[dict[str, Any]]:
    if not EVENTS_FILE.exists():
        return []
    events: list[dict[str, Any]] = []
    with EVENTS_FILE.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Probe event log is invalid at line {line_number}") from exc
            if isinstance(item, dict):
                events.append(item)
    return events


def _append_event(event: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _active_session(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "probe_started":
            ended = any(
                item.get("type") == "probe_ended"
                and item.get("session_id") == event.get("session_id")
                for item in events
            )
            if not ended:
                return event
    return None


@mcp.tool()
def start_voice_probe() -> dict[str, Any]:
    """Start a fresh probe session. Use synthetic speech, never a real resume."""
    with _lock:
        events = _read_events()
        active = _active_session(events)
        if active:
            return {
                "ok": False,
                "error": "probe_already_active",
                "session_id": active["session_id"],
                "instruction": "Continue the active probe or end it before starting another.",
            }

        session_id = str(uuid.uuid4())
        _append_event(
            {
                "type": "probe_started",
                "session_id": session_id,
                "at": _now(),
            }
        )
        return {
            "ok": True,
            "session_id": session_id,
            "recorded_turns": 0,
            "instruction": (
                "Voice probe started. Ask the user to say a short synthetic test sentence. "
                "After each completed user answer, call record_candidate_turn exactly once "
                "with the recognized words. Do not send real personal data or a real resume."
            ),
        }


@mcp.tool()
def record_candidate_turn(session_id: str, transcript: str, event_id: str) -> dict[str, Any]:
    """Record one finalized recognized utterance; duplicate event IDs are idempotent."""
    clean_text = transcript.strip()
    clean_event_id = event_id.strip()
    if not clean_text:
        return {"ok": False, "error": "empty_transcript", "recorded": False}
    if len(clean_text) > 4000:
        return {"ok": False, "error": "transcript_too_long", "recorded": False}
    if not clean_event_id or len(clean_event_id) > 128:
        return {"ok": False, "error": "invalid_event_id", "recorded": False}

    with _lock:
        events = _read_events()
        active = _active_session(events)
        if not active or active.get("session_id") != session_id:
            return {"ok": False, "error": "no_matching_active_probe", "recorded": False}

        for existing in events:
            if (
                existing.get("type") == "candidate_turn"
                and existing.get("session_id") == session_id
                and existing.get("event_id") == clean_event_id
            ):
                return {
                    "ok": True,
                    "recorded": False,
                    "duplicate": True,
                    "turn_number": existing["turn_number"],
                    "transcript_received": existing["transcript"],
                    "instruction": "This event was already recorded. Do not record or repeat it again.",
                }

        previous_turns = [
            event
            for event in events
            if event.get("type") == "candidate_turn"
            and event.get("session_id") == session_id
        ]
        turn_number = len(previous_turns) + 1
        entry = {
            "type": "candidate_turn",
            "session_id": session_id,
            "event_id": clean_event_id,
            "turn_number": turn_number,
            "at": _now(),
            "transcript": clean_text,
        }
        _append_event(entry)

    return {
        "ok": True,
        "recorded": True,
        "duplicate": False,
        "turn_number": turn_number,
        "transcript_received": clean_text,
        "instruction": (
            f"Recorded candidate turn {turn_number}. Tell the user it was recorded, "
            "then ask them for the next synthetic test sentence."
        ),
    }


@mcp.tool()
def get_voice_probe_status(session_id: str) -> dict[str, Any]:
    """Return the transcript log for the active or most recently ended probe."""
    with _lock:
        events = _read_events()
        started = next(
            (
                event
                for event in reversed(events)
                if event.get("type") == "probe_started"
                and event.get("session_id") == session_id
            ),
            None,
        )
        if not started:
            return {"ok": False, "error": "probe_not_found"}

        turns = [
            {
                "turn_number": event["turn_number"],
                "event_id": event["event_id"],
                "transcript": event["transcript"],
            }
            for event in events
            if event.get("type") == "candidate_turn"
            and event.get("session_id") == session_id
        ]
        ended = any(
            event.get("type") == "probe_ended" and event.get("session_id") == session_id
            for event in events
        )
        return {
            "ok": True,
            "session_id": session_id,
            "status": "ended" if ended else "active",
            "recorded_turns": len(turns),
            "turns": turns,
        }


@mcp.tool()
def end_voice_probe(session_id: str) -> dict[str, Any]:
    """End the active probe without generating an interview evaluation."""
    with _lock:
        events = _read_events()
        active = _active_session(events)
        if not active or active.get("session_id") != session_id:
            return {"ok": False, "error": "no_matching_active_probe"}
        turns = [
            event
            for event in events
            if event.get("type") == "candidate_turn"
            and event.get("session_id") == session_id
        ]
        _append_event(
            {
                "type": "probe_ended",
                "session_id": session_id,
                "at": _now(),
            }
        )
        return {
            "ok": True,
            "status": "ended",
            "recorded_turns": len(turns),
            "instruction": "Probe ended. Use get_voice_probe_status to inspect the recognized text.",
        }


@mcp.tool()
def clear_voice_probe_log() -> dict[str, Any]:
    """Delete all probe transcripts from this local test server."""
    with _lock:
        if EVENTS_FILE.exists():
            EVENTS_FILE.unlink()
    return {"ok": True, "deleted": True}


if __name__ == "__main__":
    # Keep the existing Codex MCP registration path stable while adding the
    # interview tools to the same stdio server.
    from interview_mcp import run_server

    run_server()
