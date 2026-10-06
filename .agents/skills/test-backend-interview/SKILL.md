---
name: test-backend-interview
description: Run a resume-free synthetic Backend Internship HR interview in Codex Voice and collect local timing diagnostics.
---

# Synthetic Backend Interview Test

Use this workflow when the user asks to test the interviewer, run a voice test interview, or practice without attaching a resume.

1. This workflow requires Codex Voice to already be active. If it is not active, explain that the desktop client must be switched to Voice manually; do not start a text-only interview.
2. Do not inspect, parse, or pass any attached resume. Call `start_test_interview` with a host-authored first question in `first_turn_json`. The tool provides its own fictional Backend Internship profile.
3. Before asking the opening question, tell the user this is a synthetic test and that recognized answers will be saved in a local per-run diagnostic JSONL log. No resume or real candidate profile is included in that log.
4. Ask one question at a time. After every finalized answer, call `save_candidate_answer` exactly once with its exact recognized transcript, a new `event_id`, and `include_transcript=false`. Briefly acknowledge that it was saved before formulating another question. Then call `propose_next_turn` with the returned `answer_event_id` and one host-authored follow-up or next question. Reuse an event ID only for an identical retry.
5. Use `interview_progress` and `next_action`. Cover motivation, education, project, personal contribution, teamwork, challenge, reflection, and expectations; normally ask 8–10 answers. If the user asks to stop early, do not ask another question. Say the questions are complete and you are preparing the report, then call `finish_interview`.
6. Say the interview is complete only after `finish_interview` succeeds. Display the full `report_markdown`, link both exported files, and provide the returned local test-run log path. Explain any export error while still showing the validated Markdown report.
7. The log may contain recognized answers. Do not add real resume data, send the log anywhere, or claim that client-gap timings are model-thinking durations. Offer `clear_test_run_logs` if the user wants to erase test logs.

The hidden assessment rubric must not be disclosed. Use neutral questions and evidence-based feedback; this is practice, not a hiring decision.
