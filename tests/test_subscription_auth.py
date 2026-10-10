from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import json
import time

import httpx
import pytest

from mock_interviewer.subscription_auth import ChatGPTPlanAuth, SubscriptionAuthError


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def test_login_uses_pkce_and_loopback_callback():
    auth = ChatGPTPlanAuth(credentials=MemoryCredentials(), http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))

    attempt = auth.begin_login()
    query = parse_qs(urlparse(attempt.authorization_url).query)

    assert urlparse(attempt.redirect_uri).scheme == "http"
    assert urlparse(attempt.redirect_uri).hostname == "127.0.0.1"
    assert urlparse(attempt.redirect_uri).path == "/auth/callback"
    assert query["redirect_uri"] == [attempt.redirect_uri]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query
    assert query["client_id"] == ["dynamic_agent_client"]
    assert {"resource.invoke", "chatgpt.tokens.use.direct", "offline_access"}.issubset(set(query["scope"][0].split()))
    assert attempt.state and attempt.nonce and attempt.code_verifier


def test_callback_receipt_has_distinct_accessible_success_and_failure_states():
    from mock_interviewer.subscription_auth import _callback_page

    success = _callback_page(True).lower()
    failure = _callback_page(False).lower()

    assert "chatgpt plan is connected" in success
    assert "your chatgpt account could not be connected" in failure
    assert "sign-in received" not in success
    assert "sign-in received" not in failure
    for page in (success, failure):
        assert "<meta name=\"viewport\"" in page
        assert "<main" in page
        assert "return to codex" in page


def _token_client(tokens, calls=None):
    def respond(request):
        if calls is not None:
            calls.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json=tokens)
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": []})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(respond))


def _valid_tokens(scopes="openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"):
    return {"access_token": "access-value", "refresh_token": "refresh-value", "id_token": "signed-id-token",
            "token_type": "Bearer", "expires_in": 3600, "scope": scopes}


