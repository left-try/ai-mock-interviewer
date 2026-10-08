"""Local JSONL diagnostics for explicitly requested synthetic interview runs."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def default_test_runs_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ai-mock-interviewer" / "test-runs"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "ai-mock-interviewer" / "test-runs"


class TestRunLog:
    """Append-only per-run JSONL log with bounded retention and safe clearing."""

    def __init__(self, root: Path | str | None = None, *, retention_days: int = 30):
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self.root = Path(root) if root is not None else default_test_runs_dir()
        self.retention_days = retention_days
        self._monotonic = time.monotonic
        self._now = lambda: datetime.now(UTC)
        self._run_origins: dict[str, float] = {}
        self._lock = threading.RLock()

    def _path_for(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise ValueError("Invalid run_id")
        return self.root / f"{run_id}.jsonl"

    def append_event(
        self,
        run_id: str,
        event_name: str,
        *,
        session_id: str | None = None,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(event_name, str) or not event_name.strip() or len(event_name) > 80:
            raise ValueError("Invalid event_name")
        if duration_ms is not None and (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            raise ValueError("duration_ms must be a finite non-negative number")
        if details is not None and not isinstance(details, dict):
            raise ValueError("details must be an object")

        with self._lock:
            path = self._path_for(run_id)
            monotonic_now = self._monotonic()
            origin = self._run_origins.setdefault(run_id, monotonic_now)
            timestamp = self._now()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            event = {
                "run_id": run_id,
                "session_id": session_id,
                "event_name": event_name,
                "timestamp_utc": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "monotonic_offset_ms": max(0.0, (monotonic_now - origin) * 1000),
            }
            if duration_ms is not None:
                event["duration_ms"] = float(duration_ms)
            if details:
                event["details"] = details
            self.root.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                stream.write("\n")

    def append_timing_event(
        self,
        run_id: str,
        event_name: str,
        *,
        session_id: str | None = None,
        duration_ms: float,
        model_role: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Write an allowlisted timing record; never accept arbitrary content fields."""
        safe_details: dict[str, str | int] = {}
        for key, value in (
            ("model_role", model_role), ("model", model),
            ("reasoning_effort", reasoning_effort),
        ):
            if value is not None and isinstance(value, str) and len(value) <= 100:
                safe_details[key] = value
        for key, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if value is not None and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_details[key] = value
        self.append_event(
            run_id, event_name, session_id=session_id, duration_ms=duration_ms,
            details=safe_details,
        )

    def prune_expired_logs(self, now: datetime | None = None) -> list[Path]:
        if not self.root.exists():
            return []
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        removed = []
        for path in self.root.glob("*.jsonl"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified < cutoff.astimezone(UTC):
                    path.unlink()
                    removed.append(path)
            except FileNotFoundError:
                continue
        return sorted(removed)

    def clear_test_run_logs(self) -> int:
        if not self.root.exists():
            return 0
        removed = 0
        for path in self.root.glob("*.jsonl"):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        return removed
