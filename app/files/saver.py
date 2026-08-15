"""Write detected attachments to the configured output folder."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from app.files.detector import Attachment


INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    cleaned = INVALID_CHARS.sub("_", name.strip())
    return cleaned or "file.bin"


def unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = Path(sanitize_filename(filename))
    stem = base.stem or "file"
    suffix = base.suffix or ".bin"
    candidate = folder / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def save_attachment(folder: Path, attachment: Attachment, fallback_stem: str = "file") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    filename = attachment.filename
    if not filename or filename == "file.bin":
        ext = ".bin"
        for known in (".pdf", ".xlsx", ".csv", ".png", ".jpg", ".txt", ".json", ".zip", ".mp4"):
            if attachment.mime == f"application/{known.lstrip('.')}" or known in attachment.mime:
                ext = known
                break
        filename = f"{fallback_stem}{ext}"
    path = unique_path(folder, filename)
    data = base64.b64decode(attachment.data_base64)
    path.write_bytes(data)
    return path


def save_all_attachments(folder: Path, attachments: list[Attachment], fallback_stem: str = "file") -> list[Path]:
    return [save_attachment(folder, att, fallback_stem=fallback_stem) for att in attachments]
