"""Contracts for local-only JSONL diagnostics produced by explicit test runs."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _logger_module():
    try:
        return importlib.import_module("mock_interviewer.test_run_log")
    except ModuleNotFoundError as exc:
        pytest.fail(f"test-run JSONL logger is not implemented: {exc}")


def test_append_event_writes_utc_timestamp_offset_and_nonnegative_duration(tmp_path):
    logger_module = _logger_module()
    log = logger_module.TestRunLog(root=tmp_path)

    log.append_event(
        "run-123", "answer_saved", session_id="session-1", duration_ms=4.25,
        details={"event_id": "answer-1", "answer_text": "Ответ для локального теста"},
    )
    path = next(tmp_path.glob("*.jsonl"))
    event = json.loads(Path(path).read_text(encoding="utf-8").splitlines()[0])
    timestamp = datetime.fromisoformat(event["timestamp_utc"])

    assert timestamp.tzinfo == UTC
    assert event["run_id"] == "run-123"
    assert event["session_id"] == "session-1"
    assert event["event_name"] == "answer_saved"
    assert event["duration_ms"] == 4.25
    assert event["monotonic_offset_ms"] >= 0
    assert event["details"]["answer_text"] == "Ответ для локального теста"


def test_default_log_directory_is_dedicated_to_test_runs(monkeypatch, tmp_path):
    logger_module = _logger_module()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    directory = logger_module.default_test_runs_dir()

    assert directory == tmp_path / "ai-mock-interviewer" / "test-runs"
    assert directory.name != "reports"
    assert directory.name != "voice-probe"


def test_retention_prunes_only_logs_older_than_thirty_days(tmp_path):
    logger_module = _logger_module()
    test_runs = tmp_path / "test-runs"
    reports = tmp_path / "reports"
    probe = tmp_path / "probe"
    test_runs.mkdir()
    reports.mkdir()
    probe.mkdir()
    expired = test_runs / "expired.jsonl"
    recent = test_runs / "recent.jsonl"
    unrelated_report = reports / "report.pdf"
    unrelated_probe = probe / "probe.jsonl"
    for path in (expired, recent, unrelated_report, unrelated_probe):
        path.write_text("fixture", encoding="utf-8")
    now = datetime(2026, 10, 6, tzinfo=UTC)
    expired.touch()
    recent.touch()
    import os
    os.utime(expired, (now.timestamp() - timedelta(days=31).total_seconds(),) * 2)
    os.utime(recent, (now.timestamp() - timedelta(days=29).total_seconds(),) * 2)

    log = logger_module.TestRunLog(root=test_runs, retention_days=30)
    removed = log.prune_expired_logs(now=now)

    assert removed == [expired]
    assert not expired.exists()
    assert recent.exists() and unrelated_report.exists() and unrelated_probe.exists()


def test_clear_removes_only_test_run_logs(tmp_path):
    logger_module = _logger_module()
    test_runs = tmp_path / "test-runs"
    reports = tmp_path / "reports"
    probe = tmp_path / "probe"
    test_runs.mkdir()
    reports.mkdir()
    probe.mkdir()
    (test_runs / "one.jsonl").write_text("{}\n", encoding="utf-8")
    (test_runs / "two.jsonl").write_text("{}\n", encoding="utf-8")
    report = reports / "report.pdf"
    probe_log = probe / "voice.jsonl"
    report.write_text("report", encoding="utf-8")
    probe_log.write_text("probe", encoding="utf-8")

    count = logger_module.TestRunLog(root=test_runs).clear_test_run_logs()

    assert count == 2
    assert list(test_runs.iterdir()) == []
    assert report.exists() and probe_log.exists()


def test_invalid_duration_and_unsafe_run_id_are_rejected(tmp_path):
    logger_module = _logger_module()
    log = logger_module.TestRunLog(root=tmp_path)

    with pytest.raises((ValueError, TypeError)):
        log.append_event("../outside", "run_started")
    with pytest.raises((ValueError, TypeError)):
        log.append_event("run-1", "answer_saved", duration_ms=-0.1)


def test_timing_events_record_only_safe_metadata(tmp_path):
    logger_module = _logger_module()
    log = logger_module.TestRunLog(root=tmp_path)

    log.append_timing_event(
        "run-123", "fast_model_completion", duration_ms=120.5,
        model_role="fast", model="gpt-example", reasoning_effort="low",
        input_tokens=40, output_tokens=12,
    )

    event = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert event["details"] == {
        "model_role": "fast", "model": "gpt-example", "reasoning_effort": "low",
        "input_tokens": 40, "output_tokens": 12,
    }
    assert not {"transcript", "prompt", "access_token"} & set(event)
    assert not {"transcript", "prompt", "access_token"} & set(event["details"])
