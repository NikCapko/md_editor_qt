#!/usr/bin/env python3
"""Экспорт параллельного текста в PDF и EPUB.

layouts:
  table      — таблица из 2 колонок (оригинал | перевод)
  interlinear — блоки оригинал → перевод по очереди
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# Абзацы
# ---------------------------------------------------------------------------

def split_paragraphs(text: str) -> list[str]:
    """Делит текст на абзацы по пустым строкам."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paras: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.strip() == "":
            if buf:
                paras.append("\n".join(buf))
                buf = []
        else:
            buf.append(line)
    if buf:
        paras.append("\n".join(buf))
    return paras


def pair_paragraphs(en_text: str, ru_text: str) -> list[tuple[str, str]]:
    en = split_paragraphs(en_text)
    ru = split_paragraphs(ru_text)
    n = max(len(en), len(ru))
    pairs = []
    for i in range(n):
        a = en[i] if i < len(en) else ""
        b = ru[i] if i < len(ru) else ""
        pairs.append((a, b))
    return pairs


def strip_md_light(text: str) -> str:
    """Лёгкая очистка markdown для plain/PDF (заголовки, ** * `)."""
    t = text
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return t


# ---------------------------------------------------------------------------
# PDF (reportlab)
# ---------------------------------------------------------------------------

def _find_font() -> tuple[str, str]:
    """Возвращает (regular, bold) пути к TTF с кириллицей."""
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
    ]
    for reg, bold in candidates:
        if Path(reg).exists() and Path(bold).exists():
            return reg, bold
    return "", ""


def export_pdf(
    path: str | Path,
    en_text: str,
    ru_text: str,
    layout: str = "table",
    title: str = "Parallel text",
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    path = Path(path)
    pairs = pair_paragraphs(en_text, ru_text)

    reg, bold = _find_font()
    font_name = "Helvetica"
    font_bold = "Helvetica-Bold"
    if reg:
        pdfmetrics.registerFont(TTFont("ParaFont", reg))
        pdfmetrics.registerFont(TTFont("ParaFont-Bold", bold))
        font_name = "ParaFont"
        font_bold = "ParaFont-Bold"

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=12,
        alignment=TA_LEFT,
    )
    head_style = ParagraphStyle(
        "Head",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=10,
        leading=13,
    )
    en_block = ParagraphStyle(
        "EnBlock",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1a73e8"),
        spaceAfter=2,
    )
    ru_block = ParagraphStyle(
        "RuBlock",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#188038"),
        spaceAfter=10,
    )
    title_style = ParagraphStyle(
        "TitleRu",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=14,
        spaceAfter=12,
    )

    def p(text: str, style) -> Paragraph:
        safe = strip_md_light(text)
        safe = html.escape(safe).replace("\n", "<br/>")
        return Paragraph(safe or "—", style)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    )
    story = [Paragraph(html.escape(title), title_style), Spacer(1, 6)]

    if layout == "table":
        data = [
            [
                Paragraph("Оригинал", head_style),
                Paragraph("Перевод", head_style),
            ]
        ]
        for en, ru in pairs:
            data.append([p(en, cell_style), p(ru, cell_style)])

        col_w = (A4[0] - 30 * mm) / 2
        table = Table(data, colWidths=[col_w, col_w], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaed")),
                    ("FONTNAME", (0, 0), (-1, 0), font_bold),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bdc1c6")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8f9fa")],
                    ),
                ]
            )
        )
        story.append(table)
    else:
        # interlinear
        for i, (en, ru) in enumerate(pairs, 1):
            block = [
                p(en, en_block),
                p(ru, ru_block),
            ]
            story.append(KeepTogether(block))

    doc.build(story)


# ---------------------------------------------------------------------------
# EPUB (zip + XHTML, без внешних зависимостей)
# ---------------------------------------------------------------------------

def _xhtml_escape(text: str) -> str:
    return xml_escape(strip_md_light(text)).replace("\n", "<br/>")


def export_epub(
    path: str | Path,
    en_text: str,
    ru_text: str,
    layout: str = "table",
    title: str = "Parallel text",
) -> None:
    path = Path(path)
    pairs = pair_paragraphs(en_text, ru_text)
    book_id = "parallel-" + re.sub(r"[^a-zA-Z0-9]+", "-", title)[:40]

    if layout == "table":
        rows = []
        for en, ru in pairs:
            rows.append(
                "<tr>"
                f"<td class='en'>{_xhtml_escape(en) or '—'}</td>"
                f"<td class='ru'>{_xhtml_escape(ru) or '—'}</td>"
                "</tr>"
            )
        body = (
            f"<h1>{xml_escape(title)}</h1>\n"
            "<table>\n"
            "<thead><tr><th>Original</th><th>Translation</th></tr></thead>\n"
            "<tbody>\n"
            + "\n".join(rows)
            + "\n</tbody></table>"
        )
        css = """
table { width: 100%; border-collapse: collapse; }
th, td { border: 1px solid #bbb; padding: 0.4em 0.5em; vertical-align: top; width: 50%; }
th { background: #e8eaed; }
tr:nth-child(even) td { background: #f8f9fa; }
.en { color: #1a73e8; }
.ru { color: #188038; }
h1 { font-size: 1.4em; }
"""
    else:
        blocks = []
        for en, ru in pairs:
            blocks.append(
                f"<div class='pair'>"
                f"<p class='en'>{_xhtml_escape(en) or '—'}</p>"
                f"<p class='ru'>{_xhtml_escape(ru) or '—'}</p>"
                f"</div>"
            )
        body = f"<h1>{xml_escape(title)}</h1>\n" + "\n".join(blocks)
        css = """
.pair { margin-bottom: 1.2em; padding-bottom: 0.6em; border-bottom: 1px solid #e0e0e0; }
.en { color: #1a73e8; margin: 0 0 0.3em 0; }
.ru { color: #188038; margin: 0; }
h1 { font-size: 1.4em; }
"""

    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{xml_escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""

    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{xml_escape(book_id)}</dc:identifier>
    <dc:title>{xml_escape(title)}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""

    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">
<head><title>Nav</title></head>
<body>
  <nav epub:type="toc">
    <ol><li><a href="chapter.xhtml">{xml_escape(title)}</a></li></ol>
  </nav>
</body>
</html>
"""

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be first and uncompressed
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/chapter.xhtml", chapter)
        zf.writestr("OEBPS/style.css", css)


def export_file(
    path: str | Path,
    en_text: str,
    ru_text: str,
    layout: str = "table",
    title: str = "Parallel text",
) -> None:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        export_pdf(path, en_text, ru_text, layout=layout, title=title)
    elif suffix == ".epub":
        export_epub(path, en_text, ru_text, layout=layout, title=title)
    else:
        raise ValueError(f"Неподдерживаемый формат: {suffix}")
