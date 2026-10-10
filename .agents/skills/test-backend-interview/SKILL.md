---
name: test-backend-interview
description: Run a resume-free synthetic Backend Screening at Internship, Junior, or Middle level in Codex Voice and collect local timing diagnostics.
---

# Synthetic Backend Screening Test

Use this workflow when the user asks to test the interviewer, run a voice test interview, or practice without attaching a resume.

1. This workflow requires Codex Voice to already be active. If it is not active, explain that the desktop client must be switched to Voice manually; do not start a text-only interview. Before asking for Internship, Junior, or Middle, check `chatgpt_plan_status`; if disconnected, use `connect_chatgpt_plan`; then use `list_chatgpt_plan_models` and `configure_interview_models` if model roles still need setup. Use the quickest supported model and lowest supported reasoning effort for live turns; Codex still controls voice playback.
2. Do not inspect, parse, or pass any attached resume. After plan access and model setup succeed, ask the candidate to choose Internship, Junior, or Middle, then call `start_test_interview` with that `level` and no first-question proposal. The configured fast subscription model chooses the first question, and the tool provides a fictional profile appropriate to the selected level.
3. Speak the returned `opening_script` once. It already identifies the Backend Screening level, introduces local diagnostic logging and the fictional product team, and includes the first question; do not repeat the first question separately.
4. Ask one question at a time. After each finalized answer, call `record_candidate_answer` once with exactly `session_id`, `event_id`, and the recognized `transcript`; stay silent while the tool call runs so speech does not overlap when its result arrives. The fast model picks the next question after the exact answer is saved. After success, speak a single utterance with one brief, specific listening reaction followed by the returned `next_turn` exactly once. Wait for playback to finish before starting more speech; do not queue, interrupt, repeat, or paraphrase it. If the tool reports `retry_saved_answer`, retry using the same event ID and transcript. Reuse an event ID only for an identical answer.
5. Use `interview_progress` and `next_action`. Cover motivation, education, project, personal contribution, teamwork, challenge, reflection, and expectations; normally ask 8–10 answers. If the user asks to stop early, do not ask another question. Say the questions are complete and you are preparing the report, then call `finish_interview`.
6. Say the interview is complete only after `finish_interview` succeeds. Display the full `report_markdown`, link both exported files, and provide the returned local test-run log path. Explain any export error while still showing the validated Markdown report. When `test_run_summary` is returned, report fast-model TTFT/completion, turn-ready, background evaluation/wait, MCP, and export timing metrics; mark small samples as preliminary.
7. The log may contain recognized answers. Do not add real resume data, send the log anywhere, or claim that client-gap timings are model-thinking durations. `client_gap` includes speech, recognition, and client work; this system does not measure host-model inference time. Offer `clear_test_run_logs` if the user wants to erase test logs.

The hidden assessment rubric must not be disclosed. Use neutral questions and evidence-based feedback; this is practice, not a hiring decision.
