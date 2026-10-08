"""Non-secret per-account model routing preferences."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def default_model_settings_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ai-mock-interviewer" / "model-settings.json"
    config = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config) if config else Path.home() / ".config") / "ai-mock-interviewer" / "model-settings.json"


@dataclass(frozen=True)
class InterviewModelSettings:
    fast_model: str
    fast_effort: str
    analysis_model: str
    analysis_effort: str

    def validate(self, client) -> "InterviewModelSettings":
        client.validate_selection(self.fast_model, self.fast_effort)
        client.validate_selection(self.analysis_model, self.analysis_effort)
        return self

    def save(self, path: Path | str | None = None, *, account_id: str) -> None:
        target = Path(path) if path is not None else default_model_settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"account_id": account_id, **asdict(self)}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @classmethod
    def load(cls, path: Path | str | None = None, *, account_id: str, client) -> "InterviewModelSettings":
        target = Path(path) if path is not None else default_model_settings_path()
        try:
            record = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Interview model settings are unavailable; configure model roles first") from exc
        if record.get("account_id") != account_id:
            raise ValueError("Saved interview models belong to a different ChatGPT account")
        settings = cls(
            fast_model=str(record["fast_model"]), fast_effort=str(record["fast_effort"]),
            analysis_model=str(record["analysis_model"]), analysis_effort=str(record["analysis_effort"]),
        )
        return settings.validate(client)
