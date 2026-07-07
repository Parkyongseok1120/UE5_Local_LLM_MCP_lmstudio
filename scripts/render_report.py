#!/usr/bin/env python
"""Render markdown report text to md/pptx/docx/pdf (graceful degradation)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

ReportFormat = Literal["md", "pptx", "docx", "pdf"]


def render_report(
    text: str,
    *,
    format: ReportFormat = "md",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render report text. Markdown always succeeds; other formats degrade if deps missing."""
    fmt = str(format or "md").strip().lower()
    if fmt not in {"md", "pptx", "docx", "pdf"}:
        fmt = "md"

    result: dict[str, Any] = {
        "ok": True,
        "format": fmt,
        "requestedFormat": format,
        "degraded": False,
        "outputPath": "",
        "notes": [],
    }

    if output_path is None:
        suffix = {"md": ".md", "pptx": ".pptx", "docx": ".docx", "pdf": ".pdf"}[fmt]
        output_path = Path("report") / f"report{suffix}"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "md":
        out.write_text(text, encoding="utf-8")
        result["outputPath"] = str(out.resolve())
        return result

    try:
        if fmt == "docx":
            _render_docx(text, out)
        elif fmt == "pptx":
            _render_pptx(text, out)
        elif fmt == "pdf":
            _render_pdf(text, out)
        result["outputPath"] = str(out.resolve())
        return result
    except ImportError as exc:
        md_fallback = out.with_suffix(".md")
        md_fallback.write_text(text, encoding="utf-8")
        result["degraded"] = True
        result["format"] = "md"
        result["outputPath"] = str(md_fallback.resolve())
        result["notes"].append(f"Missing dependency for {fmt}: {exc}. Wrote UTF-8 markdown instead.")
        return result
    except Exception as exc:
        md_fallback = out.with_suffix(".md")
        md_fallback.write_text(text, encoding="utf-8")
        result["degraded"] = True
        result["format"] = "md"
        result["outputPath"] = str(md_fallback.resolve())
        result["notes"].append(f"Render failed for {fmt}: {exc}. Wrote UTF-8 markdown instead.")
        return result


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "Report"
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line.lstrip("#").strip() or "Section"
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or not sections:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return sections


def _render_docx(text: str, out: Path) -> None:
    from docx import Document  # type: ignore[import-untyped]

    doc = Document()
    for title, body in _split_sections(text):
        doc.add_heading(title, level=1)
        for paragraph in body.split("\n\n"):
            if paragraph.strip():
                doc.add_paragraph(paragraph.strip())
    doc.save(out)


def _render_pptx(text: str, out: Path) -> None:
    from pptx import Presentation  # type: ignore[import-untyped]
    from pptx.util import Pt  # type: ignore[import-untyped]

    prs = Presentation()
    for title, body in _split_sections(text):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        body_shape = slide.placeholders[1]
        tf = body_shape.text_frame
        tf.clear()
        for index, paragraph in enumerate(body.split("\n")):
            if not paragraph.strip():
                continue
            p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
            p.text = paragraph.strip()
            p.font.size = Pt(16)
    prs.save(out)


def _render_pdf(text: str, out: Path) -> None:
    from reportlab.lib.pagesizes import letter  # type: ignore[import-untyped]
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    c = canvas.Canvas(str(out), pagesize=letter)
    width, height = letter
    y = height - 72
    for line in text.splitlines():
        if y < 72:
            c.showPage()
            y = height - 72
        c.drawString(72, y, line[:120])
        y -= 14
    c.save()


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a markdown report to md/pptx/docx/pdf.")
    parser.add_argument("--input", default="-")
    parser.add_argument("--format", default="md", choices=["md", "pptx", "docx", "pdf"])
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if args.input == "-":
        import sys

        text = sys.stdin.read()
    else:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    payload = render_report(text, format=args.format, output_path=args.output or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
