"""AI provider adapters (server-side only).

The assistant speaks to a provider through a tiny, dependency-free interface so
the model/vendor is configuration, not application logic:

* ``openai``    any OpenAI-compatible ``/chat/completions`` endpoint
                (OpenAI, Azure-compatible gateways, vLLM, Ollama, ...)
* ``anthropic`` Anthropic Messages API with tool use

API keys are read from the environment on the server and are never sent to, or
returned by, the frontend.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from ...config import settings


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class ProviderTimeout(ProviderError):
    def __init__(self):
        super().__init__("ai_timeout", "The AI provider took too long to respond.")


class ProviderUnavailable(ProviderError):
    def __init__(self, message: str = "The AI assistant is not configured on this server."):
        super().__init__("ai_not_configured", message)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""


@dataclass
class ModelTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None


def _parse_arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class AIProvider:
    name = "base"

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        raise NotImplementedError


class UnavailableProvider(AIProvider):
    name = "unavailable"

    def __init__(self, message: str = "The AI assistant is not configured on this server."):
        self.message = message

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        raise ProviderUnavailable(self.message)


class _HTTPProvider(AIProvider):
    def __init__(self, *, api_key: str, model: str, base_url: str,
                 timeout: float, max_tokens: int):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderTimeout() from exc
        except urllib.error.HTTPError as exc:
            status = exc.code
            raise ProviderError(
                "ai_rate_limited" if status == 429 else "ai_provider_error",
                "The AI provider is busy or rejected the request." if status == 429
                else f"The AI provider returned an error (HTTP {status}).",
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
                raise ProviderTimeout() from exc
            raise ProviderError("ai_unreachable", "The AI provider could not be reached.") from exc
        except Exception as exc:  # noqa: BLE001 - never leak transport details
            raise ProviderError("ai_unreachable", "The AI provider could not be reached.") from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("ai_invalid_response", "The AI provider returned an unreadable response.") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("ai_invalid_response", "The AI provider returned an unreadable response.")
        return parsed


class OpenAICompatibleProvider(_HTTPProvider):
    name = "openai"

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                }}
                for tool in tools
            ]
            payload["tool_choice"] = "auto"
        data = self._post(
            self._endpoint(),
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            choice = (data.get("choices") or [])[0]
            message = choice.get("message") or {}
        except (IndexError, AttributeError, TypeError) as exc:
            raise ProviderError("ai_invalid_response", "The AI provider returned an unexpected response.") from exc
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append(ToolCall(
                id=str(item.get("id") or f"call_{len(calls) + 1}"),
                name=str(function.get("name") or ""),
                arguments=_parse_arguments(function.get("arguments")),
                raw_arguments=str(function.get("arguments") or ""),
            ))
        return ModelTurn(
            content=str(message.get("content") or ""),
            tool_calls=[call for call in calls if call.name],
            finish_reason=choice.get("finish_reason"),
        )


class AnthropicProvider(_HTTPProvider):
    name = "anthropic"

    def _endpoint(self) -> str:
        if self.base_url.endswith("/messages"):
            return self.base_url
        return self.base_url + "/messages"

    @staticmethod
    def _convert(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts = []
        converted: list[dict] = []
        pending_tool_results: list[dict] = []

        def flush_tools():
            nonlocal pending_tool_results
            if pending_tool_results:
                converted.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content") or ""))
                continue
            if role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(message.get("content") or ""),
                })
                continue
            flush_tools()
            if role == "assistant":
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": str(message["content"])})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    blocks.append({
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "input": _parse_arguments(function.get("arguments")),
                    })
                converted.append({"role": "assistant", "content": blocks or ""})
            else:
                converted.append({"role": "user", "content": str(message.get("content") or "")})
        flush_tools()
        return "\n\n".join(system_parts), converted

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        system, converted = self._convert(messages)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": converted,
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = [
                {"name": tool["name"], "description": tool["description"],
                 "input_schema": tool["parameters"]}
                for tool in tools
            ]
        data = self._post(
            self._endpoint(),
            payload,
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        text_parts, calls = [], []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(
                    id=str(block.get("id") or f"toolu_{len(calls) + 1}"),
                    name=str(block.get("name") or ""),
                    arguments=_parse_arguments(block.get("input")),
                    raw_arguments=json.dumps(block.get("input") or {}),
                ))
        return ModelTurn(
            content="".join(text_parts),
            tool_calls=[call for call in calls if call.name],
            finish_reason=data.get("stop_reason"),
        )


def get_provider() -> AIProvider:
    provider = (settings.ai_provider or "openai").strip().lower()
    if provider in {"none", "disabled", "off"}:
        return UnavailableProvider("The AI assistant is disabled on this server.")
    if not settings.ai_api_key:
        return UnavailableProvider(
            "The AI assistant is not configured yet. Ask your administrator to set AI_API_KEY.")
    base_url = settings.ai_base_url or "https://api.openai.com/v1"
    if provider in {"openai_compatible", "compatible"}:
        provider = "openai"
    if provider == "anthropic":
        if not base_url or base_url.rstrip("/") == "https://api.openai.com/v1":
            base_url = "https://api.anthropic.com/v1"
        return AnthropicProvider(
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=base_url,
            timeout=settings.ai_timeout_seconds,
            max_tokens=settings.ai_max_output_tokens,
        )
    if provider != "openai":
        return UnavailableProvider(f"AI provider '{provider}' is not supported.")
    return OpenAICompatibleProvider(
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        base_url=base_url,
        timeout=settings.ai_timeout_seconds,
        max_tokens=settings.ai_max_output_tokens,
    )
