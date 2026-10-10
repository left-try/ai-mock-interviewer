# Test suite contracts

This document defines the production boundary expected by the tests. It exists so implementation work can satisfy stable behavior without weakening assertions to match incidental implementation details. The tests are authoritative for executable assertions; this file explains their shared API assumptions.

## Test command

Run `python -m pip install -e ".[test]"` and then `python -m pytest`.

The suite must not call a live model provider or use real candidate data. A configured external model is only appropriate in a separate, explicitly invoked evaluation job. Unit, graph, contract and CI integration tests use the queue-based fake in `tests/fakes.py`.

## Expected package boundary

The production distribution is `mock_interviewer` under `src/`. The public service is `mock_interviewer.service.InterviewService` and accepts:

- `model`: asynchronous structured-output model adapter with `ainvoke(messages, response_schema=...)`;
- `resume_parser`: asynchronous parser with `extract_text(content: bytes, filename: str)`;
- optional `limits` and `rubric` configuration.

The service methods exercised by the tests are:

- `start(resume_text=...)`;
- `start_from_upload(content, filename=...)`;
- `submit_answer(session_id, text, event_id=...)`;
- `finish(session_id)`;
- `cancel(session_id)`;
- `delete(session_id)`;
- `get_session(session_id)`;
- `list_sessions()`.

Calls are asynchronous. Invalid user input raises `ValueError` or a documented application error before calling the model. Unknown sessions raise `SessionNotFound`; invalid terminal-state transitions raise `InvalidSessionTransition`; invalid provider output raises `InvalidModelOutput`; unusable resume input raises `ResumeValidationError`/`ResumeParseError`; provider failures raise `ModelProviderError`; invalid reports raise `InvalidReport`; conflicting concurrent writes raise `ConcurrentSessionUpdate`.

## State and event contract

- A newly created session has a unique `id`, status `active`, validated resume text, ordered `turns`, and optional `report`.
- Allowed lifecycle statuses are `active`, `awaiting_report`, `completed`, and `cancelled`. `completed` and `cancelled` are terminal.
- A turn has a stable ID, `role` (`interviewer` or `candidate`), `text`, and, for interviewer turns, `topic_id` and `kind` (`question`, `follow_up`, or `repeat`).
- Candidate event IDs are idempotency keys. Replaying the same event returns the existing result and does not append a second turn or call the model again.
- Distinct writes to one session are serialized or one fails with `ConcurrentSessionUpdate`; they must not silently overwrite one another.
- A completed report is immutable and repeated `finish` calls are idempotent.

## Voice interview workflow contract

- The voice host records every finalized candidate answer through `record_candidate_answer` before asking another question. Each answer has a distinct idempotency key; a retry reuses the same key and cannot duplicate the answer.
- The active Backend Screening covers motivation, education, project experience, personal contribution, teamwork, a challenge, reflection, and expectations at Internship, Junior, or Middle depth. The normal target is 8–10 candidate answers.
- The host follows deterministic `interview_progress` and `next_action` fields returned by MCP. It must announce completion itself when directed, then request the report without inventing an extra unanswered question.
- `record_candidate_answer` supports a compact voice mode that returns only the latest saved answer, the next interviewer turn, and progress. It does not resend the full transcript on each turn.
- If a finalized transcript tail arrives after the session enters `awaiting_report` but before report creation, it is appended exactly once and included in the report. A completed report remains immutable.
- The host announces a completed interview only after `finish_interview` succeeds; a failed report call cannot be described as a finished report.
- A successful `finish_interview` returns the full validated report as Markdown for visible chat presentation and saves matching `.md` and `.pdf` files locally. The PDF must retain Cyrillic text and be searchable.
- If local file export fails after report validation, the completed report and Markdown text are still returned, alongside an explicit export error; the host must show the text and disclose the file failure.
- If the candidate stops early or the maximum answer count is reached with uncovered topics, the report must preserve those gaps as uncertainty.
- These host/MCP behaviors are covered by `tests/test_voice_interview_workflow.py`; this supplements the service contracts above without changing existing assertions.

## Model output contract

Next-turn structured output contains `kind`, `topic_id`, `text`, `evidence`, and `confidence`. `kind` is an allowlisted action; model output cannot choose arbitrary tools, code, shell, browser or filesystem actions. The service validates every output before adding it to state or displaying it.

Report structured output contains `recommendation`, `scores`, `strengths`, `growth_areas`, `evidence`, `uncertainties`, and `disclaimer`. Recommendations are `strong_signal`, `mixed_signal`, or `insufficient_data`; numeric criterion scores, if present, are integers from 1 through 5 (or null where evidence is insufficient). Each evidence item includes a criterion, source turn ID, quote, and observation. References must resolve to the actual resume or a candidate turn, and quoted facts must be supported by that source. Unsupported output is rejected or safely converted to uncertainty; it cannot be silently presented as fact.

Deterministic domain rules live in `mock_interviewer.domain.rules`: lifecycle terminality, allowed lifecycle events, score validation, exact normalized duplicate-question detection, and quote-in-source validation. These rules stay independent of LangGraph and model behavior.

## Stable test data policy

Resume fixtures and dialogue are synthetic. Tests may include intentionally malicious instruction strings, but no actual personal data, API credentials, audio recordings or production transcripts. Provider fakes record what the application sends so tests can verify data boundaries and call counts.

## Implementation status

The production `src/mock_interviewer` package implements this boundary. Keep the test suite unchanged as the behavior contract. If a test fixture conflicts with the stated integrity requirements (for example, citing an answer that was never recorded), resolve the fixture/specification conflict explicitly rather than weakening evidence validation in production.
