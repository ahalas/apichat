"""Local FastAPI app wrapping config, chat history, and providers."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import DEFAULT_OUTPUT_FOLDER, load_config, merge_config, save_config
from app.database import Database
from app.files.convert import ConversionError, convert_office_attachments
from app.files.detector import attachments_from_json, attachments_to_json, detect_attachments
from app.files.export import export_pdf, export_text
from app.files.saver import save_all_attachments
from app.providers.base import ModelInfo, parse_api_error
from app.providers.messages import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from app.providers.openrouter import OpenRouterClient
from app.providers.xai import XAIClient

db = Database()
_stop_flags: dict[str, bool] = {}


class ConfigUpdate(BaseModel):
    xai_api_key: str | None = None
    openrouter_api_key: str | None = None
    output_folder: str | None = None
    disabled_models: dict | None = None


class NewConversation(BaseModel):
    provider: str = "xAI"
    model: str = ""
    effort: str = "medium"


class AttachmentIn(BaseModel):
    filename: str = "file"
    mime: str = "application/octet-stream"
    data_base64: str = ""


class SendPayload(BaseModel):
    conversation_id: str | None = None
    text: str = ""
    provider: str = "xAI"
    model: str
    mode: str = "Chat"
    effort: str | None = None
    duration: int = 8
    attachments: list[AttachmentIn] = Field(default_factory=list)
    web_search: bool = False


class SavePayload(BaseModel):
    content: str = ""
    title: str = "file"
    format: str = "txt"
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    open_after: bool = True


def web_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="Apichat")

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        cfg = load_config()
        return {
            "xai_api_key_set": bool(cfg.xai_api_key),
            "openrouter_api_key_set": bool(cfg.openrouter_api_key),
            "output_folder": cfg.output_folder or str(DEFAULT_OUTPUT_FOLDER),
            "default_provider": cfg.default_provider,
            "default_model": cfg.default_model,
            "default_effort": cfg.default_effort,
            "disabled_models": cfg.disabled_models or {"xAI": [], "OpenRouter": []},
        }

    @app.put("/api/config")
    def put_config(payload: ConfigUpdate) -> dict[str, Any]:
        cfg = load_config()
        updates: dict[str, Any] = {}
        if payload.xai_api_key is not None:
            updates["xai_api_key"] = payload.xai_api_key
        if payload.openrouter_api_key is not None:
            updates["openrouter_api_key"] = payload.openrouter_api_key
        if payload.output_folder is not None:
            folder = payload.output_folder.strip()
            if folder:
                try:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise HTTPException(400, str(exc)) from exc
            updates["output_folder"] = folder
        if payload.disabled_models is not None:
            updates["disabled_models"] = payload.disabled_models
        save_config(merge_config(cfg, updates))
        return get_config()

    @app.post("/api/test/{provider}")
    def test_provider(provider: str) -> dict[str, Any]:
        cfg = load_config()
        if provider == "xAI":
            if not cfg.xai_api_key:
                return {"ok": False, "message": "Enter an xAI key first", "models": []}
            client = XAIClient(cfg.xai_api_key)
            ok, msg = client.test_connection()
            models = [_model_dict(m) for m in client.list_models()] if ok else []
            return {"ok": ok, "message": msg, "models": models}
        if provider == "OpenRouter":
            if not cfg.openrouter_api_key:
                return {"ok": False, "message": "Enter an OpenRouter key first", "models": []}
            client = OpenRouterClient(cfg.openrouter_api_key)
            ok, msg = client.test_connection()
            models = [_model_dict(m) for m in client.list_models()] if ok else []
            return {"ok": ok, "message": msg, "models": models}
        raise HTTPException(400, "Unknown provider")

    @app.get("/api/models")
    def list_models(provider: str = "xAI", mode: str = "chat", include_all: bool = False) -> dict[str, Any]:
        cfg = load_config()
        models = _list_models(cfg, provider)
        if not include_all:
            disabled = set(cfg.disabled_for(provider))
            models = [m for m in models if m.id not in disabled]
            kind = mode.lower()
            if provider == "OpenRouter":
                kind = "chat"
            models = [m for m in models if m.kind == kind]
        return {"models": [_model_dict(m) for m in models]}

    @app.get("/api/conversations")
    def list_conversations() -> dict[str, Any]:
        return {
            "conversations": [
                {
                    "id": c.id,
                    "title": c.title,
                    "provider": c.provider,
                    "model": c.model,
                    "effort": c.effort,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                }
                for c in db.list_conversations()
            ]
        }

    @app.post("/api/conversations")
    def create_conversation(payload: NewConversation) -> dict[str, Any]:
        conv = db.create_conversation(provider=payload.provider, model=payload.model, effort=payload.effort)
        return asdict(conv)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, str]:
        db.delete_conversation(conversation_id)
        return {"status": "ok"}

    @app.get("/api/conversations/{conversation_id}/messages")
    def list_messages(conversation_id: str) -> dict[str, Any]:
        conv = db.get_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        messages = []
        for m in db.list_messages(conversation_id):
            messages.append(
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "attachments": m.attachments,
                    "created_at": m.created_at,
                }
            )
        return {
            "conversation": asdict(conv),
            "messages": messages,
        }

    @app.post("/api/send")
    def send(payload: SendPayload) -> StreamingResponse:
        text = payload.text.strip()
        attachments = _normalize_attachments(payload.attachments)
        if not text and not attachments:
            raise HTTPException(400, "Empty message")
        return StreamingResponse(
            _send_events(payload, attachments),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/stop/{conversation_id}")
    def stop(conversation_id: str) -> dict[str, str]:
        _stop_flags[conversation_id] = True
        return {"status": "ok"}

    @app.post("/api/save")
    def save_file(payload: SavePayload) -> dict[str, Any]:
        cfg = load_config()
        folder = cfg.get_output_folder()
        try:
            if payload.attachments:
                atts = attachments_from_json(payload.attachments)
                paths = save_all_attachments(folder, atts, fallback_stem=payload.title.replace(" ", "_")[:32])
            elif payload.format == "pdf":
                paths = [export_pdf(folder, payload.content, payload.title)]
            else:
                paths = [export_text(folder, payload.content, payload.title)]
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        saved = [str(p) for p in paths]
        if payload.open_after and paths:
            _open_path(str(paths[0]))
        return {"paths": saved, "folder": str(folder)}

    @app.post("/api/open-folder")
    def open_folder() -> dict[str, str]:
        folder = load_config().get_output_folder()
        _open_path(str(folder))
        return {"folder": str(folder)}

    static = web_dir()
    if static.exists():
        app.mount("/", StaticFiles(directory=str(static), html=True), name="web")
    return app


def _model_dict(m: ModelInfo) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "kind": m.kind,
        "supports_reasoning": m.supports_reasoning,
        "reasoning_efforts": m.reasoning_efforts or [],
    }


def _list_models(cfg, provider: str) -> list[ModelInfo]:
    if provider == "xAI":
        if not cfg.xai_api_key:
            return []
        return XAIClient(cfg.xai_api_key).list_models()
    if not cfg.openrouter_api_key:
        return []
    return OpenRouterClient(cfg.openrouter_api_key).list_models()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _send_events(payload: SendPayload, user_attachments: list[dict[str, Any]]):
    cfg = load_config()
    provider = payload.provider
    mode = payload.mode
    text = payload.text.strip()
    web_search = bool(payload.web_search) and mode == "Chat"
    if mode in {"Image", "Video"}:
        provider = "xAI"
        user_attachments = []
        web_search = False
    if provider == "xAI" and not cfg.xai_api_key:
        yield _sse({"type": "error", "message": "Add your xAI API key in Settings."})
        return
    if provider == "OpenRouter" and not cfg.openrouter_api_key:
        yield _sse({"type": "error", "message": "Add your OpenRouter API key in Settings."})
        return
    if not payload.model:
        yield _sse({"type": "error", "message": "Select a model."})
        return

    conv_id = payload.conversation_id
    if not conv_id:
        conv = db.create_conversation(provider=provider, model=payload.model, effort=payload.effort or "medium")
        conv_id = conv.id
    conv = db.get_conversation(conv_id)
    if not conv:
        yield _sse({"type": "error", "message": "Conversation not found."})
        return

    title = conv.title
    if title == "New chat":
        seed = text or (user_attachments[0]["filename"] if user_attachments else "New chat")
        title = seed[:48] + ("…" if len(seed) > 48 else "")
        db.update_conversation(conv_id, title=title)
    db.update_conversation(conv_id, provider=provider, model=payload.model, effort=payload.effort or conv.effort)
    db.add_message(conv_id, "user", text, user_attachments)
    assistant = db.add_message(conv_id, "assistant", "")
    yield _sse(
        {
            "type": "meta",
            "conversation_id": conv_id,
            "title": title,
            "message_id": assistant.id,
        }
    )

    _stop_flags[conv_id] = False
    effort = payload.effort if payload.effort not in {None, "—", "-"} else None
    content = ""
    attachments: list[dict[str, Any]] = []
    status_queue: list[str] = []

    def on_status(message: str) -> None:
        status_queue.append(message)

    try:
        if mode == "Image":
            att = XAIClient(cfg.xai_api_key).generate_image(text, payload.model)
            attachments = attachments_to_json([att])
            content = "Image generated."
            db.update_message_content(assistant.id, content, attachments)
            yield _sse({"type": "media", "content": content, "attachments": attachments})
            yield _sse({"type": "done", "conversation_id": conv_id})
            return
        if mode == "Video":
            class Flag:
                def is_set(self_inner) -> bool:
                    return bool(_stop_flags.get(conv_id))

            att = XAIClient(cfg.xai_api_key).generate_video(
                text, payload.model, duration=payload.duration, stop_event=Flag()
            )
            attachments = attachments_to_json([att])
            content = "Video generated."
            db.update_message_content(assistant.id, content, attachments)
            yield _sse({"type": "media", "content": content, "attachments": attachments})
            yield _sse({"type": "done", "conversation_id": conv_id})
            return

        history = [
            {"role": m.role, "content": m.content, "attachments": m.attachments}
            for m in db.list_messages(conv_id)
            if m.id != assistant.id
        ]
        if provider == "xAI":
            stream = XAIClient(cfg.xai_api_key).stream_chat(
                payload.model, history, effort=effort, web_search=web_search, on_status=on_status
            )
        else:
            models = {m.id: m for m in _list_models(cfg, "OpenRouter")}
            info = models.get(payload.model)
            stream = OpenRouterClient(cfg.openrouter_api_key).stream_chat(
                payload.model,
                history,
                effort=effort,
                supports_reasoning=bool(info and info.supports_reasoning),
                web_search=web_search,
                on_status=on_status,
            )
        for token in stream:
            while status_queue:
                yield _sse({"type": "status", "text": status_queue.pop(0)})
            if _stop_flags.get(conv_id):
                break
            if not token:
                continue
            content += token
            yield _sse({"type": "token", "text": token})
        while status_queue:
            yield _sse({"type": "status", "text": status_queue.pop(0)})
        detected = detect_attachments(content)
        attachments = attachments_to_json(detected)
        db.update_message_content(assistant.id, content, attachments)
        yield _sse({"type": "done", "conversation_id": conv_id, "attachments": attachments})
    except Exception as exc:
        message = parse_api_error(exc)
        if not content:
            content = ""
        db.update_message_content(assistant.id, content or message, attachments)
        yield _sse({"type": "error", "message": message})


def _normalize_attachments(items: list[AttachmentIn]) -> list[dict[str, Any]]:
    if not items:
        return []
    if len(items) > MAX_ATTACHMENTS:
        raise HTTPException(400, f"Too many attachments (max {MAX_ATTACHMENTS}).")
    out: list[dict[str, Any]] = []
    for item in items:
        filename = (item.filename or "file").strip() or "file"
        mime = (item.mime or "application/octet-stream").strip() or "application/octet-stream"
        raw = "".join((item.data_base64 or "").split())
        if not raw:
            continue
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise HTTPException(400, f"Invalid attachment: {filename}") from exc
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(400, f"{filename} is larger than 20 MB.")
        out.append({"filename": filename, "mime": mime, "data_base64": raw})
    try:
        return convert_office_attachments(out)
    except ConversionError as exc:
        raise HTTPException(400, str(exc)) from exc


def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


app = create_app()
