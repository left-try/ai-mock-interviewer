# Voice test mode, diagnostics, and report design

## Status

Design approved by the user on 2026-10-06. This document records the proposed behavior for review before implementation planning.

## Problem

The current interview flow requires a resume, gives little visible feedback while the host model prepares a turn, and does not retain enough timing information to distinguish slow MCP operations from delays in Codex Voice. The generated PDF also has visual layout defects: evidence quote panels overlap adjacent content, and the disclaimer can be stranded on a mostly empty page.

## Goals

1. Let the user run a repeatable Backend HR interview without attaching a resume.
2. Save a local, inspectable diagnostic log for each explicitly requested test run.
3. Measure durations at boundaries the MCP server can actually observe, with clear names that do not imply model telemetry it does not receive.
4. Make interviewer transitions clearer with short spoken acknowledgments and explicit report-preparation status.
5. Produce a readable, visually structured PDF and keep the Markdown report useful and consistent.

## Non-goals and platform limits

- The MCP server receives finalized recognized text; it does not receive raw audio, partial transcripts, voice activity, or the host model's token stream.
- Therefore it cannot enforce an exact three-second silence threshold, transcribe incrementally while the candidate is speaking, or directly measure voice-model time to first token and private reasoning duration.
- It cannot promise to run host-model reasoning concurrently with speech or control the voice engine's acoustic modulation. Host instructions can request natural pacing and concise acknowledgments, but the client controls whether and how those are rendered.
- Diagnostic values must not label the time between MCP calls as model-thinking time. That interval includes speech, recognition, and other client work.
- The visual recommendation is practice feedback, not a calibrated probability of being hired.

## Proposed behavior

### A. Resume-free test interview

- Add a separately invocable test-interview workflow (for example, `/test-backend-interview`) and matching MCP start entry point.
- The test workflow must not request or inspect an attachment. The server supplies a clearly labeled synthetic candidate profile that gives the same Backend Internship HR flow enough context to ask standard, realistic questions.
- It uses the ordinary question, answer-recording, progress, and report lifecycle, with an eight-to-ten-answer target and explicit early-stop behavior.
- At startup, tell the user that this is a test run and that recognized answers are saved to a local diagnostic log. Do not include a real resume in test-run storage.

### B. Local run diagnostics

- Write one JSONL event log per test run under the existing per-user application data directory, in a dedicated `test-runs` subdirectory. Keep test logs separate from reports and the temporary voice-probe log.
- Each event includes a run/session identifier, event name, UTC wall timestamp, monotonic offset or elapsed duration, and only the fields needed to reconstruct the test workflow.
- Record: run started/ended; finalized answer received and saved; question proposal received and accepted; report validation started/completed; Markdown/PDF export started/completed/failed; and relevant error categories.
- Measure and label MCP work separately: answer persistence/validation duration, proposal validation duration, report validation duration, Markdown rendering duration, PDF export duration, and complete MCP tool-call duration.
- Record timestamps for MCP call boundaries so a post-run analyzer can show observed gaps between calls. Label these as `client_gap_ms` or similar and explicitly state that they are not model latency.
- Provide a small post-run command/script that summarizes counts, per-operation durations, percentiles where sample size permits, missing steps, and errors. Preserve the raw JSONL for debugging. Provide a clear-log action for test-run logs.
- Logs may contain recognized candidate answers. Keep them local, make that disclosure before the test starts, and do not upload them or include the resume. Define a documented delete/retention behavior before implementation.

### C. Conversational transitions

- At the end of a finalized answer, the host gives a brief, varied acknowledgment before announcing or asking the next question.
- To avoid requiring the model to formulate the next question before the answer is durably recorded, consider splitting answer persistence from next-turn proposal: first save the answer and return progress; then, after the acknowledgment, accept and persist the host's next-question proposal. The implementation plan must preserve event idempotency and prevent an unrecorded answer from being followed by another question.
- When resume processing or report preparation takes place, the host uses one short status sentence so the user understands what is happening. Full completion is still announced only after the validated report is returned.
- These are best-effort host instructions. They do not make model generation asynchronous or guarantee continuous speech during host reasoning.
- Adjust text instructions for concise, natural Russian pacing, varied acknowledgments, and one question at a time. Do not claim the MCP server can alter the selected TTS voice or remove audio glitches.

