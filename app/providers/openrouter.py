"""OpenRouter API client for chat, image, and video."""

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
    OPENROUTER_EFFORT,
    OPENROUTER_FALLBACK_IMAGE_MODELS,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_FALLBACK_VIDEO_MODELS,
    ModelInfo,
    parse_api_error,
    raise_if_http_error,
)
from app.providers.messages import (
    chat_annotation_urls,
    chat_delta_text,
    format_sources,
    iter_sse_json,
    stream_error_message,
    to_chat_messages,
)

BASE_URL = "https://openrouter.ai/api/v1"
_MODELS_CACHE: dict[str, tuple[float, list[ModelInfo]]] = {}
_MODELS_CACHE_TTL = 60.0


class OpenRouterClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _headers(self, json_body: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/ahalas/apichat",
            "X-Title": "Apichat",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _get(self, path: str, timeout: float = 30.0) -> Any:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{BASE_URL}{path}", headers=self._headers(json_body=False))
            raise_if_http_error(resp)
            return resp.json()

    def _post(self, path: str, body: dict[str, Any], timeout: float = 120.0) -> Any:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{BASE_URL}{path}", headers=self._headers(), json=body)
            raise_if_http_error(resp)
            return resp.json()

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._get("/key")
            models = self._load_all_models()
            _MODELS_CACHE[self.api_key] = (time.time(), models)
            if models:
                return True, f"Connected ({len(models)} models)"
            return False, "No models returned"
        except Exception as exc:
            return False, parse_api_error(exc)

    def list_models(self) -> list[ModelInfo]:
        now = time.time()
        cached = _MODELS_CACHE.get(self.api_key)
        if cached and cached[0] + _MODELS_CACHE_TTL > now:
            return cached[1]
        try:
            models = self._load_all_models()
        except Exception:
            return _fallback_models()
        _MODELS_CACHE[self.api_key] = (now, models)
        return models

    def _load_all_models(self) -> list[ModelInfo]:
        errors: list[Exception] = []
        chat = _try_load(lambda: self._load_chat_models(), errors)
        images = _try_load(lambda: self._load_image_models(), errors)
        videos = _try_load(lambda: self._load_video_models(), errors)
        if not chat and not images and not videos:
            if errors:
                raise errors[0]
            return _fallback_models()
        image_ids = {m.id for m in images}
        video_ids = {m.id for m in videos}
        chat = [m for m in chat if m.id not in image_ids and m.id not in video_ids]
        if not chat:
            chat = _fallback_chat_models()
        return chat + images + videos

    def _load_chat_models(self) -> list[ModelInfo]:
        data = self._get("/models").get("data") or []
        models: list[ModelInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id or _output_kind(item) != "chat":
                continue
            models.append(_chat_model_info(item))
        return models

    def _load_image_models(self) -> list[ModelInfo]:
        try:
            data = self._get("/images/models").get("data") or []
        except Exception:
            data = self._get("/models?output_modalities=image").get("data") or []
        models: list[ModelInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    name=str(item.get("name") or model_id),
                    supports_reasoning=False,
                    reasoning_efforts=[],
                    kind="image",
                )
            )
        return models

    def _load_video_models(self) -> list[ModelInfo]:
        try:
            data = self._get("/videos/models").get("data") or []
        except Exception:
            data = self._get("/models?output_modalities=video").get("data") or []
        models: list[ModelInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    name=str(item.get("name") or model_id),
                    supports_reasoning=False,
                    reasoning_efforts=[],
                    kind="video",
                )
            )
        return models

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
        if supports_reasoning and effort and effort not in ("—", "-"):
            body["reasoning"] = {"effort": effort, "exclude": True}
        if web_search:
            body["tools"] = [{"type": "openrouter:web_search"}]
            if on_status:
                on_status("Searching the web…")
                yield ""
        if any(_has_pdf_part(msg) for msg in converted):
            body["plugins"] = [{"id": "file-parser"}]
            if on_status:
                on_status("Reading attached files…")
                yield ""

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
                    elif _looks_like_web_search(chunk) and on_status:
                        on_status("Searching the web…")
        extra = format_sources(sources)
        if extra:
            yield extra

    def generate_image(self, prompt: str, model: str) -> Attachment:
        data = self._post("/images", {"model": model, "prompt": prompt, "n": 1}, timeout=180.0)
        items = data.get("data") or []
        if not items:
            raise RuntimeError("No image returned")
        item = items[0] if isinstance(items[0], dict) else {}
        mime = str(item.get("media_type") or "image/png")
        b64 = item.get("b64_json")
        if b64:
            return Attachment(filename=_filename_for_mime(mime, "image"), mime=mime, data_base64=str(b64))
        url = item.get("url")
        if not url:
            raise RuntimeError("Image response had no url or base64 data")
        with httpx.Client(timeout=120.0) as client:
            img = client.get(str(url), headers=self._headers(json_body=False))
            raise_if_http_error(img)
            payload = img.content
        ext = _ext_from_url(str(url), ".png")
        mime = f"image/{ext.lstrip('.')}" if ext != ".jpg" else "image/jpeg"
        if ext == ".jpeg":
            mime = "image/jpeg"
        return Attachment(
            filename=f"image{ext}",
            mime=mime,
            data_base64=base64.b64encode(payload).decode("ascii"),
        )

    def generate_video(
        self,
        prompt: str,
        model: str,
        duration: int = 8,
        stop_event: threading.Event | None = None,
    ) -> Attachment:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": "16:9",
        }
        data = self._post("/videos", payload, timeout=60.0)
        job_id = str(data.get("id") or "")
        polling_url = str(data.get("polling_url") or "")
        if not job_id and not polling_url:
            raise RuntimeError("Video request did not return a job id")
        poll_path = polling_url if polling_url.startswith("http") else f"{BASE_URL}/videos/{job_id}"

        with httpx.Client(timeout=60.0) as client:
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("Generation cancelled")
                if polling_url.startswith("http"):
                    result = client.get(poll_path, headers=self._headers(json_body=False))
                else:
                    result = client.get(f"{BASE_URL}/videos/{job_id}", headers=self._headers(json_body=False))
                raise_if_http_error(result)
                status_data = result.json()
                status = str(status_data.get("status") or "").lower()
                if status in {"completed", "complete", "succeeded", "success", "done"}:
                    return self._download_video(client, status_data, job_id)
                if status in {"failed", "expired", "error", "cancelled", "canceled"}:
                    err = status_data.get("error") or status_data.get("message") or status
                    raise RuntimeError(f"Video generation {status}: {err}")
                time.sleep(5)

    def _download_video(self, client: httpx.Client, status_data: dict[str, Any], job_id: str) -> Attachment:
        urls = status_data.get("unsigned_urls") or []
        url = urls[0] if urls else ""
        if url:
            clip = client.get(str(url), timeout=180.0)
            if clip.status_code < 400:
                return Attachment(
                    filename="video.mp4",
                    mime="video/mp4",
                    data_base64=base64.b64encode(clip.content).decode("ascii"),
                )
        if not job_id:
            raise RuntimeError("Video completed but no URL was returned")
        clip = client.get(
            f"{BASE_URL}/videos/{job_id}/content",
            params={"index": 0},
            headers=self._headers(json_body=False),
            timeout=180.0,
        )
        raise_if_http_error(clip)
        return Attachment(
            filename="video.mp4",
            mime="video/mp4",
            data_base64=base64.b64encode(clip.content).decode("ascii"),
        )


