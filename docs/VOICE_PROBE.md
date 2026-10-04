# Codex Voice → MCP: probe and interview MVP

The original tools in this server remain a temporary transcript probe. The same stdio server now also exposes the LangGraph-backed internship HR interview. Probe calls only validate voice transcript delivery; interview calls validate turn proposals, candidate events, session state, and the report.

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

## Interview tools

The same MCP server publishes `start_interview`, `record_candidate_answer`, `interview_status`, `finish_interview`, `cancel_interview`, and `delete_interview`. Session state is held in memory. The Codex Voice host supplies structured next-turn and report proposals, so it can use its subscription model without an application API key. The service validates each proposal and prevents duplicate event IDs from adding another answer. It does not receive raw audio.
