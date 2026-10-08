"""Summarize local interview diagnostics without a model or network."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable

_METRIC_NAMES = {
    "answer_save": "answer_save_duration_ms",
    "answer_saved": "answer_save_duration_ms",
    "proposal_validation": "proposal_validation_duration_ms",
    "report_validation": "report_validation_duration_ms",
    "markdown_render": "markdown_render_duration_ms",
    "pdf_export": "pdf_export_duration_ms",
    "mcp_tool_call": "mcp_tool_call_duration_ms",
    "client_gap": "client_gap_ms",
    "fast_model_ttft": "fast_model_ttft_duration_ms",
    "fast_model_completion": "fast_model_completion_duration_ms",
    "model_setup": "model_setup_duration_ms",
    "background_evaluation": "background_evaluation_duration_ms",
    "background_wait": "background_wait_duration_ms",
    "turn_ready": "turn_ready_duration_ms",
}


def summarize_events(lines: Iterable[str]) -> dict:
    counts: Counter[str] = Counter()
    samples: dict[str, list[float]] = defaultdict(list)
    warnings: list[str] = []
    malformed_lines: list[int] = []
    valid_event_count = 0
    saw_start = False
    saw_end = False
    ended_status = None

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            malformed_lines.append(line_number)
            warnings.append(f"Line {line_number} is not valid JSON and was skipped.")
            continue
        if not isinstance(event, dict) or not isinstance(event.get("event_name"), str):
            malformed_lines.append(line_number)
            warnings.append(f"Line {line_number} is missing an event_name and was skipped.")
            continue
        name = event["event_name"]
        valid_event_count += 1
        counts[name] += 1
        saw_start |= name == "run_started"
        saw_end |= name == "run_ended"
        if name == "run_ended":
            details = event.get("details")
            if isinstance(details, dict):
                ended_status = details.get("status")
        metric_name = _METRIC_NAMES.get(name)
        duration = event.get("duration_ms")
        if metric_name and isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
            samples[metric_name].append(float(duration))
        elif metric_name and duration is not None:
            warnings.append(f"Line {line_number} has an invalid duration_ms for {name}.")

    metrics = {}
    for name, values in samples.items():
        entry = {"count": len(values), "samples_ms": values}
        if len(values) >= 5:
            ordered = sorted(values)
            entry["p50"] = _nearest_rank(ordered, 0.50)
            entry["p95"] = _nearest_rank(ordered, 0.95)
        else:
            entry["p50"] = None
            entry["p95"] = None
        if len(values) < 20:
            entry["note"] = "preliminary: fewer than twenty samples"
        metrics[name] = entry

    if not samples:
        warnings.append("No timing samples were recorded.")
    missing_events = []
    if valid_event_count and not saw_start:
        missing_events.append("run_started")
        warnings.append("The run_started event is missing; this may be a partial log.")
    if valid_event_count and not saw_end:
        missing_events.append("run_ended")
        warnings.append("The run_ended event is missing; the run may have been interrupted.")
    if ended_status == "completed":
        for required in ("report_validation", "markdown_render", "pdf_export"):
            if counts[required] == 0:
                missing_events.append(required)
                warnings.append(f"The completed run is missing lifecycle event {required}.")
    if counts["client_gap"]:
        warnings.append("client_gap_ms includes speech, recognition, and client work; it is not model processing time.")

    return {
        "event_counts": dict(sorted(counts.items())),
        "metrics": metrics,
        "missing_events": missing_events,
        "error_count": sum(count for name, count in counts.items() if name.endswith("_failed") or name == "operation_failed"),
        "warnings": warnings,
        "malformed_lines": malformed_lines,
    }


def _nearest_rank(ordered: list[float], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]
