# Test Interview and Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a resume-free Backend HR test interview that leaves an inspectable local event log and timing summary after each run.

**Architecture:** A dedicated MCP entry point starts a session using a fixed synthetic profile. The existing session tools emit test-only JSONL events. A standard-library analyzer summarizes MCP durations and client gaps without external services.

**Tech Stack:** Python 3.11+, FastMCP, existing LangGraph service, pytest, JSONL, PowerShell-compatible CLI invocation.

**Spec:** `docs/superpowers/specs/2026-10-06-voice-test-and-report-design.md`

## Global Constraints

- The test entry point accepts no resume path, attachment, or resume text.
- Store test logs locally under `%LOCALAPPDATA%/ai-mock-interviewer/test-runs` on Windows and the existing XDG-style application-data path elsewhere.
- Test logs include recognized test answers; disclose this before the first question.
- Retain logs for 30 days and provide an explicit clear-log action.
- Use a monotonic clock for durations and UTC timestamps for event ordering.
- Label gaps between MCP calls as client gaps, never as model TTFT or reasoning time.

## Review Focus

- Missing or malformed log files must not destroy interview state or prevent a validated report.
- Duplicate event IDs must not duplicate transcript or answer-saved events.
- Retention cleanup must not delete reports, probe logs, or logs younger than 30 days.
- A malformed JSONL line must not hide later valid events from the summary.
- Empty and one-sample timing sets must produce valid summaries without misleading percentiles.

---

### Task 1: Add test-run event storage and retention

**Files:**
- Create: `src/mock_interviewer/test_run_log.py`
- Test: `tests/test_test_run_log.py`
- Modify: `src/mock_interviewer/report_export.py` only if a shared app-data path helper is needed; prefer a separate helper otherwise.

**Interfaces:**
- `TestRunLog(root: Path | None = None, *, retention_days: int = 30)` writes one UTF-8 JSONL file per `run_id`.
- `append_event(run_id: str, event_name: str, *, session_id: str | None = None, duration_ms: float | None = None, details: dict | None = None) -> None` adds UTC time, monotonic offset, and event metadata.
- `prune_expired_logs(now: datetime | None = None) -> list[Path]` deletes only expired files within the test-runs directory.
- `clear_test_run_logs() -> int` removes only files in the test-runs directory and returns the deleted-file count.

- [ ] **Step 1: Write these failing tests** in `tests/test_test_run_log.py`:
  - `test_append_event_writes_utc_timestamp_and_monotonic_offset`: parse each JSONL row and assert run ID, event name, UTC timestamp, monotonic offset, and non-negative duration.
  - `test_default_log_directory_is_separate_from_reports`: assert the resolved path ends in `ai-mock-interviewer/test-runs` and is not the reports or probe-log directory.
  - `test_prune_expired_logs_removes_only_files_older_than_30_days`: inject `now`; assert the 31-day test log is removed and 29-day, report, and probe files remain.
  - `test_append_event_keeps_candidate_text_only_in_test_log`: assert a test answer is represented in the test JSONL while no resume/report file is modified.
  - `test_clear_test_logs_does_not_delete_reports_or_probe_logs`: assert the test-run files are removed and files in both neighboring directories remain.
- [ ] **Step 2: Run `pytest tests/test_test_run_log.py -q`** and verify expected failures identify missing logger behavior.
- [ ] **Step 3: Implement the JSONL logger, clear operation, and 30-day pruning** using an injectable root and clock for deterministic tests.
- [ ] **Step 4: Run the logger tests** and confirm each JSONL line parses independently.
- [ ] **Step 5: Commit** as `[feat] log local voice test runs`.

### Task 2: Add the resume-free test interview entry point

**Files:**
- Create: `src/mock_interviewer/test_profile.py`
- Modify: `tools/interview_mcp.py`
- Modify: `tools/voice_probe.py`
- Create: `.agents/skills/test-backend-interview/SKILL.md`
- Test: `tests/test_test_interview_workflow.py`

**Interfaces:**
- `SYNTHETIC_BACKEND_PROFILE: str` describes a fictional internship candidate and contains no user data.
- `start_test_interview(first_turn_json: str) -> dict` has no resume-related parameters, starts a normal session from the synthetic profile, creates a `run_id`, and returns the log disclosure, run ID, first turn, and test-mode marker.

