# Codex Voice → MCP: probe and interview MVP

The original tools in this server remain a temporary transcript probe. The same stdio server now also exposes the LangGraph-backed Backend Screening for Internship, Junior, and Middle. Probe calls only validate voice transcript delivery; interview calls validate turn proposals, candidate events, session state, and the report.

## Install and register

From the repository root in PowerShell, install the project and MCP dependencies:

    python -m pip install -e ".[test,voice-probe]"
    codex mcp add interviewer-probe -- python "C:\Users\Ivan\Documents\ChatGPT\ai-mock-interviewer\tools\voice_probe.py"
    codex mcp list

Restart Codex Desktop after registering the server. Confirm the interviewer-probe tools are available in a new Codex chat.

## Manual voice run

1. Start a new local Codex chat in this project and enable Voice.
2. To test transcript delivery, say: “Start the voice probe. Use the interviewer-probe tools. Record each completed sentence exactly once and do not paraphrase it.” For the actual interview, invoke the `backend-internship-interviewer` skill.
3. When the probe starts, use only synthetic sentences, for example:
   - “В учебном проекте я написал API на Python.”
   - “Поправка: не на Python, а на Go.”
   - “Я работал с PostgreSQL, версия пятнадцать.”
4. Ask Codex to end the probe, then call the status tool for that session.
5. Compare the displayed transcript with what you said and inspect the ordered turn numbers.
6. Call the clear-log tool after reviewing the result.

Do not use a real CV or sensitive personal information. The server writes transcripts to the ignored local file .voice-probe/events.jsonl so they can be inspected after the voice call. Delete the log with the clear-log tool or remove that file after the experiment.

## Pass criteria

- The tools are visible in Codex Desktop.
- Voice starts exactly one probe session.
- Each completed utterance causes exactly one record call.
- Russian words, numbers, corrections, and sentence order arrive correctly.
- Replaying the same event ID does not add a second turn.
- Ending the probe prevents later turns from being recorded.
- The returned text is visible in the chat, and the transcript file contains the same ordered turns.

This does not validate raw audio access, partial transcripts, speech latency, interruption timing, or reliable automatic tool use during a real interview. Codex Voice owns those behaviors.

## Resume-free interview and diagnostics

Start `/test-backend-interview` in Codex Voice and choose Internship, Junior, or Middle. The server uses a fictional profile for that level and does not inspect attached resumes. Before the opening question, it discloses that recognized answers are saved in a local per-run JSONL log under `%LOCALAPPDATA%\ai-mock-interviewer\test-runs`. Logs are retained for 30 days. `clear_test_run_logs` removes test-run logs without touching report or voice-probe files.

Analyze a log with `python scripts/summarize_test_run.py <log.jsonl>`. The summary reports distinct MCP stages and percentiles when there are at least five samples. `client_gap_ms` measures time between MCP calls and may include speech, recognition, and host work; it is not model thinking time or voice TTFT.

In the answer-first interview protocol, `save_candidate_answer` stores each finalized answer, the host acknowledges it, and `propose_next_turn` stores the next question. The older `record_candidate_answer` tool remains for compatibility. The report announces completion only after validation succeeds and contains data-derived topic coverage and individual score bars, without a hiring probability.

## Interview tools

The same MCP server publishes `start_interview`, `record_candidate_answer`, `interview_status`, `finish_interview`, `cancel_interview`, and `delete_interview`. Session state is held in memory. The Codex Voice host supplies structured next-turn and report proposals, so it can use its subscription model without an application API key. The service validates each proposal and prevents duplicate event IDs from adding another answer. It does not receive raw audio.
