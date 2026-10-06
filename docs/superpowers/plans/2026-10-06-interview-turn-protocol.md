# Interview Turn Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save each finalized candidate answer before generating the following interviewer question, so the host can acknowledge the answer first without risking transcript loss.

**Architecture:** Separate answer persistence from host-proposed next-turn validation. A pending answer event blocks another candidate answer until a question proposal is accepted or the interview is finished. Preserve retry safety for both answer events and proposals.

**Tech Stack:** Python 3.11+, Pydantic, LangGraph, FastMCP, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-voice-test-and-report-design.md`

## Global Constraints

- Keep candidate-answer event IDs idempotent.
- Never ask the next question before the prior answer is durably stored.
- Preserve exact candidate transcripts and report evidence validation.
- Announce full completion only after report validation succeeds.
- Do not claim MCP can enforce silence thresholds or access partial audio.

## Review Focus

- Duplicate answer retry after a tool timeout must not append a second turn or log success twice.
- A failed question proposal must leave the saved answer intact and retryable.
- A second answer arriving while a next-question proposal is pending must not overwrite the pending state.
- Early stop immediately after answer persistence must still permit a partial report.
- A final transcript correction during `awaiting_report` must remain recordable once before report creation.

---

### Task 1: Persist candidate answers independently

**Files:**
- Modify: `src/mock_interviewer/domain/models.py`
- Modify: `src/mock_interviewer/service.py`
- Test: `tests/test_graph_regressions.py`
- Test: `tests/test_public_contracts.py`

**Interfaces:**
- Produces `InterviewService.submit_answer(session_id: str, text: str, *, event_id: str, allow_final_correction: bool = False) -> InterviewSession`; it appends the candidate turn without invoking the model and marks that event as awaiting a next-turn proposal.
- Produces `InterviewService.propose_next_turn(session_id: str, answer_event_id: str, proposal: dict) -> InterviewSession`; it validates and appends exactly one interviewer turn associated with the saved answer event.

- [ ] **Step 1: Write these failing tests** in `tests/test_graph_regressions.py` or `tests/test_public_contracts.py`:
  - `test_submit_answer_persists_before_invoking_model`: after `submit_answer`, the session has one candidate turn, `pending_answer_event_id` is set, and the fake model call count is unchanged.
  - `test_duplicate_answer_event_does_not_append_second_turn`: replaying the same event ID preserves one candidate turn and the same pending event.
  - `test_proposal_retry_appends_only_one_question`: an invalid proposal leaves the candidate turn pending; a valid retry appends exactly one interviewer turn and clears pending state.
  - `test_rejects_new_answer_while_question_is_pending`: a distinct event ID cannot append another answer before the pending proposal is resolved.
  - `test_finish_after_saved_answer_preserves_early_exit`: finishing without a next question retains the candidate answer and permits an insufficient-data report.
  - `test_final_transcript_correction_before_report_is_persisted_once`: while awaiting a report, a distinct final-tail event is appended once and appears in report inputs.
- [ ] **Step 2: Run those focused tests and verify they fail** because the current `submit_answer` requires the next-turn proposal in the same operation.
- [ ] **Step 3: Implement pending-answer state and proposal idempotency** without changing quote validation or maximum-answer rules.
- [ ] **Step 4: Run the focused tests** and confirm each saved answer has exactly one event result and can be followed by at most one interviewer turn.
- [ ] **Step 5: Commit** as `[refactor] persist interview answers before next turns`.

### Task 2: Expose answer-first MCP calls and host behavior

**Files:**
- Modify: `tools/interview_mcp.py`
- Modify: `tools/voice_probe.py`
- Modify: `.agents/skills/backend-internship-interviewer/SKILL.md`
- Test: `tests/test_voice_interview_workflow.py`

**Interfaces:**
- `record_candidate_answer(session_id: str, transcript: str, event_id: str, include_transcript: bool = False) -> dict` persists the answer and returns progress plus `next_action` (`propose_next_turn` or `finish_interview`).
- `propose_next_turn(session_id: str, answer_event_id: str, next_turn_json: str) -> dict` validates and stores the host question proposal for that answer.

- [ ] **Step 1: Add these failing MCP contract tests** in `tests/test_voice_interview_workflow.py`:
  - `test_record_candidate_answer_returns_before_question_proposal`: response has `next_action == "propose_next_turn"`, one saved candidate turn, and no interviewer question after it.
  - `test_propose_next_turn_persists_question_after_saved_answer`: valid proposal returns the exact saved interviewer question and `next_action == "ask_next_question"`.
  - `test_duplicate_next_turn_proposal_does_not_duplicate_question`: repeating `answer_event_id` returns one interviewer turn.
  - `test_invalid_next_turn_proposal_keeps_answer_retryable`: invalid JSON/schema returns an error while the transcript still contains exactly one saved candidate answer.
  - `test_early_stop_after_answer_save_can_finish_without_next_question`: finish succeeds with the saved answer and missing-topic uncertainty.
- [ ] **Step 2: Run the focused workflow tests and verify they fail** on the existing combined call contract.
- [ ] **Step 3: Update MCP tools and instructions** so the host acknowledges a saved answer before generating/proposing the next question; keep one-question-at-a-time behavior.
- [ ] **Step 4: Run workflow tests** and assert the returned transcript, progress, next action, and report status.
- [ ] **Step 5: Run the full suite**; preserve and report the known invalid-evidence fixture failure if it remains.
- [ ] **Step 6: Commit** as `[fix] save voice answers before generating follow-ups`.

### Task 3: Document and manually verify voice boundaries

**Files:**
- Modify: `docs/GETTING_STARTED.md`
- Modify: `docs/VOICE_PROBE.md`
- Test: manual Codex Voice checklist in `docs/VOICE_PROBE.md`

- [ ] **Step 1: Document the supported workflow**: answer saved, brief acknowledgment, then next-question generation; preparation status before final report.
- [ ] **Step 2: Add manual pause checks** for 1-, 3-, and 5-second pauses and record observations without calling them enforced thresholds.
- [ ] **Step 3: Run `git diff --check`** and confirm docs do not promise partial transcripts, TTFT, or direct TTS control.
- [ ] **Step 4: Commit** as `[docs] describe answer-first voice flow`.

