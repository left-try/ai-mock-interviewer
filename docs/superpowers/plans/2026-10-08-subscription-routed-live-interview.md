# Subscription-Routed Live Interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route adaptive interview turns through a fast model and per-answer report evaluation through a stronger model using the user’s ChatGPT subscription authorization, while preserving Codex Voice and raw transcript evidence.

**Architecture:** The local MCP server obtains a user-approved ChatGPT plan OAuth token, discovers account-available models, and uses the Responses API for fast structured next-turn generation. Each exact answer is saved before inference; background evaluation tasks analyze that answer independently, and local code validates and aggregates evidence into the final report. Codex Voice remains the audio interface and reads the acknowledgment and question returned by MCP.

**Tech Stack:** Python 3.11+, asyncio, MCP Python SDK, Pydantic v2, `httpx`, PyJWT with cryptography support, OS credential store via `keyring`, ChatGPT plan OAuth, Responses API, existing LangGraph service, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-subscription-routed-live-interview-design.md`

## Global Constraints

- Use ChatGPT plan OAuth with `resource.invoke` and `chatgpt.tokens.use.direct`; do not require or accept an API key.
- Set `store=false` on every Responses request and never write transcripts, prompts, tokens, authorization codes, or refresh tokens to diagnostics.
- Keep exact recognized answers as durable source text; derive compact context separately and never use a paraphrase as a report quote.
- Validate model slugs and reasoning effort against the connected account’s available model catalog.
- Preserve event idempotency and pending-answer retry behavior when model inference fails.
- Run background report analysis independently of fast question generation; failed analysis may lower report confidence but cannot discard a candidate answer.
- Preserve current interview topics, duplicate-question checks, evidence criteria, quote validation, and PDF layout.

## Review Focus

- OAuth callback state, nonce, PKCE verifier, client ID, and account identity mismatch: reject the login and do not save credentials.
- Expired access token or rotated refresh token: refresh once, persist the replacement atomically, then retry only safe inference requests.
- Subscription model unavailable or usage limit reached: keep the candidate answer saved and return a clear retryable state without duplicating it.
- Background evaluations finish out of order or fail: aggregate deterministically by candidate turn and report missing evidence as uncertainty.
- Fast-model output repeats a question or proposes an invalid topic: reject it while preserving the saved answer for retry; pin this with `test_invalid_fast_turn_keeps_answer_retryable` in Task 4.
- Fast-model context becomes stale or contradicts the raw answer: keep the raw transcript authoritative and pin this with `test_fast_turn_uses_compact_state_without_rewriting_transcript` in Task 4.
- OAuth account denies plan inference or lacks the requested scope: fail before starting an interview; pin this with `test_login_requires_chatgpt_plan_usage_scope` in Task 2.
- Background analysis is still running at finish or fails: bound the final wait, retain successful evidence, and disclose missing analysis; pin this with `test_finish_waits_for_pending_evaluations_and_marks_failures` in Task 5.

---

### Task 1: Add inference and turn-stage latency diagnostics

**Files:**
- Modify: `src/mock_interviewer/test_run_log.py`
- Modify: `src/mock_interviewer/test_run_summary.py`
- Modify: `tools/interview_mcp.py`
- Test: `tests/test_test_run_log.py`
- Test: `tests/test_test_run_summary.py`

**Interfaces:**
- Add timing events for `fast_model_ttft`, `fast_model_completion`, `background_evaluation`, `background_wait`, and `turn_ready`.
- Record only model role, selected model slug, reasoning effort, response usage counts when present, and durations; omit all content and credentials.

- [ ] Add `test_subscription_metrics_are_summarized_by_stage`, `test_invalid_subscription_durations_are_reported`, and `test_client_gap_warning_never_claims_host_model_latency` in `tests/test_test_run_summary.py`; assert stage metrics remain distinct and no transcript field is needed.
- [ ] Add `test_timing_events_record_only_safe_metadata` in `tests/test_test_run_log.py`; assert event records contain no transcript, prompt, or credential values.
- [ ] Implement monotonic timing at the subscription-client and MCP service boundaries; preserve the existing `client_gap` warning and label.
- [ ] Confirm summaries mark percentiles preliminary below twenty samples for the benchmark output.
- [ ] Run `pytest tests/test_test_run_log.py tests/test_test_run_summary.py -q`; expect all diagnostic contract tests to pass.
- [ ] Commit as `[perf] measure interview model stages separately`.

### Task 2: Add ChatGPT plan OAuth and secure credential lifecycle

**Files:**
- Create: `src/mock_interviewer/subscription_auth.py`
- Modify: `pyproject.toml`
- Modify: `tools/interview_mcp.py`
- Test: `tests/test_subscription_auth.py`

**Interfaces:**
- `ChatGPTPlanAuth.begin_login() -> LoginAttempt`
- `ChatGPTPlanAuth.complete_login(callback_query: Mapping[str, str]) -> AccountStatus`
- `ChatGPTPlanAuth.get_access_token() -> str`
- `ChatGPTPlanAuth.disconnect(account_id: str | None = None) -> None`
- Expose MCP tools `connect_chatgpt_plan`, `chatgpt_plan_status`, and `disconnect_chatgpt_plan`.

- [ ] Add mocked tests `test_login_uses_pkce_and_loopback_callback`, `test_callback_rejects_state_or_nonce_mismatch`, `test_login_requires_chatgpt_plan_usage_scope`, `test_login_rejects_account_mismatch`, `test_denied_consent_does_not_store_tokens`, `test_expired_access_token_refreshes_once`, `test_refresh_replaces_rotated_refresh_token_atomically`, and `test_credential_store_failure_fails_closed` in `tests/test_subscription_auth.py`.
- [ ] Implement the public-client authorization-code flow with a loopback callback, exact callback URI reuse, dynamic registration, JWKS ID-token validation, and the required plan-usage scopes.
- [ ] Persist tokens only through the OS credential store; do not provide a plaintext-file fallback.
- [ ] Add `httpx`, `PyJWT[crypto]`, and `keyring` as bounded dependencies in the `voice-probe` extra; keep the base package free of desktop auth dependencies.
- [ ] Add `connect_chatgpt_plan` MCP tool that opens the system browser and completes authorization only after user consent; return status without exposing tokens.
- [ ] Run `pytest tests/test_subscription_auth.py -q`; expect auth and refresh behavior covered with mocked HTTP and credential-store adapters.
- [ ] Commit as `[feat] authorize ChatGPT plan inference`.

### Task 3: Implement account model discovery and structured Responses client

**Files:**
- Create: `src/mock_interviewer/subscription_inference.py`
- Create: `src/mock_interviewer/model_settings.py`
- Modify: `tools/interview_mcp.py`
- Test: `tests/test_subscription_inference.py`
- Test: `tests/test_model_settings.py`

**Interfaces:**
- `ChatGPTPlanClient.list_models() -> list[AvailableModel]`
- `ChatGPTPlanClient.create_structured_response(*, model: str, effort: str, input: list[dict], schema: dict, max_output_tokens: int) -> StructuredResult`
- `InterviewModelSettings(fast_model: str, fast_effort: str, analysis_model: str, analysis_effort: str)`
- Expose `list_chatgpt_plan_models` and `configure_interview_models` MCP tools.

- [ ] Add `test_model_catalog_is_account_specific`, `test_unavailable_model_or_effort_is_rejected`, `test_structured_stream_requires_completed_response`, `test_responses_requests_disable_storage`, `test_usage_limit_error_is_retryable`, `test_interrupted_stream_is_not_success`, and `test_credentials_never_appear_in_errors_or_logs` in `tests/test_subscription_inference.py`.
- [ ] Add `test_model_roles_persist_and_revalidate_against_catalog` and `test_reasoning_effort_must_be_supported_by_selected_model` in `tests/test_model_settings.py`.
- [ ] Implement Responses requests using only the saved OAuth bearer token and public `/v1/models` and `/v1/responses` endpoints.
- [ ] Use `instructions`/developer input rather than an input message with role `system`; omit `max_output_tokens` because the ChatGPT plan preview rejects it. Bound output with the strict JSON schema and validate the completed response locally.
- [ ] Use dynamic model catalog validation; default fast effort to `low` and analysis effort to `medium` only when the chosen models advertise those settings, otherwise require a valid setting from the user.
- [ ] Keep model choices in a small local configuration record; validate every load against the refreshed account catalog before inference.
- [ ] Capture time-to-first-token and full completion time without retaining streamed content in diagnostics.
- [ ] Run `pytest tests/test_subscription_inference.py tests/test_model_settings.py -q`; expect all HTTP interactions to use mocked transports.
- [ ] Commit as `[feat] route interview inference through ChatGPT plan`.

### Task 4: Move adaptive question generation into the fast subscription path

**Files:**
- Modify: `src/mock_interviewer/domain/models.py`
- Modify: `src/mock_interviewer/service.py`
- Modify: `src/mock_interviewer/prompts.py`
- Modify: `tools/interview_mcp.py`
- Modify: `.agents/skills/backend-internship-interviewer/SKILL.md`
- Modify: `.agents/skills/test-backend-interview/SKILL.md`
- Modify: `tools/voice_probe.py`
- Test: `tests/test_voice_interview_workflow.py`
- Test: `tests/test_test_interview_workflow.py`
- Test: `tests/test_answer_first_contract.py`

**Interfaces:**
- Add `InterviewContextState` with compact `candidate_facts`, `covered_topics`, and `open_threads`; retain exact `Turn.text` unchanged.
- Add `FastTurnResult(acknowledgment: str, state: InterviewContextState, next_turn: NextTurn)`.
- `InterviewService.start(..., fast_model=...)` obtains the first turn from the subscription model.
- `InterviewService.generate_next_turn(session_id, answer_event_id) -> FastTurnResult` operates on a previously persisted answer and keeps retries idempotent.
- `record_candidate_answer` accepts only `session_id`, `event_id`, and exact `transcript`; return one `acknowledgment` and one `next_turn` or a retryable saved-answer state.
- `finish_interview` accepts only the session ID; report generation is handled by Task 5.

- [ ] Add `test_start_uses_subscription_fast_model`, `test_fast_turn_uses_compact_state_without_rewriting_transcript`, and `test_fast_turn_returns_one_adaptive_question` in `tests/test_voice_interview_workflow.py`.
- [ ] Add `test_answer_is_saved_before_fast_inference`, `test_replayed_event_does_not_enqueue_duplicate_inference`, and `test_fast_inference_failure_keeps_answer_retryable` in `tests/test_answer_first_contract.py`.
- [ ] Add `test_invalid_fast_turn_keeps_answer_retryable`, `test_fast_turn_cannot_repeat_previous_question`, and `test_test_interview_opening_keeps_synthetic_disclosure` in `tests/test_test_interview_workflow.py`.
- [ ] Implement the structured fast-turn schema and compact state update; return exactly one validated adaptive question without a second acknowledgment.
- [ ] Make the server, not the Codex host prompt, choose and validate the next question; update MCP tool schemas and skill instructions to speak one concise, answer-specific listening reaction before the tool call, then ask the returned question once.
- [ ] Keep the opening script concise and deterministic after first-question generation; preserve the fictional product-team framing and synthetic-run disclosure.
- [ ] Run `pytest tests/test_voice_interview_workflow.py tests/test_test_interview_workflow.py tests/test_answer_first_contract.py -q`; expect saved-answer, retry, duplicate-question, and single-question contracts to pass.
- [ ] Commit as `[feat] generate adaptive interview turns with fast model`.

### Task 5: Evaluate each answer in the background and assemble reports locally

**Files:**
- Create: `src/mock_interviewer/background_evaluation.py`
- Create: `src/mock_interviewer/report_aggregation.py`
- Modify: `src/mock_interviewer/domain/models.py`
- Modify: `src/mock_interviewer/service.py`
- Modify: `tools/interview_mcp.py`
- Test: `tests/test_background_evaluation.py`
- Test: `tests/test_report_aggregation.py`
- Test: `tests/test_report_exports.py`

**Interfaces:**
- `AnswerEvaluation(turn_id: str, scores: dict[str, int | None], evidence: list[Evidence], strengths: list[str], growth_areas: list[str], uncertainties: list[str])`.
- `BackgroundEvaluationQueue.enqueue(session_id: str, candidate_turn: Turn, context: InterviewContextState) -> None`.
- `BackgroundEvaluationQueue.finish_session(session_id: str) -> list[AnswerEvaluation]` waits for pending jobs in bounded parallelism and reports individual failures without discarding successful results.
- `aggregate_report(evaluations: Sequence[AnswerEvaluation], turns: Sequence[Turn]) -> InterviewReport` deterministically sorts by candidate turn, validates score range and exact quote source, and marks incomplete analysis in uncertainties.

- [ ] Add `test_background_evaluation_runs_without_blocking_next_question`, `test_out_of_order_evaluations_are_sorted_by_turn`, `test_failed_evaluation_does_not_fail_interview`, `test_cancel_cleans_up_pending_tasks`, `test_evidence_quote_must_match_exact_candidate_turn`, and `test_background_evaluation_respects_concurrency_limit` in `tests/test_background_evaluation.py`.
- [ ] Add `test_report_aggregation_is_deterministic`, `test_scores_average_only_observed_criteria`, `test_duplicate_evidence_is_removed`, `test_incomplete_evaluations_add_uncertainty`, and `test_report_disclaimer_is_preserved` in `tests/test_report_aggregation.py`.
- [ ] Enqueue one analysis per saved answer with the selected stronger subscription model; send only the relevant question, current raw answer, compact context, and internal rubric.
- [ ] Validate all returned evidence against the exact candidate turn before storing it; ignore invalid evidence and record uncertainty rather than accepting an unsupported quote.
- [ ] At finish, wait only for outstanding background tasks, aggregate locally, run existing report validation, render Markdown, and export PDF.
- [ ] Add `test_finish_waits_for_pending_evaluations_and_marks_failures` in `tests/test_voice_interview_workflow.py`; verify finalization includes completed evaluations and reports failed ones as uncertainty.
- [ ] Keep PDF export synchronous and report timings separate; preserve the existing quote-block layout fix.
- [ ] Run `pytest tests/test_background_evaluation.py tests/test_report_aggregation.py tests/test_report_exports.py -q`; expect local report and export contracts to pass without network access.
- [ ] Commit as `[feat] build reports from incremental background evaluations`.

### Task 6: Complete MCP integration and benchmark the voice workflow

**Files:**
- Modify: `tools/interview_mcp.py`
- Modify: `tools/voice_probe.py`
- Modify: `.agents/skills/backend-internship-interviewer/SKILL.md`
- Modify: `.agents/skills/test-backend-interview/SKILL.md`
- Modify: `docs/GETTING_STARTED.md`
- Modify: `docs/VOICE_PROBE.md`
- Modify: `docs/TEST_PLAN.md`
- Test: `tests/test_public_contracts.py`
- Test: `tests/test_test_run_summary.py`
- Test: `tests/test_voice_interview_workflow.py`

**Interfaces:**
- Startup returns auth status, available fast/analysis model roles, and the opening script; it does not perform model discovery during the live turn.
- The voice skill tells Codex to use a brief contextual acknowledgment, call MCP once per finalized transcript, then speak only the returned question once.
- Test-run summary reports server model and export metrics while explicitly labeling client gap as combined speech, recognition, host, and client time.

- [ ] Add `test_subscription_tool_contracts_are_public_and_minimal`, `test_finish_interview_no_longer_requires_host_report_json`, and `test_tool_outputs_never_expose_credentials_or_prompt_text` in `tests/test_public_contracts.py`.
- [ ] Update setup instructions for one-time ChatGPT plan authorization, model selection, supported reasoning levels, disconnect, and subscription-limit errors.
- [ ] Remove host-authored `first_turn_json`, `next_turn_json`, and `report_json` from the normal supported workflow; keep any migration shim explicitly bounded and documented if existing clients require it.
- [ ] Run the complete automated suite with mocked inference and no network credentials; expect the whole repository suite to pass.
- [ ] Run five matched synthetic voice interviews before and after the change; review repeated-question rate, quote validity, relevance, answer-to-next-turn-ready median, fast-model TTFT/completion, background wait, and final export duration.
- [ ] During the voice benchmark, confirm the candidate-specific listening reaction begins before the MCP response arrives, is not repeated by the tool result, and does not restate the next question.
- [ ] Accept only if median answer-to-next-turn-ready time improves by at least 50%, no answers are lost or duplicated, and human review finds no material decline in relevance; otherwise adjust model roles/effort before enabling the new path by default.
- [ ] Commit documentation and integration changes as `[docs] configure subscription-routed voice interviews`.

## Execution Notes

- Task 1 establishes comparable latency instrumentation before changing model routing.
- Tasks 2 and 3 provide reusable subscription access before service integration.
- Task 4 and Task 5 can then share the same model client but must keep fast-turn response independent from background evaluation completion.
- Task 6 is the release gate. Do not describe model inference time using `client_gap` measurements.

## Execution Rulings

- Task 3: The initial interface proposed `max_output_tokens`, but the current ChatGPT plan preview explicitly rejects that field. Omit it on the wire; retain the code-level parameter only as a local contract until output limits are supported. Use strict schema validation for bounded structured output.
- Task 3: The preview also rejects `role: system` in `input`; prompts must use `instructions` or developer messages.
