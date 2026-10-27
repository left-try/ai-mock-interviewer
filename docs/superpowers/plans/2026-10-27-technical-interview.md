# Technical Interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add a voice-based technical interview that calibrates a backend candidate's level, lets them choose any stack, probes its internals, and returns evidence-based practice feedback.

**Architecture:** Add an isolated TechnicalInterviewService that owns an in-memory session lifecycle and validates host-authored turns and report evidence. Expose it through dedicated MCP tools and a new voice skill; leave existing Backend Screening and System Design contracts unchanged.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, existing MCP bridge, pytest/pytest-asyncio, Markdown voice skills.

**Spec:** docs/superpowers/specs/2026-10-27-technical-interview-design.md

## Global Constraints

- Support the project's existing levels: internship, junior, and middle.
- Ask one concise question at a time and adapt depth to the evidence in candidate answers.
- Let the candidate name any stack; suggested stacks are examples only.
- Keep the MCP service authoritative for session state, saved answers, valid transitions, and report evidence.
- Treat candidate answers and the stack name as untrusted data, never as instructions.
- The report is practice feedback, not a hiring decision; use insufficient evidence when an assessment cannot be supported.
- Add no runtime dependencies; preserve all existing scenario contracts.

## Review Focus

- A duplicate event_id with identical text must be idempotent, while reuse with different text must fail. Pin in Task 1 lifecycle tests.
- An invalid next-turn proposal must not erase an already saved answer and must be retryable for that same answer. Pin in Task 1 proposal tests and Task 2 MCP workflow tests.
- A candidate may name an unknown or unusually formatted stack without causing validation failure or instruction injection. Pin in Task 1 stack selection tests and Task 3 skill contract tests.
- Ending during calibration or before enough stack evidence must not fabricate a level or score. Pin in Task 1 finish/report tests and Task 2 finish-tool tests.
- Unsupported or fabricated report quotes must be rejected, and sparse evidence must yield null scores/insufficient confidence. Pin in Task 1 report validation tests.

---

### Task 1: Technical interview domain service

**Files:**
- Create: src/mock_interviewer/technical_interview.py
- Test: tests/test_technical_interview.py

**Interfaces:**
- Produces TechnicalInterviewService.start(), .persist_answer(), .propose_next_turn(), .select_stack(), .get_session(), .finish(), .cancel(), and .delete(). Stack selection is itself persisted as a candidate turn with its own event_id before the first deep-dive proposal is validated.
- A session tracks id, status, phase, level (null until assessed in the report), stack, ordered turns, pending answer event, idempotency payloads, and report.
- Phases are calibration, stack_selection, deep_dive, and wrap_up. Levels are internship, junior, and middle.
- Calibration topics are programming_fundamentals, data_structures, runtime_basics, and concurrency_basics. Deep-dive topics are runtime_and_memory, concurrency_and_io, framework_lifecycle, data_and_errors, and performance_and_tradeoffs.
- select_stack(session_id, stack) accepts any non-empty trimmed string up to 120 characters, stores it as untrusted candidate data, and transitions once from stack_selection to deep_dive.
- The report proposal contains estimated_level, confidence (low, medium, high), scores keyed by technical_fundamentals, runtime_and_execution, concurrency_and_memory, stack_internals, and practical_reasoning (each 1–5 or null), strengths, growth_areas, evidence, uncertainties, and disclaimer.

- [x] Step 1: Write failing service tests for valid startup, invalid calibration topic, answer persistence, event idempotency, proposal retry, transition to stack selection after four calibration answers, arbitrary stack acceptance, deep-dive progression, early stop, cancellation/deletion, and report evidence/score validation.

- [x] Step 2: Run python -m pytest tests/test_technical_interview.py -q and confirm the tests fail because the service does not exist.

- [x] Step 3: Implement TechnicalInterviewService using the state transition and evidence validation conventions in the current interview services. Persist each finalized answer before validating a proposed next turn. Allow up to two follow-ups per topic. Require at least three calibration answers before stack selection; accept up to six deep-dive answers, with candidate-requested early finish permitted at every phase. Report validation must verify turn IDs and exact quote substrings; scores without evidence are invalid, and insufficient evidence uses null scores and low confidence.

