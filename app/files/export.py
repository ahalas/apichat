"""Fallback export for plain-text assistant messages."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from app.files.saver import sanitize_filename, unique_path

_FONT_NAME = "ExportSans"


def export_text(folder: Path, content: str, title: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = sanitize_filename(f"{title}_{stamp}.txt")
    path = unique_path(folder, filename)
    path.write_text(content, encoding="utf-8")
    return path


def export_pdf(folder: Path, content: str, title: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = sanitize_filename(f"{title}_{stamp}.pdf")
    path = unique_path(folder, filename)

    text = _prepare_text(content)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font_path = _unicode_font_path()
    if font_path:
        pdf.add_font(_FONT_NAME, fname=str(font_path))
        pdf.set_font(_FONT_NAME, size=11)
    else:
        pdf.set_font("Helvetica", size=11)
        text = text.encode("latin-1", errors="replace").decode("latin-1")
    pdf.multi_cell(0, 6, text)
    pdf.output(str(path))
    return path


def _prepare_text(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)


def _unicode_font_path() -> Path | None:
    windir = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates = [
        *_bundled_font_paths(),
        windir / "Fonts" / "arial.ttf",
        windir / "Fonts" / "Arial.ttf",
        windir / "Fonts" / "segoeui.ttf",
        windir / "Fonts" / "calibri.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/croscore/Arimo-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _bundled_font_paths() -> list[Path]:
    local = Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf"
    paths = [local]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        root = Path(sys._MEIPASS)
        paths.extend(
            [
                root / "app" / "files" / "fonts" / "DejaVuSans.ttf",
                root / "fonts" / "DejaVuSans.ttf",
            ]
        )
    return paths