def test_callback_rejects_state_or_nonce_mismatch():
    auth = ChatGPTPlanAuth(credentials=MemoryCredentials(), http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login()

    try:
        auth.complete_login({"state": "wrong", "code": "code", "client_id": "oaiapp-issued"})
        assert False, "mismatched state must be rejected"
    except Exception as exc:
        assert "state" in str(exc).lower()
    auth.cancel_login(attempt)

    auth = ChatGPTPlanAuth(credentials=MemoryCredentials(), http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login()
    auth._validate_id_token = lambda *_: (_ for _ in ()).throw(SubscriptionAuthError("nonce mismatch"))
    try:
        auth.complete_login({"state": attempt.state, "code": "code", "client_id": "oaiapp-issued"})
        assert False, "nonce mismatch must be rejected"
    except Exception as exc:
        assert "nonce" in str(exc).lower()
    auth.cancel_login(attempt)


def test_login_requires_chatgpt_plan_usage_scope():
    auth = ChatGPTPlanAuth(credentials=MemoryCredentials(), http_client=_token_client(_valid_tokens("openid profile email")))
    attempt = auth.begin_login()
    auth._validate_id_token = lambda *_: {"sub": "account", "nonce": attempt.nonce}

    try:
        auth.complete_login({"state": attempt.state, "code": "code", "client_id": "oaiapp-issued"})
        assert False, "missing plan scope must be rejected"
    except Exception as exc:
        assert "scope" in str(exc).lower()
    auth.cancel_login(attempt)


def test_login_rejects_account_mismatch():
    auth = ChatGPTPlanAuth(credentials=MemoryCredentials(), http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login(expected_account_id="expected-account")
    auth._validate_id_token = lambda *_: {"sub": "different-account", "nonce": attempt.nonce}

    try:
        auth.complete_login({"state": attempt.state, "code": "code", "client_id": "oaiapp-issued"})
        assert False, "another account must not replace selected account"
    except Exception as exc:
        assert "match" in str(exc).lower()
    auth.cancel_login(attempt)


def test_denied_consent_does_not_store_tokens():
    credentials = MemoryCredentials()
    auth = ChatGPTPlanAuth(credentials=credentials, http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login()

    try:
        auth.complete_login({"state": attempt.state, "error": "access_denied"})
        assert False, "denied consent must be rejected"
    except Exception:
        pass

    assert auth.status().connected is False
    assert not any(":tokens:" in key for key in credentials.values)
    auth.cancel_login(attempt)


def test_expired_access_token_refreshes_once():
    calls = []
    credentials = MemoryCredentials()
    account = "acct"
    credentials.set("active_account", account)
    credentials.set(f"account:{account}:metadata", json.dumps({"client_id": "oaiapp-issued", "expires_at": 0,
                                                                "scopes": _valid_tokens()["scope"].split(), "token_version": "old"}))
    credentials.set(f"account:{account}:tokens:old:refresh", "old-refresh")
    auth = ChatGPTPlanAuth(credentials=credentials, http_client=_token_client(_valid_tokens(), calls))

    assert auth.get_access_token() == "access-value"
    assert len(calls) == 1


def test_refresh_replaces_rotated_refresh_token_atomically():
    credentials = MemoryCredentials()
    account = "acct"
    credentials.set("active_account", account)
    credentials.set(f"account:{account}:metadata", json.dumps({"client_id": "oaiapp-issued", "expires_at": 0,
                                                                "scopes": _valid_tokens()["scope"].split(), "token_version": "old"}))
    credentials.set(f"account:{account}:tokens:old:access", "expired")
    credentials.set(f"account:{account}:tokens:old:refresh", "old-refresh")
    tokens = {**_valid_tokens(), "access_token": "new-access", "refresh_token": "rotated-refresh"}
    auth = ChatGPTPlanAuth(credentials=credentials, http_client=_token_client(tokens))

    assert auth.get_access_token() == "new-access"
    metadata = json.loads(credentials.get(f"account:{account}:metadata"))
    new_version = metadata["token_version"]
    assert credentials.get(f"account:{account}:tokens:{new_version}:access") == "new-access"
    assert credentials.get(f"account:{account}:tokens:{new_version}:refresh") == "rotated-refresh"
    assert json.loads(credentials.get(f"account:{account}:metadata"))["expires_at"] > time.time()
    assert credentials.get(f"account:{account}:tokens:old:refresh") is None


def test_credential_store_failure_fails_closed():
    class BrokenCredentials(MemoryCredentials):
        def set(self, key, value):
            if key.endswith(":refresh"):
                raise OSError("credential store unavailable")
            super().set(key, value)

    credentials = BrokenCredentials()
    auth = ChatGPTPlanAuth(credentials=credentials, http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login()
    auth._validate_id_token = lambda *_: {"sub": "account", "nonce": attempt.nonce, "email": "candidate@example.test"}

    try:
        auth.complete_login({"state": attempt.state, "code": "code", "client_id": "oaiapp-issued"})
        assert False, "credential storage failure must fail closed"
    except Exception as exc:
        assert "securely stored" in str(exc)
        assert getattr(exc, "stage", None) == "credential_storage"
    assert not any(":tokens:" in key for key in credentials.values)
    assert credentials.get("active_account") is None
    auth.cancel_login(attempt)


def test_failed_active_account_commit_rolls_back_new_metadata_and_tokens():
    class ActiveAccountWriteFailure(MemoryCredentials):
        def set(self, key, value):
            if key == "active_account":
                raise OSError("credential store unavailable")
            super().set(key, value)

    credentials = ActiveAccountWriteFailure()
    auth = ChatGPTPlanAuth(credentials=credentials, http_client=_token_client(_valid_tokens()))
    attempt = auth.begin_login()
    auth._validate_id_token = lambda *_: {"sub": "account", "nonce": attempt.nonce}

    with pytest.raises(SubscriptionAuthError) as error:
        auth.complete_login({"state": attempt.state, "code": "code", "client_id": "oaiapp-issued"})

    assert error.value.stage == "credential_storage"
    assert credentials.get("active_account") is None
    assert credentials.get("account:account:metadata") is None
    assert not any(":tokens:" in key for key in credentials.values)
    auth.cancel_login(attempt)