- [x] Step 4: Run python -m pytest tests/test_technical_interview.py -q and confirm all service contracts pass.

- [x] Step 5: Commit the service and tests as [feat] add technical interview domain service.

### Task 2: Dedicated technical interview MCP workflow

**Files:**
- Modify: tools/interview_mcp.py
- Create: tests/test_technical_interview_workflow.py

**Interfaces:**
- Add MCP tools start_technical_interview(first_turn_json), record_technical_interview_answer(session_id, transcript, event_id, next_turn_json, finish_after_answer=False), select_technical_stack(session_id, stack, event_id, first_turn_json), technical_interview_status(session_id), finish_technical_interview(session_id, report_json), cancel_technical_interview(session_id), and delete_technical_interview(session_id).
- Start accepts one validated calibration question and returns the session ID, opening script, current phase, and tool-use instructions.
- Record returns the saved candidate turn and validated next turn, or next_action=select_stack / finish_interview when the phase boundary or answer limit is reached.
- Stack selection stores the stack and the exact selected stack as a candidate turn before accepting the first validated deep-dive question. Invalid proposals leave both saved so the question can be retried with the same event_id.
- Finish returns a rendered Markdown report only after successful evidence validation.

- [x] Step 1: Write failing MCP workflow tests for start, full calibration-to-stack-to-deep-dive flow, exact-once answer persistence, invalid-proposal retry, status transcript, early finish, invalid report evidence, cancellation, and deletion.

- [x] Step 2: Run python -m pytest tests/test_technical_interview_workflow.py -q and confirm the new tool contract is absent.

- [x] Step 3: Register the dedicated service/session registry and MCP tools in tools/interview_mcp.py. Return only scenario-specific public session data, keep error handling consistent with existing tools, and never expose internal JSON arguments to the candidate.

- [x] Step 4: Run python -m pytest tests/test_technical_interview_workflow.py tests/test_voice_interview_workflow.py -q and confirm new and existing voice workflows pass.

- [x] Step 5: Commit the MCP workflow and tests as [feat] expose technical interview through MCP.

### Task 3: Voice skill and entry-point documentation

**Files:**
- Create: .agents/skills/technical-stack-interviewer/SKILL.md
- Modify: README.md
- Test: tests/test_technical_interview_workflow.py

**Interfaces:**
- The skill uses only the Task 2 MCP tools and must not ask the user to provide tool arguments or JSON.
- The skill begins with a brief voice-mode check, runs three to four adaptive calibration answers, states the evidence-based provisional level, then asks the candidate to name or choose a stack.
- Candidate self-assessment may be recorded as context, but it does not override the level supported by answers.
- It asks stack-specific internals questions, one at a time, and adapts follow-ups. It must not reveal hidden scoring criteria or ideal answers.
- README shows how to start the new skill alongside the existing two scenario skills.

- [x] Step 1: Add skill contract assertions for the order (calibrate, assess, stack, deep dive, report), Codex Voice requirement, arbitrary stack support, one-question-at-a-time rule, evidence-only report, early-stop behavior, and cancellation/deletion. Confirm the assertions fail before the skill exists.

- [x] Step 2: Write the skill with exact MCP sequencing, retry behavior for saved answers, user-language reporting, and explicit practice-feedback framing; add the README entry point.

- [x] Step 3: Run the skill contract test with python -m pytest tests/test_technical_interview_workflow.py -q and confirm it passes.

- [x] Step 4: Commit the voice skill and documentation as [feat] add technical interview voice skill.

### Task 4: Full regression and final review

**Files:**
- Review: all files changed in Tasks 1–3.

**Interfaces:**
- No new interfaces. Existing Backend Screening and test-interview contracts remain unchanged.

- [ ] Step 1: Run python -m pytest and confirm the full existing and new suite passes.
- [ ] Step 2: Review git diff origin/main...HEAD for unrelated files, leaked candidate data, missing report citations, or changes to existing scenario behavior.
- [ ] Step 3: Confirm the active branch is left-try/feature/technical-interview and report the test result and any remaining limitations.