- [ ] **Step 1: Write these failing tests** in `tests/test_test_interview_workflow.py`:
  - `test_test_interview_starts_without_resume_or_attachment`: call `start_test_interview(first_turn_json=...)`; assert success, `test_mode is True`, non-empty synthetic resume internally, and a first interviewer turn.
  - `test_test_interview_never_calls_resume_parser`: install a parser fake that raises if called; assert test start succeeds and the parser call count is zero.
  - `test_test_interview_discloses_local_answer_logging`: assert returned instruction mentions local test logging before the first candidate answer.
  - `test_test_start_creates_run_started_event`: read the run log and assert exactly one initial `run_started` event with the returned run ID.
- [ ] **Step 2: Run the focused tests and verify they fail** because no test-start entry point or test-mode marker exists.
- [ ] **Step 3: Implement the synthetic profile and MCP start tool**; prune expired test logs at server startup, emit `run_started` before returning the first question, and keep pruning scoped to `test-runs`.
- [ ] **Step 4: Add the slash skill** with one-question-at-a-time operation and no resume request.
- [ ] **Step 5: Run test-workflow tests** for normal start, invalid opening proposal, and disclosure text.
- [ ] **Step 6: Commit** as `[feat] start interviews with a synthetic profile`.

### Task 3: Instrument test-mode lifecycle operations

**Files:**
- Modify: `tools/interview_mcp.py`
- Modify: `src/mock_interviewer/test_run_log.py`
- Test: `tests/test_test_interview_workflow.py`

**Interfaces:**
- Test sessions map `session_id` to `run_id` and logger instance; ordinary resume interviews do not write test logs.
- Instrument `record_candidate_answer`, `propose_next_turn`, `finish_interview`, and report export with start/end/error events and monotonic durations.

- [ ] **Step 1: Add these failing tests** in `tests/test_test_interview_workflow.py`:
  - `test_complete_test_flow_logs_answer_before_proposal_and_finish`: run start, answer save, proposal, and finish; assert the event order and one answer event.
  - `test_test_flow_records_nonnegative_mcp_stage_durations`: assert answer-save, proposal-validation, report-validation, and export durations are non-negative and distinctly named.
  - `test_duplicate_answer_retry_logs_no_second_saved_event`: replay the same event ID; assert one candidate turn and one successful answer-saved event.
  - `test_non_test_interview_does_not_write_test_run_log`: start the resume-based path and assert no test-runs file is created.
  - `test_logger_failure_does_not_lose_saved_answer_or_valid_report`: inject a write failure and assert session/report result remains authoritative with a diagnostic error.
- [ ] **Step 2: Run the tests and verify they fail** because lifecycle events are not currently emitted.
- [ ] **Step 3: Add isolated instrumentation** that cannot turn a logger I/O error into a lost answer or failed valid report.
- [ ] **Step 4: Run tests** and assert event counts and order, not wall-clock thresholds.
- [ ] **Step 5: Commit** as `[feat] capture test interview processing timings`.

### Task 4: Add the JSONL analyzer and operator docs

**Files:**
- Create: `scripts/summarize_test_run.py`
- Test: `tests/test_test_run_summary.py`
- Modify: `docs/GETTING_STARTED.md`
- Modify: `docs/VOICE_PROBE.md`

**Interfaces:**
- `summarize_events(lines: Iterable[str]) -> dict` returns event counts, missing lifecycle events, valid samples, p50/p95 when meaningful, and malformed-line diagnostics.
- CLI accepts one JSONL path and prints a concise human-readable summary; it never calls a model or network.

- [ ] **Step 1: Write these failing tests** in `tests/test_test_run_summary.py`:
  - `test_summary_reports_event_counts_and_mcp_percentiles`: with at least five durations, assert count, p50, and p95 are present under MCP metric names; with fewer than five, assert percentiles are explicitly marked as preliminary/omitted.
  - `test_summary_omits_percentiles_for_single_sample`: assert one sample is reported as a sample, not a stable percentile.
  - `test_summary_handles_no_timing_samples`: assert no division error and a clear no-samples note.
  - `test_summary_skips_malformed_line_and_reads_later_events`: assert valid rows after an invalid row remain counted and an actionable warning is returned.
  - `test_summary_labels_inter_call_delay_as_client_gap`: assert output says `client_gap_ms` and does not contain a TTFT/model-thinking metric.
  - `test_summary_reports_missing_run_end_event`: assert an interrupted-run warning appears when no `run_ended` event exists.
- [ ] **Step 2: Run analyzer tests and verify they fail** because the summary function is absent.
- [ ] **Step 3: Implement percentile and event summary behavior** with no minimum latency pass/fail threshold until real baselines are collected.
- [ ] **Step 4: Document log path, sensitive transcript disclosure, 30-day retention, manual clearing, and commands.**
- [ ] **Step 5: Run focused tests and `git diff --check`.**
- [ ] **Step 6: Commit** as `[feat] summarize voice test timings`.

