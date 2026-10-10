"""ChatGPT plan OAuth using the documented public-client loopback flow."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import http.server
import html
import json
import queue
import secrets
import time
import threading
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import keyring

AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_ENDPOINT = "https://auth.openai.com/.well-known/jwks.json"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
REQUIRED_SCOPES = frozenset({"openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"})
SERVICE_NAME = "ai-mock-interviewer.chatgpt-plan"


class SubscriptionAuthError(RuntimeError):
    """Safe-to-display authorization failure without response bodies or tokens."""

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class LoginAttempt:
    authorization_url: str
    redirect_uri: str
    state: str
    nonce: str
    code_verifier: str
    client_id: str
    expected_account_id: str | None = None


@dataclass(frozen=True)
class AccountStatus:
    connected: bool
    account_id: str | None = None
    email: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: float | None = None


@dataclass
class _CallbackDelivery:
    query: dict[str, str]
    completed: threading.Event
    succeeded: bool = False


def _callback_page(succeeded: bool) -> str:
    """Render the local OAuth receipt after the app has finished validating the callback."""
    if succeeded:
        eyebrow = "Connection complete"
        title = "ChatGPT plan is connected"
        message = "Your account is ready for interview model setup. Return to Codex to continue."
        symbol = "✓"
        status = "success"
    else:
        eyebrow = "Connection not completed"
        title = "Your ChatGPT account could not be connected"
        message = "Return to Codex to see the next step. You can close this page."
        symbol = "!"
        status = "failure"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{html.escape(title)} · AI Mock Interviewer</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f3f5f8; color: #142033; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 24px; background: radial-gradient(ellipse at 50% 0%, #e3ebff 0, transparent 55%), #f3f5f8; }}
    main {{ width: min(100%, 520px); padding: clamp(28px, 7vw, 48px); border: 1px solid #e3e8f0; border-radius: 24px; background: rgba(255,255,255,.96); box-shadow: 0 24px 80px rgba(34, 51, 84, .13); text-align: center; }}
    .mark {{ display: grid; place-items: center; width: 64px; height: 64px; margin: 0 auto 24px; border-radius: 20px; background: #edf2ff; color: #344ec5; font-size: 30px; font-weight: 700; }}
    .mark.success {{ background: #e7f7ef; color: #137548; }}
    .mark.failure {{ background: #fff1ed; color: #a33b22; }}
    .eyebrow {{ margin: 0 0 10px; color: #596a83; font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }}
    h1 {{ margin: 0; color: #142033; font-size: clamp(25px, 6vw, 34px); line-height: 1.15; letter-spacing: -.035em; }}
    .message {{ margin: 18px auto 0; max-width: 38ch; color: #506079; font-size: 16px; line-height: 1.65; }}
    .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #e8ecf2; color: #738198; font-size: 13px; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ background: #101522; color: #eaf0fa; }} body {{ background: radial-gradient(ellipse at 50% 0%, #1b2b50 0, transparent 55%), #101522; }}
      main {{ border-color: #303b50; background: rgba(24, 32, 48, .97); box-shadow: 0 24px 80px rgba(0, 0, 0, .35); }}
      h1 {{ color: #f0f4fc; }} .eyebrow, .message, .footer {{ color: #b5c0d2; }} .footer {{ border-color: #354057; }}
      .mark {{ background: #283455; color: #b8c7ff; }} .mark.success {{ background: #173b30; color: #8ee0b2; }} .mark.failure {{ background: #472b29; color: #ffae98; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation-duration: .01ms !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
  <main aria-labelledby="receipt-title">
    <div class="mark {status}" aria-hidden="true">{symbol}</div>
    <p class="eyebrow">{eyebrow}</p>
    <h1 id="receipt-title">{title}</h1>
    <p class="message">{message}</p>
    <p class="footer">AI Mock Interviewer · Secure account connection</p>
  </main>
</body>
</html>"""


