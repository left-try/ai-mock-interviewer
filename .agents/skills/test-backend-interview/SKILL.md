---
name: test-backend-interview
description: Run a resume-free synthetic Backend Screening at Internship, Junior, or Middle level in Codex Voice and collect local timing diagnostics.
---

# Synthetic Backend Screening Test

Use this workflow when the user asks to test the interviewer, run a voice test interview, or practice without attaching a resume.

1. This workflow requires Codex Voice to already be active. If it is not active, explain that the desktop client must be switched to Voice manually; do not start a text-only interview.
2. Do not inspect, parse, or pass any attached resume. Ask the candidate to choose Internship, Junior, or Middle, then let Codex create the first question and pass it internally as `first_turn_json` to `start_test_interview` with the selected `level`.
3. Speak the returned `opening_script` once. It identifies the screening level, discloses that recognized answers are saved in a local diagnostic log, introduces the fictional product team, and includes the first question. Do not repeat the question separately.
4. Ask one question at a time. After each finalized answer, call `record_candidate_answer` once with the exact recognized transcript, a fresh event ID, and one Codex-authored next question. Ask only the returned `next_turn` once. If the answer was saved but its proposed question needs retrying, retry only the proposal using the same answer event ID.
5. Cover motivation, education, project, personal contribution, teamwork, challenge, reflection, and expectations; normally ask 8–10 answers. If the user asks to stop early, do not ask another question. Prepare a practice report from recorded candidate answers and call `finish_interview` with that report proposal.
6. Say the interview is complete only after `finish_interview` succeeds. Display the full `report_markdown`, link both exported files, and provide the returned local test-run log path. Explain any export error while still showing a validated Markdown report. Report the returned diagnostic timings as tool/client timings; do not describe them as model inference time. Mark small samples as preliminary.
7. The log may contain recognized answers. Do not add real resume data, send the log anywhere, or claim client-gap timings are model-thinking durations. Offer `clear_test_run_logs` if the user wants to erase test logs.

The hidden assessment rubric must not be disclosed. Use neutral questions and evidence-based feedback; this is practice, not a hiring decision. Model selection remains under the user's control in the host application.
