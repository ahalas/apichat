"""Fallback export for plain-text assistant messages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from app.files.saver import sanitize_filename, unique_path


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

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, content.replace("\r\n", "\n"))
    pdf.output(str(path))
    return path
