"""OpenRouter API client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterator

import httpx

from app.providers.base import OPENROUTER_EFFORT, OPENROUTER_FALLBACK_MODELS, ModelInfo, parse_api_error
from app.providers.messages import (
    chat_annotation_urls,
    chat_delta_text,
    format_sources,
    iter_sse_json,
    stream_error_message,
    to_chat_messages,
)

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://agent-chat.local",
            "X-Title": "Apichat",
        }

    def test_connection(self) -> tuple[bool, str]:
        try:
            models = self.list_models()
            if models:
                return True, f"Connected ({len(models)} models)"
            return False, "No models returned"
        except Exception as exc:
            return False, parse_api_error(exc)

    def _model_supports_reasoning(self, item: dict) -> bool:
        supported = item.get("supported_parameters") or []
        if "reasoning" in supported or "include_reasoning" in supported:
            return True
        architecture = item.get("architecture") or {}
        modality = str(architecture.get("modality", "")).lower()
        if "reasoning" in modality:
            return True
        name = str(item.get("id", "")).lower()
        reasoning_hints = ("o1", "o3", "o4", "reasoning", "think", "r1")
        return any(h in name for h in reasoning_hints)

    def list_models(self) -> list[ModelInfo]:
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{BASE_URL}/models", headers=self._headers())
                resp.raise_for_status()
                data = resp.json().get("data", [])
        except Exception:
            return [
                ModelInfo(id=m, name=m, supports_reasoning=False, reasoning_efforts=[], kind="chat")
                for m in OPENROUTER_FALLBACK_MODELS
            ]

        models: list[ModelInfo] = []
        for item in data:
            model_id = item.get("id", "")
            if not model_id:
                continue
            name = str(item.get("name") or model_id)
            architecture = item.get("architecture") or {}
            modality = str(architecture.get("modality", "")).lower()
            if modality and "text" not in modality and "image->text" not in modality:
                if "text" not in name.lower():
                    continue
            supports = self._model_supports_reasoning(item)
            models.append(
                ModelInfo(
                    id=model_id,
                    name=name,
                    supports_reasoning=supports,
                    reasoning_efforts=OPENROUTER_EFFORT if supports else [],
                    kind="chat",
                )
            )
        if not models:
            return [
                ModelInfo(id=m, name=m, supports_reasoning=False, reasoning_efforts=[], kind="chat")
                for m in OPENROUTER_FALLBACK_MODELS
            ]
        return models[:200]

    def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        effort: str | None = None,
        supports_reasoning: bool = False,
        *,
        web_search: bool = False,
        on_status: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        converted = to_chat_messages(messages, include_pdf_parts=True)
        body: dict[str, Any] = {"model": model, "messages": converted, "stream": True}
        if effort and supports_reasoning and effort != "none":
            body["reasoning"] = {"effort": effort}
        if web_search:
            body["tools"] = [{"type": "openrouter:web_search"}]
            if on_status:
                on_status("Searching the web…")
                yield ""
        if any(_has_pdf_part(msg) for msg in converted):
            body["plugins"] = [{"id": "file-parser"}]

        sources: list[str] = []
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{BASE_URL}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeError(stream_error_message(response)[:400] or f"HTTP {response.status_code}")
                for chunk in iter_sse_json(response):
                    sources.extend(chat_annotation_urls(chunk))
                    token = chat_delta_text(chunk)
                    if token:
                        yield token
                    elif _tool_call_name(chunk) and on_status:
                        on_status("Searching the web…")
        extra = format_sources(sources)
        if extra:
            yield extra


def _has_pdf_part(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(part.get("type") == "file" for part in content if isinstance(part, dict))


def _tool_call_name(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    tool_calls = delta.get("tool_calls") or []
    if not tool_calls:
        return ""
    fn = (tool_calls[0] or {}).get("function") or {}
    return str(fn.get("name") or "")