### D. Report and PDF presentation

- Preserve the existing evidence-based Markdown sections and exact-quote/source requirements.
- Add a clear qualitative verdict: `strong signal`, `mixed signal`, or `insufficient data`, translated for the reader.
- Add an interview-coverage indicator based on actual covered topics and a visual 1–5 bar for each scored criterion. Do not fabricate scores for insufficient evidence.
- Do not label a score as hiring probability. If a normalized visual percentage is shown, label its calculation and scope explicitly as rubric/coverage progress, never as chance of passing.
- Redesign the PDF with a clear title and verdict panel, compact criterion bars, distinct strength/growth sections, visually separated quote cards, uncertainty notes, and a readable disclaimer.
- Use ReportLab flowables with explicit padding and pagination-safe grouping so a quote, source label, observation, and section heading cannot overlap or split confusingly. Avoid stranding the disclaimer on its own page. Add page numbers when the report spans multiple pages.
- Keep Cyrillic font embedding and searchable text. Inspect rendered pages at normal reading size as part of visual verification.

## Data flow

1. The test workflow starts a session with a server-provided synthetic profile and creates its run log.
2. Codex Voice asks the opening question and handles the spoken conversation.
3. Finalized answers and interviewer turns are written to the session and test log in order. Every candidate answer remains idempotent by event ID.
4. The analyzer reads the completed JSONL and produces a local timing summary without calling a model or external service.
5. Report validation produces the authoritative report model. Markdown and PDF render from that same validated model.

## Test plan

### Unit tests

- **Synthetic test start:** starting test mode builds the configured synthetic profile, does not invoke resume parsing, and does not read or require an attachment.
- **Run log schema:** each supported event has a run ID, event name, UTC timestamp, monotonic offset/duration, and valid JSONL encoding; answer text is present only for the explicit local test workflow.
- **Timing calculations:** operation durations use a monotonic clock, are non-negative, and distinguish MCP processing durations from inter-call client gaps. Empty samples and a single sample do not cause invalid percentile calculations.
- **Summary analyzer:** reports event counts, missing lifecycle events, operation samples, and p50/p95 only when the sample count supports them; malformed JSONL lines and unknown future event types produce an actionable diagnostic without discarding valid neighboring events.
- **Retention and deletion:** expired test logs are pruned at startup according to the 30-day policy, recent logs remain, and explicit deletion removes test logs without touching interview reports or probe logs.
- **PDF flowables:** long quotes and observations wrap without overlap; section headings remain associated with their content; multi-page documents retain headers/footers and a non-orphaned disclaimer.
- **Infographic semantics:** topic coverage reflects actual topic IDs; score bars render only scored criteria; `insufficient_data` displays no fabricated aggregate percentage or score.

### Service and MCP contract tests

- Start a test session without resume text or attachment and verify it receives a synthetic profile, a test-mode marker, and a fresh run log.
- Record a candidate answer once and verify it is persisted before any next-question proposal is accepted.
- Replay the same answer event ID and verify one candidate turn and one answer log event exist; reject a conflicting payload under an already-used event ID.
- Accept a valid next-turn proposal after answer persistence; reject invalid kinds/topics and ensure failures do not corrupt prior state or append success events.
- Stop after any answer count and verify an early-exit report preserves unanswered topics as uncertainty and exports both formats.
- Exercise duplicate report finish, export failure, log-write failure, analyzer failure, clear-log, and retention cleanup without turning a valid transcript into a false completion.
- Verify the resume-based path uses the same answer-first contract and that the latest transcript tail is recorded before report validation.

### Integration tests with a fake host model

