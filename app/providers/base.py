"""Shared provider utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

import httpx


XAI_REASONING_MODELS = {
    "grok-4.6",
    "grok-4.5",
    "grok-4.3",
    "grok-4-0709",
    "grok-4-1-fast-reasoning",
}

XAI_EFFORT_GROK_43 = ["none", "low", "medium", "high"]
XAI_EFFORT_GROK_45_46 = ["low", "medium", "high", "xhigh"]
OPENROUTER_EFFORT = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

XAI_FALLBACK_MODELS = [
    "grok-4-1-fast-reasoning",
    "grok-3",
    "grok-3-mini",
    "grok-imagine-image-2.0",
    "grok-imagine-video-1.5",
]

OPENROUTER_FALLBACK_MODELS = [
    "x-ai/grok-4-fast",
    "anthropic/claude-sonnet-4",
    "openai/gpt-4o-mini",
]
OPENROUTER_FALLBACK_IMAGE_MODELS = [
    "openai/gpt-image-1",
]
OPENROUTER_FALLBACK_VIDEO_MODELS = [
    "google/veo-3.1",
]


@dataclass
class ModelInfo:
    id: str
    name: str
    supports_reasoning: bool = False
    reasoning_efforts: list[str] | None = None
    kind: str = "chat"


def classify_xai_model(model_id: str) -> str:
    lowered = model_id.lower()
    if "video" in lowered or "imagine-video" in lowered:
        return "video"
    if "imagine-image" in lowered or "grok-2-image" in lowered:
        return "image"
    if "image" in lowered and "vision" not in lowered:
        return "image"
    return "chat"


def model_supports_xai_reasoning(model_id: str) -> bool:
    if classify_xai_model(model_id) != "chat":
        return False
    lowered = model_id.lower()
    if "non-reasoning" in lowered:
        return False
    if any(token in lowered for token in ("grok-4.6", "grok-4.5", "grok-4-5", "grok-4.3")):
        return True
    if "grok-4" in lowered and "image" not in lowered and "vision" not in lowered:
        return True
    return model_id in XAI_REASONING_MODELS


def xai_efforts_for_model(model_id: str) -> list[str]:
    if not model_supports_xai_reasoning(model_id):
        return []
    lowered = model_id.lower()
    if "grok-4.3" in lowered:
        return XAI_EFFORT_GROK_43
    if any(token in lowered for token in ("grok-4.6", "grok-4.5", "grok-4-5")):
        return XAI_EFFORT_GROK_45_46
    return XAI_EFFORT_GROK_45_46


def parse_api_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = ""
        try:
            body = exc.response.text
        except Exception:
            body = ""
        parsed = _message_from_body(body)
        if parsed:
            return parsed
        return f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
    return str(exc)


def _message_from_body(body: str) -> str:
    if not body:
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:400]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("msg") or err)
    if isinstance(err, str):
        return err
    if data.get("message"):
        return str(data["message"])
    return body[:400]


def raise_if_http_error(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = response.text
    message = _message_from_body(body) or f"HTTP {response.status_code}"
    raise RuntimeError(message)


StreamCallback = Callable[[str], None]