class _KeyringCredentials:
    def get(self, key: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(SERVICE_NAME, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except keyring.errors.PasswordDeleteError:
            pass


class ChatGPTPlanAuth:
    def __init__(self, *, credentials=None, http_client: httpx.Client | None = None, host_id: str | None = None):
        self.credentials = credentials or _KeyringCredentials()
        self.http = http_client or httpx.Client(timeout=20.0, follow_redirects=False)
        self.host_id = host_id or self._load_host_id()
        self._attempts: dict[str, LoginAttempt] = {}
        self._callback_servers = {}

    def begin_login(self, *, expected_account_id: str | None = None, client_id: str | None = None) -> LoginAttempt:
        callback_queue: queue.Queue[_CallbackDelivery] = queue.Queue(maxsize=1)

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                values = {key: items[0] for key, items in parse_qs(parsed.query).items() if items}
                delivery = _CallbackDelivery(values, threading.Event())
                try:
                    callback_queue.put_nowait(delivery)
                except queue.Full:
                    self.send_error(409)
                    return
                # Do not show a success receipt until code exchange, identity validation,
                # and secure credential persistence have all completed in wait_for_callback.
                delivery.completed.wait(timeout=90)
                body = _callback_page(delivery.succeeded).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_port
        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        client_id = client_id or "dynamic_agent_client"
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "ext_agent_host_id": self.host_id,
        }
        if client_id == "dynamic_agent_client":
            params["agent_name_hint"] = "AI Mock Interviewer"
        elif expected_account_id:
            try:
                record = self._metadata(expected_account_id)
                version = record.get("token_version")
                id_token = self.credentials.get(f"account:{expected_account_id}:tokens:{version}:id")
                if id_token:
                    params["id_token_hint"] = id_token
                if record.get("email"):
                    params["login_hint"] = str(record["email"])
            except SubscriptionAuthError:
                pass
        query = urlencode(params)
        attempt = LoginAttempt(
            authorization_url=f"{AUTHORIZATION_ENDPOINT}?{query}", redirect_uri=redirect_uri,
            state=state, nonce=nonce, code_verifier=verifier, client_id=client_id,
            expected_account_id=expected_account_id,
        )
        self._attempts[state] = attempt
        self._callback_servers[state] = (server, callback_queue)
        return attempt

    async def wait_for_callback(self, attempt: LoginAttempt, *, timeout: float = 300) -> AccountStatus:
        entry = self._callback_servers.get(attempt.state)
        if entry is None:
            raise SubscriptionAuthError("The sign-in callback listener is unavailable")
        server, callback_queue = entry
        try:
            delivery = await asyncio.to_thread(callback_queue.get, True, timeout)
            try:
                result = await asyncio.to_thread(self.complete_login, delivery.query)
                delivery.succeeded = result.connected
                return result
            finally:
                delivery.completed.set()
        except queue.Empty as exc:
            self._attempts.pop(attempt.state, None)
            raise SubscriptionAuthError("ChatGPT sign-in timed out; start a new connection attempt") from exc
        finally:
            server.shutdown()
            server.server_close()
            self._callback_servers.pop(attempt.state, None)

    def cancel_login(self, attempt: LoginAttempt) -> None:
        self._attempts.pop(attempt.state, None)
        entry = self._callback_servers.pop(attempt.state, None)
        if entry:
            server, _ = entry
            server.shutdown()
            server.server_close()

    def complete_login(self, callback_query: Mapping[str, str]) -> AccountStatus:
        state = callback_query.get("state", "")
        attempt = self._attempts.pop(state, None)
        if attempt is None or not secrets.compare_digest(state, attempt.state):
            raise SubscriptionAuthError("The sign-in callback state did not match this authorization attempt")
        if callback_query.get("error"):
            raise SubscriptionAuthError("ChatGPT sign-in was denied or cancelled")
        code = callback_query.get("code")
        if not code:
            raise SubscriptionAuthError("The sign-in callback did not include an authorization code")
        issued_client_id = callback_query.get("client_id") or attempt.client_id
        if attempt.client_id != "dynamic_agent_client" and issued_client_id != attempt.client_id:
            raise SubscriptionAuthError("The sign-in callback returned a different client registration")
        if not issued_client_id or issued_client_id == "dynamic_agent_client":
            raise SubscriptionAuthError("ChatGPT did not complete the app registration")
        stage = "token_exchange"
        try:
            response = self.http.post(TOKEN_ENDPOINT, data={
                "grant_type": "authorization_code", "client_id": issued_client_id,
                "code": code, "code_verifier": attempt.code_verifier,
                "redirect_uri": attempt.redirect_uri, "resource": RESOURCE,
            })
            response.raise_for_status()
            tokens = response.json()
            stage = "grant_validation"
            scopes = frozenset(str(tokens.get("scope", "")).split())
            if not REQUIRED_SCOPES.issubset(scopes):
                raise SubscriptionAuthError("The ChatGPT account did not grant all required plan-usage scopes")
            claims = self._validate_id_token(tokens.get("id_token", ""), issued_client_id, attempt.nonce)
            account_id = claims.get("sub")
            if not isinstance(account_id, str) or not account_id:
                raise SubscriptionAuthError("The sign-in identity was missing")
            if attempt.expected_account_id and account_id != attempt.expected_account_id:
                raise SubscriptionAuthError("The returned ChatGPT account did not match the selected account")
            record = {
                "account_id": account_id, "email": claims.get("email"), "client_id": issued_client_id,
                "scopes": sorted(scopes), "expires_at": time.time() + int(tokens.get("expires_in", 0)),
                "token_type": tokens.get("token_type", "Bearer"),
            }
            stage = "credential_storage"
            old_metadata = self.credentials.get(f"account:{account_id}:metadata")
            old_active_account = self.credentials.get("active_account")
            version = self._store_tokens(account_id, tokens)
            record["token_version"] = version
            try:
                self.credentials.set(f"account:{account_id}:metadata", json.dumps(record, separators=(",", ":")))
                self.credentials.set("active_account", account_id)
            except Exception:
                self._delete_token_version(account_id, version)
                if old_metadata:
                    self.credentials.set(f"account:{account_id}:metadata", old_metadata)
                else:
                    self.credentials.delete(f"account:{account_id}:metadata")
                if old_active_account:
                    self.credentials.set("active_account", old_active_account)
                else:
                    self.credentials.delete("active_account")
                raise
            if old_metadata:
                self._delete_token_version(account_id, json.loads(old_metadata).get("token_version"))
            return AccountStatus(True, account_id, claims.get("email"), tuple(sorted(scopes)), record["expires_at"])
        except SubscriptionAuthError as exc:
            if exc.stage is None:
                exc.stage = stage
            raise
        except Exception as exc:
            raise SubscriptionAuthError(
                "ChatGPT sign-in could not be completed or securely stored", stage=stage,
            ) from exc

    def status(self) -> AccountStatus:
        account_id = self.credentials.get("active_account")
        if not account_id:
            return AccountStatus(False)
        record = self._metadata(account_id)
        return AccountStatus(True, account_id, record.get("email"), tuple(record.get("scopes", ())), record.get("expires_at"))

    def get_access_token(self) -> str:
        account_id = self.credentials.get("active_account")
        if not account_id:
            raise SubscriptionAuthError("Connect a ChatGPT account before starting an interview")
        record = self._metadata(account_id)
        version = record.get("token_version")
        token = self.credentials.get(f"account:{account_id}:tokens:{version}:access") if version else None
        if token and float(record.get("expires_at", 0)) > time.time() + 30:
            return token
        refresh = self.credentials.get(f"account:{account_id}:tokens:{version}:refresh") if version else None
        if not refresh:
            raise SubscriptionAuthError("ChatGPT authorization expired; reconnect the account")
        try:
            response = self.http.post(TOKEN_ENDPOINT, data={
                "grant_type": "refresh_token", "client_id": record["client_id"], "refresh_token": refresh,
                "resource": RESOURCE,
            })
            response.raise_for_status()
            tokens = response.json()
            scopes = frozenset(str(tokens.get("scope", " ".join(record.get("scopes", [])))).split())
            if not REQUIRED_SCOPES.issubset(scopes):
                raise SubscriptionAuthError("The refreshed ChatGPT grant no longer includes plan usage")
            if not tokens.get("refresh_token"):
                tokens["refresh_token"] = refresh
            replacement = dict(record, scopes=sorted(scopes), expires_at=time.time() + int(tokens.get("expires_in", 0)))
            next_version = self._store_tokens(account_id, tokens)
            replacement["token_version"] = next_version
            try:
                self.credentials.set(f"account:{account_id}:metadata", json.dumps(replacement, separators=(",", ":")))
            except Exception:
                self._delete_token_version(account_id, next_version)
                raise
            self._delete_token_version(account_id, version)
            return str(tokens["access_token"])
        except SubscriptionAuthError:
            raise
        except Exception as exc:
            raise SubscriptionAuthError("ChatGPT authorization refresh failed; reconnect the account") from exc

    def disconnect(self, account_id: str | None = None) -> None:
        account_id = account_id or self.credentials.get("active_account")
        if not account_id:
            return
        raw = self.credentials.get(f"account:{account_id}:metadata")
        if raw:
            self._delete_token_version(account_id, json.loads(raw).get("token_version"))
        self.credentials.delete(f"account:{account_id}:metadata")
        if self.credentials.get("active_account") == account_id:
            self.credentials.delete("active_account")

    def _store_tokens(self, account_id: str, tokens: Mapping[str, object]) -> str:
        # Stage secrets under a fresh version; the metadata pointer is committed separately.
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise SubscriptionAuthError("The ChatGPT token response was incomplete")
        version = secrets.token_urlsafe(12)
        prefix = f"account:{account_id}:tokens:{version}:"
        try:
            self.credentials.set(prefix + "access", access)
            self.credentials.set(prefix + "refresh", refresh)
            id_token = tokens.get("id_token")
            if isinstance(id_token, str):
                self.credentials.set(prefix + "id", id_token)
        except Exception:
            self._delete_token_version(account_id, version)
            raise
        return version

    def _delete_token_version(self, account_id: str, version: str | None) -> None:
        if not version:
            return
        prefix = f"account:{account_id}:tokens:{version}:"
        for suffix in ("access", "refresh", "id"):
            self.credentials.delete(prefix + suffix)

    def _metadata(self, account_id: str) -> dict:
        raw = self.credentials.get(f"account:{account_id}:metadata")
        if not raw:
            raise SubscriptionAuthError("The saved ChatGPT account record is unavailable")
        return json.loads(raw)

    def _load_host_id(self) -> str:
        host_id = self.credentials.get("host_id")
        if host_id:
            return host_id
        host_id = f"urn:uuid:{uuid.uuid4()}"
        self.credentials.set("host_id", host_id)
        return host_id

    def _validate_id_token(self, token: str, client_id: str, nonce: str) -> dict:
        if not token:
            raise SubscriptionAuthError("The sign-in response did not include an identity token")
        response = self.http.get(JWKS_ENDPOINT)
        response.raise_for_status()
        jwks = jwt.PyJWKSet.from_dict(response.json())
        header = jwt.get_unverified_header(token)
        key = next((item.key for item in jwks.keys if item.key_id == header.get("kid")), None)
        if key is None:
            raise SubscriptionAuthError("The sign-in identity could not be verified")
        claims = jwt.decode(token, key=key, algorithms=[header.get("alg", "RS256")], audience=client_id, issuer=ISSUER)
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SubscriptionAuthError("The sign-in identity nonce did not match this authorization attempt")
        return claims
