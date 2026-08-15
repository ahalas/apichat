"""xAI API client for chat, image, and video."""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Callable
from typing import Any, Iterator
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
from app.providers.messages import (
    attachment_key,
    chat_delta_text,
    citation_urls,
    document_attachments,
    format_sources,
    iter_sse_json,
    needs_document_files,
    stream_error_message,
    to_chat_messages,
    to_responses_input,
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

    def upload_file(self, filename: str, data: bytes, mime: str | None = None) -> str:
        files = {"file": (filename, data, mime or "application/octet-stream")}
        form = {"purpose": "assistants", "expires_after": "86400"}
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{BASE_URL}/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=form,
            )
            raise_if_http_error(resp)
            file_id = resp.json().get("id")
            if not file_id:
                raise RuntimeError(f"Upload of {filename} did not return a file id")
            return str(file_id)

    def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        effort: str | None = None,
        *,
        web_search: bool = False,
        on_status: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        if web_search or needs_document_files(messages):
            yield from self._stream_responses(model, messages, effort=effort, web_search=web_search, on_status=on_status)
            return
        body: dict = {"model": model, "messages": to_chat_messages(messages), "stream": True}
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
                    raise RuntimeError(stream_error_message(response) or f"HTTP {response.status_code}")
                for chunk in iter_sse_json(response):
                    token = chat_delta_text(chunk)
                    if token:
                        yield token

    def _stream_responses(
        self,
        model: str,
        messages: list[dict[str, Any]],
        effort: str | None = None,
        *,
        web_search: bool = False,
        on_status: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        file_ids: dict[str, str] = {}
        for att in document_attachments(messages):
            key = attachment_key(att)
            if key in file_ids:
                continue
            if on_status:
                on_status(f"Uploading {att.get('filename') or 'file'}…")
            file_ids[key] = self.upload_file(
                str(att.get("filename") or "file"),
                base64.b64decode(str(att.get("data_base64") or ""), validate=False),
                str(att.get("mime") or "application/octet-stream"),
            )
            yield ""
        body: dict[str, Any] = {
            "model": model,
            "input": to_responses_input(messages, file_ids),
            "stream": True,
        }
        if web_search:
            body["tools"] = [{"type": "web_search"}]
        if effort and effort not in ("—", "-", "none") and model_supports_xai_reasoning(model):
            body["reasoning_effort"] = effort

        sources: list[str] = []
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{BASE_URL}/responses",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeError(stream_error_message(response) or f"HTTP {response.status_code}")
                for event in iter_sse_json(response):
                    etype = str(event.get("type") or "")
                    if etype in {"response.output_text.delta", "response.text.delta"} or etype.endswith("output_text.delta"):
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yield delta
                        elif isinstance(delta, dict):
                            if delta.get("text"):
                                yield str(delta["text"])
                            elif isinstance(delta.get("content"), str):
                                yield delta["content"]
                        elif isinstance(event.get("text"), str) and event["text"]:
                            yield event["text"]
                    elif "web_search" in etype and on_status:
                        on_status("Searching the web…")
                    elif "file_search" in etype or "attachment_search" in etype:
                        if on_status:
                            on_status("Reading attached files…")
                    elif etype in {"response.completed", "response.done"}:
                        payload = event.get("response") or event
                        sources.extend(citation_urls(payload.get("citations")))
                        sources.extend(citation_urls(payload.get("output")))
        extra = format_sources(sources)
        if extra:
            yield extra

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
