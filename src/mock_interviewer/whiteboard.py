"""Vendor-neutral whiteboard port and local Excalidraw event adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from spikes.system_design_whiteboard_probe.store import BoardEventStore


@dataclass(frozen=True)
class WhiteboardStatus:
    connected: bool
    source: str
    detail: str | None = None


class WhiteboardAdapter(Protocol):
    """Interview-facing boundary; implementations return normalized event deltas."""

    def connect(self, session_id: str, *, consent: bool) -> WhiteboardStatus: ...

    def read_changes(self, session_id: str, *, cursor: int, limit: int = 100) -> dict[str, Any]: ...

    def capture_snapshot(self, session_id: str) -> dict[str, Any] | None: ...

    def disconnect(self, session_id: str) -> None: ...

    def delete_session(self, session_id: str) -> None: ...


class ExcalidrawWhiteboardAdapter:
    """Maps the local Excalidraw transport store to the neutral event port."""

    source = "excalidraw"

    def __init__(self, store: BoardEventStore, *, ui_available: bool, unavailable_reason: str = "excalidraw_ui_not_built"):
        self.store = store
        self.ui_available = ui_available
        self.unavailable_reason = unavailable_reason

    def connect(self, session_id: str, *, consent: bool) -> WhiteboardStatus:
        if not consent:
            return WhiteboardStatus(connected=False, source=self.source, detail="consent_not_given")
        if not self.ui_available:
            return WhiteboardStatus(connected=False, source=self.source, detail=self.unavailable_reason)
        # Validate and authorize this one explicitly consented session identifier.
        self.store.open_session(session_id)
        return WhiteboardStatus(connected=True, source=self.source)

    def read_changes(self, session_id: str, *, cursor: int, limit: int = 100) -> dict[str, Any]:
        return self.store.read_changes(session_id, after_cursor=cursor, limit=limit)

    def capture_snapshot(self, session_id: str) -> dict[str, Any] | None:
        # The first version records structured deltas; it does not store image snapshots.
        self.store.read_changes(session_id, after_cursor=0, limit=1)
        return None

    def disconnect(self, session_id: str) -> None:
        # The adapter is stateless; disabling it stops new reads while preserving
        # already captured evidence for the final report.
        self.store.close_session(session_id)

    def delete_session(self, session_id: str) -> None:
        self.store.delete_session(session_id)
