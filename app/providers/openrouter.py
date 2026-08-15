"""OpenRouter API client."""

from __future__ import annotations

import json
from typing import Iterator

import httpx

from app.providers.base import OPENROUTER_EFFORT, OPENROUTER_FALLBACK_MODELS, ModelInfo, parse_api_error

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
        messages: list[dict[str, str]],
        effort: str | None = None,
        supports_reasoning: bool = False,
    ) -> Iterator[str]:
        body: dict = {"model": model, "messages": messages, "stream": True}
        if effort and supports_reasoning and effort != "none":
            body["reasoning"] = {"effort": effort}

        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{BASE_URL}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    body_text = response.read().decode("utf-8", errors="replace")
                    raise RuntimeError(body_text[:400] or f"HTTP {response.status_code}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
