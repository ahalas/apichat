"""Convert stored messages and attachments into provider request payloads."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

from app.files.detector import MIME_BY_EXT

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".xml",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".log",
    ".sh",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".rb",
    ".php",
}

IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
XAI_IMAGE_MIMES = {"image/png", "image/jpeg"}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 10


def data_url(mime: str, data_base64: str) -> str:
    return f"data:{mime};base64,{data_base64}"


def _ext(filename: str) -> str:
    if "." not in (filename or ""):
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def is_image(att: dict[str, Any]) -> bool:
    mime = str(att.get("mime") or "").lower()
    if mime.startswith("image/"):
        return True
    return _ext(str(att.get("filename") or "")) in {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def is_pdf(att: dict[str, Any]) -> bool:
    mime = str(att.get("mime") or "").lower()
    return mime == "application/pdf" or _ext(str(att.get("filename") or "")) == ".pdf"


def is_text_file(att: dict[str, Any]) -> bool:
    mime = str(att.get("mime") or "").lower()
    if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
        return True
    return _ext(str(att.get("filename") or "")) in TEXT_EXTENSIONS


def decode_bytes(att: dict[str, Any]) -> bytes:
    return base64.b64decode(str(att.get("data_base64") or ""), validate=False)


def decode_text(att: dict[str, Any]) -> str | None:
    try:
        raw = decode_bytes(att)
    except Exception:
        return None
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def prompt_text(content: str, attachments: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    text = (content or "").strip()
    if text:
        parts.append(text)
    for att in attachments or []:
        if is_image(att) or is_pdf(att):
            continue
        if not is_text_file(att):
            name = att.get("filename") or "file"
            parts.append(f"(Attached file `{name}`.)")
            continue
        body = decode_text(att)
        if body is None:
            continue
        name = att.get("filename") or "file"
        parts.append(f"Attached file `{name}`:\n```\n{body}\n```")
    if parts:
        return "\n\n".join(parts)
    if any(is_image(a) or is_pdf(a) or not is_text_file(a) for a in (attachments or [])):
        return "Please analyze the attached file(s)."
    return text


def needs_document_files(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for att in msg.get("attachments") or []:
            if is_pdf(att):
                return True
            if not is_image(att) and not is_text_file(att):
                return True
    return False


def xai_image_url(att: dict[str, Any]) -> str:
    mime = str(att.get("mime") or "image/png").lower()
    b64 = str(att.get("data_base64") or "")
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime in XAI_IMAGE_MIMES and b64:
        return data_url(mime, b64)
    try:
        from PIL import Image

        img = Image.open(BytesIO(decode_bytes(att)))
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGBA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return data_url("image/png", encoded)
    except Exception:
        return data_url(mime if mime.startswith("image/") else "image/png", b64)


def to_chat_messages(messages: list[dict[str, Any]], *, include_pdf_parts: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        atts = msg.get("attachments") or []
        if role != "user":
            out.append({"role": role, "content": msg.get("content") or ""})
            continue
        text = prompt_text(str(msg.get("content") or ""), atts)
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for att in atts:
            if not att.get("data_base64"):
                continue
            if is_image(att):
                mime = str(att.get("mime") or "image/png")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url(mime, str(att["data_base64"])), "detail": "high"},
                    }
                )
            elif include_pdf_parts and is_pdf(att):
                mime = str(att.get("mime") or "application/pdf")
                parts.append(
                    {
                        "type": "file",
                        "file": {
                            "filename": str(att.get("filename") or "document.pdf"),
                            "file_data": data_url(mime, str(att["data_base64"])),
                        },
                    }
                )
        if len(parts) == 1:
            out.append({"role": "user", "content": text})
        else:
            out.append({"role": "user", "content": parts})
    return out


def to_responses_input(
    messages: list[dict[str, Any]],
    file_ids: dict[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        atts = msg.get("attachments") or []
        if role != "user":
            out.append({"role": role, "content": [{"type": "output_text", "text": content}]})
            continue
        text = prompt_text(content, atts)
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        for att in atts:
            key = attachment_key(att)
            if is_image(att) and att.get("data_base64"):
                parts.append({"type": "input_image", "image_url": xai_image_url(att)})
            elif key in file_ids:
                parts.append({"type": "input_file", "file_id": file_ids[key]})
        out.append({"role": "user", "content": parts})
    return out


def attachment_key(att: dict[str, Any]) -> str:
    b64 = str(att.get("data_base64") or "")
    return f"{att.get('filename') or 'file'}:{len(b64)}:{b64[:48]}"


def document_attachments(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for att in msg.get("attachments") or []:
            if is_image(att) or is_text_file(att):
                continue
            key = attachment_key(att)
            if key in seen or not att.get("data_base64"):
                continue
            seen.add(key)
            found.append(att)
    return found


def citation_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, list):
        for item in value:
            urls.extend(citation_urls(item))
        return _unique(urls)
    if isinstance(value, str) and value.startswith("http"):
        return [value]
    if isinstance(value, dict):
        for key in ("url", "uri", "href"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.startswith("http"):
                urls.append(raw)
        for nested in value.values():
            if isinstance(nested, (list, dict)):
                urls.extend(citation_urls(nested))
    return _unique(urls)


def format_sources(urls: list[str]) -> str:
    if not urls:
        return ""
    lines = "\n".join(f"- {url}" for url in urls[:12])
    return f"\n\n**Sources**\n{lines}"


def mime_for_filename(filename: str, fallback: str = "application/octet-stream") -> str:
    return MIME_BY_EXT.get(_ext(filename), fallback)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def iter_sse_json(response: Any) -> Any:
    event_type = ""
    for line in response.iter_lines():
        if not line:
            event_type = ""
            continue
        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
        if text.startswith("event: "):
            event_type = text[7:].strip()
            continue
        if not text.startswith("data: "):
            continue
        payload = text[6:].strip()
        if payload == "[DONE]":
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            event_type = ""
            continue
        if isinstance(data, dict) and event_type and not data.get("type"):
            data["type"] = event_type
        event_type = ""
        yield data


def stream_error_message(response: Any) -> str:
    body_text = response.read().decode("utf-8", errors="replace")
    message = body_text
    try:
        parsed = json.loads(body_text)
        err = parsed.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or err)
        elif isinstance(err, str):
            message = err
        elif parsed.get("message"):
            message = str(parsed["message"])
    except json.JSONDecodeError:
        pass
    return message or f"HTTP {getattr(response, 'status_code', '')}"


def chat_delta_text(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


def chat_annotation_urls(chunk: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    choices = chunk.get("choices") or []
    if not choices:
        return []
    delta = choices[0].get("delta") or {}
    message = choices[0].get("message") or {}
    for blob in (delta, message, chunk):
        urls.extend(citation_urls(blob.get("annotations")))
        urls.extend(citation_urls(blob.get("citations")))
    return _unique(urls)
