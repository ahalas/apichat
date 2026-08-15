"""xAI API client for chat, image, and video."""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Iterator
from urllib.parse import urlparse

import httpx

from app.files.detector import Attachment
from app.providers.base import (
    ModelInfo,
    XAI_FALLBACK_MODELS,
    classify_xai_model,
    model_supports_xai_reasoning,
    parse_api_error,
    raise_if_http_error,
    xai_efforts_for_model,
)

BASE_URL = "https://api.x.ai/v1"


def _model_info(model_id: str) -> ModelInfo:
    kind = classify_xai_model(model_id)
    supports = model_supports_xai_reasoning(model_id)
    return ModelInfo(
        id=model_id,
        name=model_id,
        supports_reasoning=supports,
        reasoning_efforts=xai_efforts_for_model(model_id) if supports else [],
        kind=kind,
    )


class XAIClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def test_connection(self) -> tuple[bool, str]:
        try:
            models = self.list_models()
            if models:
                return True, f"Connected ({len(models)} models)"
            return False, "No models returned"
        except Exception as exc:
            return False, parse_api_error(exc)

    def list_models(self) -> list[ModelInfo]:
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{BASE_URL}/models", headers=self._headers())
                raise_if_http_error(resp)
                data = resp.json().get("data", [])
        except Exception:
            return [_model_info(m) for m in XAI_FALLBACK_MODELS]

        models: list[ModelInfo] = []
        for item in data:
            model_id = item.get("id", "")
            if not model_id:
                continue
            models.append(_model_info(model_id))
        return models or [_model_info(m) for m in XAI_FALLBACK_MODELS]

    def stream_chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        effort: str | None = None,
    ) -> Iterator[str]:
        body: dict = {"model": model, "messages": messages, "stream": True}
        if effort and effort not in ("—", "-", "none") and model_supports_xai_reasoning(model):
            body["reasoning_effort"] = effort

        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{BASE_URL}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    body_text = response.read().decode("utf-8", errors="replace")
                    message = body_text
                    try:
                        parsed = json.loads(body_text)
                        err = parsed.get("error")
                        if isinstance(err, dict):
                            message = str(err.get("message") or err)
                        elif isinstance(err, str):
                            message = err
                    except json.JSONDecodeError:
                        pass
                    raise RuntimeError(message or f"HTTP {response.status_code}")
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

    def generate_image(self, prompt: str, model: str) -> Attachment:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{BASE_URL}/images/generations",
                headers=self._headers(),
                json={"model": model, "prompt": prompt, "n": 1},
            )
            raise_if_http_error(resp)
            items = resp.json().get("data") or []
            if not items:
                raise RuntimeError("No image returned")
            item = items[0]
            b64 = item.get("b64_json")
            mime = "image/png"
            filename = "image.png"
            if b64:
                return Attachment(filename=filename, mime=mime, data_base64=b64)
            url = item.get("url")
            if not url:
                raise RuntimeError("Image response had no url or base64 data")
            img = client.get(url, timeout=120.0)
            raise_if_http_error(img)
            ext = _ext_from_url(url, ".png")
            mime = f"image/{ext.lstrip('.')}" if ext != ".jpg" else "image/jpeg"
            if ext == ".jpeg":
                mime = "image/jpeg"
            return Attachment(
                filename=f"image{ext}",
                mime=mime,
                data_base64=base64.b64encode(img.content).decode("ascii"),
            )

    def generate_video(
        self,
        prompt: str,
        model: str,
        duration: int = 8,
        stop_event: threading.Event | None = None,
    ) -> Attachment:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{BASE_URL}/videos/generations",
                headers=self._headers(),
                json={
                    "model": model,
                    "prompt": prompt,
                    "duration": duration,
                    "aspect_ratio": "16:9",
                    "resolution": "720p",
                },
            )
            raise_if_http_error(resp)
            request_id = resp.json().get("request_id")
            if not request_id:
                raise RuntimeError("Video request did not return a request_id")

        with httpx.Client(timeout=60.0) as client:
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("Generation cancelled")
                result = client.get(
                    f"{BASE_URL}/videos/{request_id}",
                    headers=self._headers(),
                )
                raise_if_http_error(result)
                data = result.json()
                status = str(data.get("status", "")).lower()
                if status in {"done", "completed", "succeeded", "success"}:
                    video = data.get("video") or {}
                    url = video.get("url") or data.get("url")
                    if not url:
                        raise RuntimeError("Video completed but no URL was returned")
                    clip = client.get(url, timeout=180.0)
                    raise_if_http_error(clip)
                    return Attachment(
                        filename="video.mp4",
                        mime="video/mp4",
                        data_base64=base64.b64encode(clip.content).decode("ascii"),
                    )
                if status in {"failed", "expired", "error"}:
                    err = data.get("error") or data.get("message") or status
                    raise RuntimeError(f"Video generation {status}: {err}")
                time.sleep(5)


def _ext_from_url(url: str, default: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if path.endswith(ext):
            return ext
    return default
