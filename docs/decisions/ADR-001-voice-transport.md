# ADR-001: Voice transport for the first MVP

- Status: Chosen for MVP validation; integration capability must be smoke-tested.
- Date: 2026-09-28

## Context

The MVP is a personal proof of the interview concept, and the preferred first path uses an existing ChatGPT/Codex subscription rather than building a separate voice client and paying a second model bill. The product must preserve a hidden interview plan and keep session state/branching in the LangGraph-backed Python code.

An external application cannot assume that a ChatGPT subscription is API access. OpenAI documents ChatGPT and API billing as separate systems. A custom application using the Realtime API therefore needs API billing even when its developer has a ChatGPT plan.

## Decision

For the first voice pilot, run the experience **inside Codex Desktop Voice**:

1. A Codex Skill describes the interviewer protocol and requires the assistant to use the interview tools for every interview turn.
2. A local MCP server exposes a narrow interface backed by the Python interview service and LangGraph, such as start interview, submit candidate turn, get next turn directive, finish interview, and delete interview.
3. The graph owns session state, the hidden rubric, topic coverage, allowed transitions, follow-up limits, and evidence collection.
4. The Codex-hosted model handles the spoken-language turn using the graph’s current directive and returns its structured assessment/next-turn proposal to the graph. The graph validates and records it before the next user-facing turn.
5. Codex Voice handles microphone capture and spoken playback. The service receives finalized text turns through MCP; it does not receive raw audio or control audio streaming.

Keep tools narrow and make tool outputs explicit about which fields are internal and which text may be spoken. The Skill must tell the host model to ask one question, not reveal the plan, not improvise beyond the graph directive, and not score accent or voice qualities.

## Why this is the MVP choice

- It tests the desired spoken interview using the user's existing Codex/ChatGPT product and plan allowance.
- It avoids building a WebRTC/audio UI before the interview itself is validated.
- MCP gives the application a code boundary for session state and graph decisions.
- Voice and MCP tools are documented in Codex/ChatGPT product workflows, but exact availability can depend on the account, desktop build, workspace controls, and selected experience.

## Limitations and acceptance spike

- This is a Codex-hosted experience, not an embeddable interview product or a standalone web app.
- Raw audio, partial transcripts, audio timing, turn detection, and per-token interruption events are controlled by Codex Voice and are not exposed to the local application through this design.
- Tool calls are mediated by the host model. The Skill and narrow tool contracts must make the graph authoritative; a smoke test must establish that voice reliably calls the MCP tools on every turn and speaks only the returned/authorized prompt.
- This path does not provide a documented callback that lets the Python graph invoke the Codex subscription model as an ordinary in-process ModelProvider. The host model must send a constrained assessment/next-turn proposal through MCP. The existing test contract currently models an injected async ModelProvider, so the voice spike must prove a compatible bridge before implementation proceeds; otherwise the voice path and frozen test contract are incompatible.
- Voice usage consumes the applicable Work/Codex allowance; where flexible pricing applies, connected Voice time can be metered separately. It is subscription-based, but not guaranteed to be unlimited or zero-marginal-cost.
- If the host cannot call the local MCP server reliably, first try an officially supported connected/remote MCP path. Do not silently fall back to an undocumented subscription endpoint.

The voice spike passes only if a complete interview can be started, advanced through at least one follow-up, stopped, and reported while every candidate turn is recorded once in the graph; the local service receives the expected finalized transcript; hidden rubric data is not spoken; and usage/billing is visible under the expected plan.

## Future migration

If a standalone product needs reliable transcript events, direct turn-taking/interrupt control, predictable latency, or model-independent routing, build a dedicated voice client. LiveKit Agents can connect WebRTC clients to a speech-to-speech realtime model or a cascaded STT → LangGraph/model → TTS pipeline. Using OpenAI Realtime/API models is a separate API-billed path; ChatGPT subscription billing does not cover API usage.

## Sources

- [Use Voice with Work or Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex)
- [Codex and ChatGPT plan usage](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [OpenAI MCP tools for ChatGPT and Codex](https://developers.openai.com/plugins/concepts/mcp-server)
- [Separate ChatGPT and API billing](https://help.openai.com/en/articles/9039756)
- [LiveKit voice agents](https://docs.livekit.io/agents/start/voice-ai/)