- Run a complete synthetic interview through start, answer save, delayed next-turn proposal, report validation, log summary, and Markdown/PDF export; assert event order and trace correlation.
- Simulate interruption after answer persistence but before the next question and verify the session can resume without duplicating the answer or losing progress.
- Run two sessions concurrently and verify logs, event IDs, metrics, and exports remain isolated.
- Verify a failed PDF export still returns the validated Markdown report and records the export error and duration.

### Performance and diagnostic checks

- Use a deterministic fake clock to verify measured MCP stage durations and client gaps independently.
- Run a local test interview and report raw sample counts plus p50/p95 for answer-save processing, proposal-validation processing, report validation, Markdown/PDF export, full MCP calls, and client gaps.
- Do not assert model TTFT or private model reasoning duration. Collect baseline samples first; do not invent latency budgets before a baseline exists.

### Manual Codex Voice checks

- Start `/test-backend-interview` in Voice with no attachment and confirm the opening does not ask for a resume.
- After each spoken answer, pause for 1, 3, and 5 seconds in separate runs; note whether Codex Voice finalizes the transcript and whether a tool call follows. Treat these as observations, not proof that the server enforces a silence threshold.
- Confirm the candidate hears a brief acknowledgment before the next question is generated/spoken, and hears a report-preparation sentence before report work. Record any visible or audible wait separately from measured MCP durations.
- Listen for robotic cadence, stutters, clipping, and abrupt timing using the same selected voice and comparable prompts. The MCP logs cannot capture or score audio quality.
- Confirm full report text, file links, test-run log location, and clear-log behavior after normal and early-stop runs.

### PDF visual review

- Generate fixtures for insufficient evidence, complete scored evidence, long quotes, empty lists, and enough content to span multiple pages.
- Render every page to PNG with Poppler at reading resolution and inspect for overlapping panels, clipped text, broken Cyrillic glyphs, awkward page breaks, orphaned headings, and disclaimer placement.
- Reopen generated PDFs with `pypdf`, verify expected report text remains searchable, and confirm the verdict, coverage visualization, scored criteria, evidence, uncertainty, and disclaimer are present.

## Acceptance criteria

- A user can start a test interview without an attachment; the workflow clearly identifies itself as a test and generates a report at completion or early stop.
- Every finalized test answer appears once in both session state and the diagnostic log; duplicate event IDs do not duplicate either record.
- A test run produces a separate local JSONL log and a summary showing MCP operation durations and client gaps with accurate labels.
- No claim of TTFT, partial transcription, exact silence duration, or TTS quality is made unless a future supported host telemetry integration provides those measurements.
- Host instructions request concise acknowledgments and report-preparation status, while the interview remains one question at a time and only claims completion after report validation.
- The PDF has no overlapping content, orphaned disclaimer page, clipped text, or broken Cyrillic glyphs in rendered-page inspection. It contains the qualitative verdict, coverage, criterion visualization, evidence, uncertainties, and disclaimer.
- Markdown and PDF are generated from the same report data; missing evidence remains explicitly unscored rather than being converted into a fabricated percentage.
- Existing transcript integrity, report evidence validation, and idempotency contracts remain unchanged or are extended with explicit regression tests.

## Decisions fixed for implementation planning

- Use the `/test-backend-interview` skill and a dedicated `start_test_interview` MCP entry point.
- Retain test-run logs locally for 30 days, then remove them during test-server startup. Provide an explicit clear-log tool as well. Disclose that recognized answers are saved before beginning each run.
- Split answer persistence from next-turn proposal for both resume-based and test interviews. Persist the candidate answer first; after the host acknowledgment, accept and persist the next-turn proposal. The plan must add replay/idempotency and interrupted-turn recovery tests before replacing the current contract.
- Show topic coverage as `covered/required` and criterion scores as individual 1–5 bars. Do not add an aggregate score or any probability of hiring.
- Add a brief resume-review status sentence to the existing resume-based skill. This is a conversational cue only; it does not imply that resume parsing can run concurrently with speech.

## Review note

The PDF defect was reproduced from the most recent generated report. Its first page shows evidence quote frames colliding with following observations/headings; the second page contains only the disclaimer. This specification addresses both issues through pagination-safe layouts and a rendered-page review gate.
