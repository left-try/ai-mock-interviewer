"""Account-scoped ChatGPT plan model discovery and streamed Responses inference."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

MODELS_ENDPOINT = "https://api.openai.com/v1/models"
RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
ALLOWED_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


class InferenceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "inference_failed", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AvailableModel:
    slug: str
    display_name: str
    visibility: str
    reasoning_efforts: tuple[str, ...] | None = None


@dataclass(frozen=True)
class StructuredResult:
    value: dict[str, Any]
    ttft_ms: float
    duration_ms: float
    usage: dict[str, int]


class ChatGPTPlanClient:
    def __init__(self, *, auth, http_client: httpx.AsyncClient | None = None,
                 timing_recorder: Callable[..., None] | None = None, model_role: str = "fast"):
        self.auth = auth
        self.http = http_client or httpx.AsyncClient(timeout=90.0)
        self.timing_recorder = timing_recorder
        self.model_role = model_role
        self._models: dict[str, AvailableModel] = {}
        self._catalog_account: str | None = None

    async def list_models(self) -> list[AvailableModel]:
        try:
            response = await self.http.get(MODELS_ENDPOINT, headers=self._headers())
            response.raise_for_status()
            raw_models = response.json().get("models", [])
            account = self._account_id()
            parsed: dict[str, AvailableModel] = {}
            for item in raw_models:
                if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                    continue
                slug = item["slug"]
                efforts = item.get("supported_reasoning_efforts", item.get("supported_reasoning_levels"))
                if not isinstance(efforts, list):
                    efforts = None
                parsed[slug] = AvailableModel(
                    slug=slug,
                    display_name=str(item.get("display_name") or slug),
                    visibility=str(item.get("visibility") or "list"),
                    reasoning_efforts=tuple(str(value) for value in efforts) if efforts is not None else None,
                )
            self._models = {slug: model for slug, model in parsed.items() if model.visibility == "list"}
            self._catalog_account = account
            return list(self._models.values())
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError("Could not refresh models for the connected ChatGPT account", retryable=True) from exc

    def validate_selection(self, model: str, effort: str) -> AvailableModel:
        if self._catalog_account != self._account_id():
            self._models = {}
            raise ValueError("Refresh the connected account model list before selecting a model")
        available = self._models.get(model)
        if available is None:
            raise ValueError("Selected model is not available to the connected ChatGPT account")
        if effort not in ALLOWED_EFFORTS:
            raise ValueError("Unsupported reasoning effort")
        if available.reasoning_efforts is not None and effort not in available.reasoning_efforts:
            raise ValueError("Selected reasoning effort is not supported by this model")
        return available

    async def create_structured_response(self, *, model: str, effort: str, input: list[dict], schema: dict,
                                        max_output_tokens: int) -> StructuredResult:
        self.validate_selection(model, effort)
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        prompt_instructions = []
        request_input = []
        for item in input:
            if item.get("role") == "system":
                content = item.get("content", "")
                prompt_instructions.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            else:
                request_input.append(item)
        body = {
            "model": model,
            "input": request_input,
            "reasoning": {"effort": effort},
            "text": {"format": {"type": "json_schema", "name": "interview_result", "strict": True, "schema": schema}},
            "store": False,
            "stream": True,
        }
        if prompt_instructions:
            body["instructions"] = "\n\n".join(prompt_instructions)
        started = time.perf_counter()
        first_token = None
        chunks: list[str] = []
        completed = None
        try:
            async with self.http.stream("POST", RESPONSES_ENDPOINT, headers=self._headers(), json=body) as response:
                if response.status_code >= 400:
                    error_code = "inference_failed"
                    try:
                        data = json.loads(await response.aread())
                        error_code = str(data.get("error", {}).get("code") or error_code)
                    except Exception:
                        pass
                    raise InferenceError(
                        "ChatGPT plan inference request failed", code=error_code,
                        retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise InferenceError("ChatGPT returned an invalid stream event") from exc
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        if first_token is None:
                            first_token = time.perf_counter()
                        chunks.append(str(event.get("delta", "")))
                    elif event_type == "response.completed":
                        completed = event.get("response", {})
                    elif event_type in {"response.failed", "response.incomplete"}:
                        error = event.get("response", {}).get("error") or {}
                        code = str(error.get("code") or event_type)
                        retryable = code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}
                        message = "ChatGPT plan usage limit reached" if retryable else "ChatGPT did not complete the inference request"
                        raise InferenceError(message, code=code, retryable=retryable)
            if completed is None:
                raise InferenceError("ChatGPT stream ended before response.completed", retryable=True)
            output_text = "".join(chunks) or self._completed_output_text(completed)
            try:
                value = json.loads(output_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise InferenceError("Completed response did not contain valid structured JSON") from exc
            if not isinstance(value, dict):
                raise InferenceError("Completed response did not contain a JSON object")
            finished = time.perf_counter()
            ttft_ms = max(0.0, ((first_token or finished) - started) * 1000)
            duration_ms = max(0.0, (finished - started) * 1000)
            usage_raw = completed.get("usage") or {}
            usage = {key: usage_raw[key] for key in ("input_tokens", "output_tokens")
                     if isinstance(usage_raw.get(key), int) and not isinstance(usage_raw.get(key), bool)}
            self._record_timing("fast_model_ttft", ttft_ms, model, effort)
            self._record_timing("fast_model_completion", duration_ms, model, effort, usage)
            return StructuredResult(value=value, ttft_ms=ttft_ms, duration_ms=duration_ms, usage=usage)
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError("ChatGPT plan inference request failed", retryable=True) from exc

    def _record_timing(self, name: str, duration: float, model: str, effort: str, usage=None) -> None:
        if self.timing_recorder is None:
            return
        try:
            event_name = name if self.model_role == "fast" else "background_evaluation"
            if self.model_role != "fast" and name == "fast_model_ttft":
                return
            self.timing_recorder(event_name, duration_ms=duration, model_role=self.model_role,
                                 model=model, reasoning_effort=effort,
                                 input_tokens=(usage or {}).get("input_tokens"),
                                 output_tokens=(usage or {}).get("output_tokens"))
        except (OSError, TypeError, ValueError):
            pass

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth.get_access_token()}", "Content-Type": "application/json"}

    def _account_id(self) -> str | None:
        status = getattr(self.auth, "status", None)
        return status().account_id if status else None

    @staticmethod
    def _completed_output_text(response: dict) -> str:
        parts = []
        for item in response.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)
