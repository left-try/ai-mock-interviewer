"""Contracts for summarizing test-run logs without a model or network."""

from __future__ import annotations

import importlib
import json

import pytest


def _summarizer():
    try:
        module = importlib.import_module("scripts.summarize_test_run")
    except ModuleNotFoundError as exc:
        pytest.fail(f"offline test-run analyzer is not implemented: {exc}")
    return module.summarize_events


def _event(name, *, duration=None, offset=0):
    event = {
        "run_id": "run-1", "event_name": name, "timestamp_utc": "2026-10-06T00:00:00Z",
        "monotonic_offset_ms": offset,
    }
    if duration is not None:
        event["duration_ms"] = duration
    return event


def test_summary_counts_events_and_reports_mcp_percentiles_when_sample_is_large_enough():
    summarize = _summarizer()
    lines = [json.dumps(_event("answer_save", duration=value, offset=value * 2)) for value in (1, 2, 3, 4, 5)]
    lines += [json.dumps(_event("run_started")), json.dumps(_event("run_ended"))]

    summary = summarize(lines)

    assert summary["event_counts"]["answer_save"] == 5
    metric = summary["metrics"]["answer_save_duration_ms"]
    assert metric["count"] == 5
    assert metric["p50"] == 3
    assert metric["p95"] is not None


def test_summary_marks_small_samples_preliminary_and_single_sample_is_not_a_percentile():
    summarize = _summarizer()

    summary = summarize([json.dumps(_event("pdf_export", duration=12.5))])
    metric = summary["metrics"]["pdf_export_duration_ms"]

    assert metric["count"] == 1
    assert metric.get("p50") is None
    assert metric.get("p95") is None
    assert "preliminary" in metric["note"].lower()


def test_answer_saved_lifecycle_events_also_contribute_to_answer_save_timing():
    summary = _summarizer()([json.dumps(_event("answer_saved", duration=3.25))])

    assert summary["metrics"]["answer_save_duration_ms"]["samples_ms"] == [3.25]


def test_summary_handles_no_timing_samples_and_missing_run_end():
    summary = _summarizer()([json.dumps(_event("run_started"))])

    assert summary["metrics"] == {}
    assert any("run_ended" in item for item in summary["warnings"])
    assert "run_ended" in summary["missing_events"]
    assert any("timing" in item.lower() for item in summary["warnings"])


def test_summary_reports_missing_report_stages_for_a_completed_run():
    summary = _summarizer()([
        json.dumps(_event("run_started")),
        json.dumps({**_event("run_ended"), "details": {"status": "completed"}}),
    ])

    assert summary["missing_events"] == ["report_validation", "markdown_render", "pdf_export"]


def test_malformed_json_line_does_not_hide_later_valid_events():
    summary = _summarizer()([
        json.dumps(_event("run_started")), "{broken json", json.dumps(_event("run_ended")),
    ])

    assert summary["event_counts"]["run_started"] == 1
    assert summary["event_counts"]["run_ended"] == 1
    assert summary["malformed_lines"] == [2]


def test_unknown_events_are_counted_and_client_gaps_are_not_model_latency():
    summary = _summarizer()([
        json.dumps(_event("future_event_v2")),
        json.dumps(_event("client_gap", duration=2500)),
    ])

    assert summary["event_counts"]["future_event_v2"] == 1
    assert summary["metrics"]["client_gap_ms"]["count"] == 1
    assert "ttft" not in json.dumps(summary).lower()
    assert "model_thinking" not in json.dumps(summary).lower()


@pytest.mark.parametrize(
    ("event_name", "metric_name"),
    [
        ("answer_save", "answer_save_duration_ms"),
        ("proposal_validation", "proposal_validation_duration_ms"),
        ("report_validation", "report_validation_duration_ms"),
        ("markdown_render", "markdown_render_duration_ms"),
        ("pdf_export", "pdf_export_duration_ms"),
        ("mcp_tool_call", "mcp_tool_call_duration_ms"),
        ("client_gap", "client_gap_ms"),
    ],
)
def test_summary_keeps_each_observable_stage_as_a_separate_metric(event_name, metric_name):
    summary = _summarizer()([json.dumps(_event(event_name, duration=17.0))])

    assert summary["metrics"][metric_name]["count"] == 1
    assert summary["metrics"][metric_name]["samples_ms"] == [17.0]


def test_subscription_metrics_are_summarized_by_stage():
    summary = _summarizer()([
        json.dumps(_event("fast_model_ttft", duration=80, offset=1)),
        json.dumps(_event("fast_model_completion", duration=900, offset=2)),
        json.dumps(_event("background_evaluation", duration=1200, offset=3)),
        json.dumps(_event("background_wait", duration=100, offset=4)),
        json.dumps(_event("turn_ready", duration=1050, offset=5)),
    ])

    assert set(summary["metrics"]) == {
        "fast_model_ttft_duration_ms", "fast_model_completion_duration_ms",
        "background_evaluation_duration_ms", "background_wait_duration_ms",
        "turn_ready_duration_ms",
    }
    assert all(metric["count"] == 1 for metric in summary["metrics"].values())
    assert "transcript" not in json.dumps(summary).lower()


def test_invalid_subscription_durations_are_reported():
    summary = _summarizer()([
        json.dumps(_event("fast_model_completion", duration=-1)),
        json.dumps(_event("background_wait", duration=float("inf"))),
        json.dumps(_event("turn_ready", duration=True)),
    ])

    assert summary["metrics"] == {}
    assert len([warning for warning in summary["warnings"] if "invalid duration_ms" in warning]) == 3


def test_client_gap_warning_never_claims_host_model_latency():
    summary = _summarizer()([json.dumps(_event("client_gap", duration=22000))])

    warning = " ".join(summary["warnings"]).lower()
    assert "not model processing time" in warning
    assert "host model latency" not in warning
    assert "fast_model" not in warning


def test_subscription_percentiles_are_marked_preliminary_below_twenty_samples():
    events = [json.dumps(_event("fast_model_completion", duration=i)) for i in range(1, 10)]

    metric = _summarizer()(events)["metrics"]["fast_model_completion_duration_ms"]

    assert metric["p50"] == 5
    assert metric["p95"] == 9
    assert "preliminary" in metric["note"].lower()