def _try_load(loader: Callable[[], list[ModelInfo]], errors: list[Exception]) -> list[ModelInfo]:
    try:
        return loader()
    except Exception as exc:
        errors.append(exc)
        return []


def _output_kind(item: dict[str, Any]) -> str:
    architecture = item.get("architecture") or {}
    outputs = [str(x).lower() for x in (architecture.get("output_modalities") or [])]
    modality = str(architecture.get("modality") or "").lower()
    if "video" in outputs or "->video" in modality:
        return "video"
    if outputs:
        if "text" in outputs:
            return "chat"
        if "image" in outputs:
            return "image"
        return "chat"
    if "->video" in modality:
        return "video"
    if "->image" in modality and "text" not in modality.split("->")[-1]:
        return "image"
    return "chat"


def _chat_model_info(item: dict[str, Any]) -> ModelInfo:
    model_id = str(item.get("id") or "")
    name = str(item.get("name") or model_id)
    supports, efforts = _reasoning_from_item(item)
    return ModelInfo(
        id=model_id,
        name=name,
        supports_reasoning=supports,
        reasoning_efforts=efforts,
        kind="chat",
    )


def _reasoning_from_item(item: dict[str, Any]) -> tuple[bool, list[str]]:
    reasoning = item.get("reasoning")
    if isinstance(reasoning, dict) and reasoning:
        efforts = [str(e) for e in (reasoning.get("supported_efforts") or []) if e]
        mandatory = bool(reasoning.get("mandatory"))
        if efforts:
            if not mandatory and "none" not in [e.lower() for e in efforts]:
                efforts = ["none", *efforts]
            return True, efforts
        if reasoning.get("default_enabled") is not None or reasoning.get("supports_max_tokens"):
            return True, list(OPENROUTER_EFFORT)
    supported = item.get("supported_parameters") or []
    if "reasoning" in supported or "include_reasoning" in supported:
        return True, list(OPENROUTER_EFFORT)
    architecture = item.get("architecture") or {}
    modality = str(architecture.get("modality") or "").lower()
    name = str(item.get("id") or "").lower()
    if "reasoning" in modality or any(h in name for h in ("o1", "o3", "o4", "reasoning", "think", "r1")):
        return True, list(OPENROUTER_EFFORT)
    return False, []


def _fallback_chat_models() -> list[ModelInfo]:
    return [
        ModelInfo(id=m, name=m, supports_reasoning=False, reasoning_efforts=[], kind="chat")
        for m in OPENROUTER_FALLBACK_MODELS
    ]


def _fallback_models() -> list[ModelInfo]:
    models = _fallback_chat_models()
    models.extend(
        ModelInfo(id=m, name=m, supports_reasoning=False, reasoning_efforts=[], kind="image")
        for m in OPENROUTER_FALLBACK_IMAGE_MODELS
    )
    models.extend(
        ModelInfo(id=m, name=m, supports_reasoning=False, reasoning_efforts=[], kind="video")
        for m in OPENROUTER_FALLBACK_VIDEO_MODELS
    )
    return models


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
    first = tool_calls[0] or {}
    fn = first.get("function") or {}
    return str(fn.get("name") or first.get("type") or "")


def _looks_like_web_search(chunk: dict[str, Any]) -> bool:
    etype = str(chunk.get("type") or "").lower()
    if "web_search" in etype or "websearch" in etype:
        return True
    name = _tool_call_name(chunk).lower()
    return "search" in name or "web_search" in name


def _filename_for_mime(mime: str, stem: str) -> str:
    lowered = mime.lower()
    if "jpeg" in lowered or lowered.endswith("/jpg"):
        return f"{stem}.jpg"
    if "webp" in lowered:
        return f"{stem}.webp"
    if "gif" in lowered:
        return f"{stem}.gif"
    if "svg" in lowered:
        return f"{stem}.svg"
    return f"{stem}.png"


def _ext_from_url(url: str, default: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
        if path.endswith(ext):
            return ext
    return default
