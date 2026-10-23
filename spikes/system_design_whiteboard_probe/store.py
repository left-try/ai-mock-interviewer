from __future__ import annotations

import json
import math
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_ALLOWED_KINDS = {
    "node_added",
    "node_updated",
    "node_moved",
    "node_resized",
    "node_deleted",
    "edge_added",
    "edge_updated",
    "edge_deleted",
    "note_added",
    "note_updated",
    "note_moved",
    "note_deleted",
}
_REQUIRED_FIELDS = {
    "node_added": {"node_id", "label"},
    "node_updated": {"node_id", "label"},
    "node_moved": {"node_id", "x", "y"},
    "node_resized": {"node_id", "width", "height"},
    "node_deleted": {"node_id"},
    "edge_added": {"edge_id", "source_id", "target_id"},
    "edge_updated": {"edge_id"},
    "edge_deleted": {"edge_id"},
    "note_added": {"note_id", "text", "x", "y"},
    "note_updated": {"note_id", "text"},
    "note_moved": {"note_id", "x", "y"},
    "note_deleted": {"note_id"},
}
_ALLOWED_FIELDS = {
    "node_added": {"node_id", "label", "x", "y", "width", "height", "angle", "shape_type"},
    "node_updated": {"node_id", "label"},
    "node_moved": {"node_id", "x", "y"},
    "node_resized": {"node_id", "width", "height", "angle"},
    "node_deleted": {"node_id"},
    "edge_added": {"edge_id", "source_id", "target_id", "label"},
    "edge_updated": {"edge_id", "source_id", "target_id", "label"},
    "edge_deleted": {"edge_id"},
    "note_added": {"note_id", "text", "x", "y"},
    "note_updated": {"note_id", "text"},
    "note_moved": {"note_id", "x", "y"},
    "note_deleted": {"note_id"},
}


class BoardEventStore:
    """Small local-only event log for the disposable whiteboard spike."""

    def __init__(
        self,
        root: Path,
        *,
        max_payload_bytes: int = 16_384,
        max_events_per_session: int = 1_000,
        require_open_session: bool = False,
    ):
        self.root = Path(root)
        self.max_payload_bytes = max_payload_bytes
        self.max_events_per_session = max_events_per_session
        self.require_open_session = require_open_session
        self._lock = threading.RLock()
        self._closed_sessions: set[str] = set()
        self._open_sessions: set[str] = set()

    def append_event(
        self,
        *,
        session_id: str,
        event_id: str,
        client_sequence: int,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        path = self._event_path(session_id)
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
            raise ValueError("event_id must be a non-empty string of at most 128 characters")
        if isinstance(client_sequence, bool) or not isinstance(client_sequence, int) or client_sequence < 1:
            raise ValueError("client sequence must be a positive integer")
        self._validate_payload(kind, payload)
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(payload_json.encode("utf-8")) > self.max_payload_bytes:
            raise ValueError("payload exceeds the configured size limit")

        with self._lock:
            if session_id in self._closed_sessions:
                raise ValueError("whiteboard session is closed")
            if self.require_open_session and session_id not in self._open_sessions:
                raise ValueError("whiteboard session is not active")
            events = self._read_file(path)
            for event in events:
                if event["event_id"] == event_id:
                    if (
                        event["client_sequence"] == client_sequence
                        and event["kind"] == kind
                        and event["payload"] == payload
                    ):
                        return event
                    raise ValueError("event_id cannot be reused with a different payload")

            expected = events[-1]["client_sequence"] + 1 if events else 1
            if len(events) >= self.max_events_per_session:
                raise ValueError("whiteboard session event limit reached")
            if client_sequence != expected:
                raise ValueError(f"client sequence must be {expected}; received {client_sequence}")

            event = {
                "event_id": event_id,
                "sequence": len(events) + 1,
                "client_sequence": client_sequence,
                "kind": kind,
                "payload": payload,
                "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
                stream.flush()
            return event

    def read_changes(
        self,
        session_id: str,
        *,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        path = self._event_path(session_id)
        if isinstance(after_cursor, bool) or not isinstance(after_cursor, int) or after_cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        events = self._read_file(path)
        remaining = [event for event in events if event["sequence"] > after_cursor]
        changes = remaining[:limit]
        next_cursor = changes[-1]["sequence"] if changes else after_cursor
        return {"events": changes, "next_cursor": next_cursor, "has_more": len(remaining) > len(changes)}

    def delete_session(self, session_id: str) -> bool:
        """Remove one session's local event log without traversing its data root."""
        path = self._event_path(session_id)
        with self._lock:
            # Keep a process-local tombstone so an open browser tab cannot recreate
            # the deleted log by posting after session cleanup.
            self._closed_sessions.add(session_id)
            self._open_sessions.discard(session_id)
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

    def close_session(self, session_id: str) -> None:
        """Reject further writes while preserving captured events for reporting."""
        self._event_path(session_id)
        with self._lock:
            self._closed_sessions.add(session_id)
            self._open_sessions.discard(session_id)

    def open_session(self, session_id: str) -> None:
        """Authorize writes for one explicitly consented interview session."""
        self._event_path(session_id)
        with self._lock:
            if session_id in self._closed_sessions:
                raise ValueError("whiteboard session is closed")
            self._open_sessions.add(session_id)

    def _event_path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError("session_id must contain only letters, digits, '_' or '-' and be at most 64 characters")
        return self.root / f"{session_id}.jsonl"

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        result = []
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"event log is invalid at line {line_number}") from exc
                if not isinstance(item, dict):
                    raise RuntimeError(f"event log entry at line {line_number} is not an object")
                result.append(item)
        return result

    @staticmethod
    def _validate_payload(kind: str, payload: dict[str, Any]) -> None:
        if kind not in _ALLOWED_KINDS:
            raise ValueError("unknown event kind")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        keys = set(payload)
        if not _REQUIRED_FIELDS[kind].issubset(keys) or not keys.issubset(_ALLOWED_FIELDS[kind]):
            raise ValueError("payload fields do not match event kind")
        if kind == "node_updated" and not keys.intersection({"label"}):
            raise ValueError("node_updated requires at least one changed field")
        if kind == "edge_updated" and not keys.intersection({"source_id", "target_id", "label"}):
            raise ValueError("edge_updated requires at least one changed field")
        if kind == "note_updated" and not keys.intersection({"text"}):
            raise ValueError("note_updated requires a changed field")
        for field in ("node_id", "edge_id", "source_id", "target_id", "note_id", "label", "text"):
            if field in payload and (
                not isinstance(payload[field], str)
                or not payload[field].strip()
                or len(payload[field]) > 200
            ):
                raise ValueError(f"{field} must be a non-empty string of at most 200 characters")
        for field in ("x", "y", "width", "height", "angle"):
            if field in payload and (
                isinstance(payload[field], bool)
                or not isinstance(payload[field], (int, float))
                or not math.isfinite(payload[field])
                or abs(payload[field]) > 100_000
            ):
                raise ValueError(f"{field} must be a finite number in range")
        if "shape_type" in payload and payload["shape_type"] not in {"rectangle", "ellipse", "diamond"}:
            raise ValueError("shape_type must be a supported Excalidraw shape")
