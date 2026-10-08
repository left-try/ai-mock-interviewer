# Subscription-Routed Live Interview Design

## Goal

Keep the interview adaptive and conversational while shortening the wait after each answer and reducing the work left for final report generation. All model inference remains within the user’s ChatGPT subscription plan.

## Approved approach

Keep Codex Voice as the speech interface. Add a local MCP-side inference adapter authenticated through the documented Sign in with ChatGPT plan-usage OAuth flow. The MCP service discovers models available to that account and routes live turn generation to a selected fast model with a low supported reasoning effort. It routes independent per-answer evaluation work to a selected stronger model in background tasks. Neither model is hard-coded to an unavailable account model.

For every answer, the voice host begins with one brief, answer-specific listening reaction while it prepares the tool call; it does not repeat the question or claim that evaluation is complete. The MCP server then persists the exact recognized transcript before inference. The fast model receives that answer and a compact interview state, then returns an updated state and one adaptive next question. The original transcript remains the source for report quotes. Background evaluation returns per-answer scores, exact evidence quotes, observations, and uncertainty; the service validates quotes and aggregates results locally when the interview ends. PDF export remains local and synchronous because its measured time is already small.

Use the ChatGPT plan OAuth access token with the public Responses endpoint, dynamic account model discovery, `store=false`, and streaming responses. Do not use an API key, API billing, or send transcripts to diagnostics. Store OAuth credentials in the operating system credential store; never log credentials.

## Constraints

- The user must explicitly connect a ChatGPT account and grant the ChatGPT plan-usage permission before subscription inference starts.
- The account’s available models and plan limits are authoritative; model selection and reasoning effort must be validated against that catalog.
- The Codex Voice host still controls spoken playback and may need its own faster model selected in the active task. The MCP server cannot change that host setting.
- The immediate listening reaction is spoken once before the MCP wait; the backend must not return a second acknowledgment that would repeat it.
- An answer is durable before inference begins. If fast inference fails, the answer remains saved and the same event can retry without duplication.
- Background analysis failure must not lose an answer or prevent the interview from ending; the report must state when evaluation evidence is incomplete.
- Only exact candidate-turn excerpts may appear as report evidence, and every quote must pass existing quote validation.
- Test diagnostics record timings, model role, and effort, never answer text, prompts, OAuth tokens, or authorization codes.

## Latency and quality checks

Measure server-observed fast-model time-to-first-token and completion time, total answer-to-next-turn-ready time, background-analysis completion and final wait, report assembly, PDF export, and MCP call time separately. Preserve client-gap data only as a combined speech, recognition, host, and client interval; do not label it model inference.

Compare the current flow with the subscription-routed flow on at least five matched synthetic interview runs. The target is at least a 50% reduction in median answer-to-next-turn-ready time, no increase in repeated questions or lost answers, valid report citations, and no material decline in interview relevance during human review. Treat p95 values from fewer than twenty samples as preliminary.

## Official references

- [Sign in with ChatGPT: registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [ChatGPT plan usage: models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Codex app-server with ChatGPT plan authorization](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
- [Reasoning models and effort](https://developers.openai.com/api/docs/guides/reasoning)
- [ChatGPT Voice in Codex](https://learn.chatgpt.com/docs/features/voice)
