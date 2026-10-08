from __future__ import annotations

import json

import httpx
import pytest

from mock_interviewer.model_settings import InterviewModelSettings
from mock_interviewer.subscription_inference import ChatGPTPlanClient


class Auth:
    def status(self):
        return type("Status", (), {"account_id": "account-one"})()
    def get_access_token(self):
        return "token"


@pytest.mark.asyncio
async def test_model_roles_persist_and_revalidate_against_catalog(tmp_path):
    catalog = {"models": [
        {"slug": "fast", "visibility": "list", "supported_reasoning_efforts": ["low"]},
        {"slug": "strong", "visibility": "list", "supported_reasoning_efforts": ["medium", "high"]},
    ]}
    client = ChatGPTPlanClient(auth=Auth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=catalog)
    )))
    await client.list_models()
    settings = InterviewModelSettings("fast", "low", "strong", "medium")
    path = tmp_path / "models.json"

    settings.save(path, account_id="account-one")
    loaded = InterviewModelSettings.load(path, account_id="account-one", client=client)

    assert loaded == settings
    assert json.loads(path.read_text()) == {
        "account_id": "account-one", "fast_model": "fast", "fast_effort": "low",
        "analysis_model": "strong", "analysis_effort": "medium",
    }


@pytest.mark.asyncio
async def test_reasoning_effort_must_be_supported_by_selected_model():
    client = ChatGPTPlanClient(auth=Auth(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"models": [{"slug": "fast", "visibility": "list",
                                                          "supported_reasoning_efforts": ["low"]}]})
    )))
    await client.list_models()

    with pytest.raises(ValueError, match="not supported"):
        InterviewModelSettings("fast", "high", "fast", "low").validate(client)
