from __future__ import annotations

import json

import httpx
import pytest

from mock_interviewer.subscription_inference import ChatGPTPlanClient, InferenceError


class FakeAuth:
    def __init__(self, token="access-secret"):
        self.token = token

    def get_access_token(self):
        return self.token


def _sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


@pytest.mark.asyncio
async def test_model_catalog_is_account_specific():
    seen = []
    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"models": [
            {"slug": "fast-a", "display_name": "Fast A", "visibility": "list", "supported_reasoning_efforts": ["low"]},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
        ]})
    client = ChatGPTPlanClient(auth=FakeAuth("account-a"), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    models = await client.list_models()

    assert [model.slug for model in models] == ["fast-a"]
    assert models[0].reasoning_efforts == ("low",)
    assert seen == ["Bearer account-a"]


@pytest.mark.asyncio
async def test_unavailable_model_or_effort_is_rejected():
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list",
                                                         "supported_reasoning_efforts": ["low"]}]})
    )))
    await client.list_models()

    with pytest.raises(ValueError):
        client.validate_selection("not-available", "low")
    with pytest.raises(ValueError):
        client.validate_selection("fast", "high")


@pytest.mark.asyncio
async def test_structured_stream_requires_completed_response():
    body = _sse(
        {"type": "response.output_text.delta", "delta": '{"question":"next"}'},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 20, "output_tokens": 5}}},
    )
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list"}]})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.list_models()
    result = await client.create_structured_response(
        model="fast", effort="low", input=[{"role": "user", "content": "next"}],
        schema={"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
        max_output_tokens=80,
    )

    assert result.value == {"question": "next"}
    assert result.usage == {"input_tokens": 20, "output_tokens": 5}
    assert result.ttft_ms >= 0 and result.duration_ms >= result.ttft_ms


@pytest.mark.asyncio
async def test_responses_requests_disable_storage():
    requests = []
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list"}]})
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=_sse(
            {"type": "response.output_text.delta", "delta": '{"ok":true}'},
            {"type": "response.completed", "response": {"usage": {}}},
        ), headers={"content-type": "text/event-stream"})
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.list_models()
    await client.create_structured_response(model="fast", effort="low", input=[{"role": "user", "content": "go"}],
                                             schema={"type": "object"}, max_output_tokens=10)

    assert requests[0]["store"] is False
    assert requests[0]["stream"] is True
    assert "max_output_tokens" not in requests[0]
    assert requests[0]["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_usage_limit_error_is_retryable():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list"}]})
        return httpx.Response(200, text=_sse({"type": "response.failed", "response": {
            "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "secret"}}}),
            headers={"content-type": "text/event-stream"})
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.list_models()

    with pytest.raises(InferenceError) as raised:
        await client.create_structured_response(model="fast", effort="low", input=[], schema={"type": "object"}, max_output_tokens=10)
    assert raised.value.retryable is True
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_interrupted_stream_is_not_success():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list"}]})
        return httpx.Response(200, text=_sse({"type": "response.output_text.delta", "delta": '{"ok":true}'}),
                              headers={"content-type": "text/event-stream"})
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.list_models()

    with pytest.raises(InferenceError, match="completed"):
        await client.create_structured_response(model="fast", effort="low", input=[], schema={"type": "object"}, max_output_tokens=10)


@pytest.mark.asyncio
async def test_credentials_never_appear_in_errors_or_logs(caplog):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list"}]})
        return httpx.Response(401, text="access-secret refresh-secret")
    client = ChatGPTPlanClient(auth=FakeAuth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.list_models()

    with pytest.raises(InferenceError) as raised:
        await client.create_structured_response(model="fast", effort="low", input=[], schema={"type": "object"}, max_output_tokens=10)
    assert "access-secret" not in str(raised.value)
    assert "refresh-secret" not in caplog.text
