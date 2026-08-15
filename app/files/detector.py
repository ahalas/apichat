"""Detect embedded file attachments in assistant messages."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, asdict
from typing import Any

SUPPORTED_EXTENSIONS = {
    ".pdf", ".xlsx", ".xls", ".docx", ".csv", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".txt", ".md", ".json", ".zip", ".mp4",
}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".zip": "application/zip",
    ".mp4": "video/mp4",
}

EXT_BY_MIME = {v: k for k, v in MIME_BY_EXT.items()}

CODE_BLOCK_RE = re.compile(
    r"```([^\n`]+)\n([A-Za-z0-9+/=\s]+?)```",
    re.DOTALL,
)
DATA_URL_RE = re.compile(
    r"data:([a-zA-Z0-9/+.-]+);base64,([A-Za-z0-9+/=\s]+)",
)
FILE_LINE_RE = re.compile(
    r"(?:^|\n)FILE:\s*([^\n]+)\n([A-Za-z0-9+/=\s]+)",
    re.MULTILINE,
)


@dataclass
class Attachment:
    filename: str
    mime: str
    data_base64: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Attachment":
        return cls(
            filename=str(data.get("filename", "file.bin")),
            mime=str(data.get("mime", "application/octet-stream")),
            data_base64=str(data.get("data_base64", "")),
        )


def _looks_like_base64(text: str, min_length: int = 16) -> bool:
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < min_length:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", cleaned):
        return False
    try:
        base64.b64decode(cleaned, validate=True)
        return True
    except Exception:
        return False


def _ext_from_filename(name: str) -> str:
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            return ext
    return ".bin"


def _filename_from_mime(mime: str, index: int) -> str:
    ext = EXT_BY_MIME.get(mime, ".bin")
    return f"attachment_{index}{ext}"


def detect_attachments(content: str) -> list[Attachment]:
    found: list[Attachment] = []
    seen: set[str] = set()

    def add(filename: str, mime: str, b64: str, *, min_length: int = 16) -> None:
        cleaned = re.sub(r"\s+", "", b64)
        if not _looks_like_base64(cleaned, min_length=min_length):
            return
        key = cleaned[:64]
        if key in seen:
            return
        seen.add(key)
        if not filename or filename == "base64":
            filename = _filename_from_mime(mime, len(found) + 1)
        found.append(Attachment(filename=filename, mime=mime, data_base64=cleaned))

    for match in CODE_BLOCK_RE.finditer(content):
        label = match.group(1).strip()
        payload = match.group(2).strip()
        if label.lower() in {"base64", "text", "plaintext"} and "." not in label:
            continue
        ext = _ext_from_filename(label)
        mime = MIME_BY_EXT.get(ext, "application/octet-stream")
        add(label if "." in label else f"file{ext}", mime, payload, min_length=8 if "." in label else 16)

    for match in DATA_URL_RE.finditer(content):
        mime = match.group(1)
        payload = match.group(2)
        add(_filename_from_mime(mime, len(found) + 1), mime, payload)

    for match in FILE_LINE_RE.finditer(content):
        filename = match.group(1).strip()
        payload = match.group(2).strip()
        ext = _ext_from_filename(filename)
        mime = MIME_BY_EXT.get(ext, "application/octet-stream")
        add(filename, mime, payload, min_length=8)

    return found


def attachments_to_json(attachments: list[Attachment]) -> list[dict[str, str]]:
    return [a.to_dict() for a in attachments]


def attachments_from_json(data: list[dict[str, Any]]) -> list[Attachment]:
    return [Attachment.from_dict(item) for item in data]
