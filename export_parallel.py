#!/usr/bin/env python3
"""Экспорт текста в PDF и EPUB.

Поддерживаемые layout:
  single      — одна колонка (для обычного .md файла)
  table       — таблица из 2 колонок (оригинал | перевод)
  interlinear — блоки оригинал → перевод по очереди
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# Абзацы и обработка текста
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


def is_heading(text: str) -> bool:
    """Проверяет, является ли абзац заголовком Markdown."""
    return bool(re.match(r"^#{1,6}\s+", text.strip()))


# ---------------------------------------------------------------------------
# PDF (reportlab)
# ---------------------------------------------------------------------------


def _find_font() -> tuple[str, str]:
    """Возвращает (regular, bold) пути к TTF с кириллицей."""
    candidates = [
        (
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
    ]
    for reg, bold in candidates:
        if Path(reg).exists() and Path(bold).exists():
            return reg, bold
    return "", ""


def export_pdf(
    path: str | Path,
    en_text: str,
    ru_text: str = "",
    layout: str = "table",
    title: str = "Document",
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    path = Path(path)

    # Автоматически переключаемся в single, если второго текста нет
    is_single = (not ru_text.strip()) or (layout == "single")
    pairs = pair_paragraphs(en_text, ru_text) if not is_single else []

    reg, bold = _find_font()
    font_name = "DejaVu"
    font_bold = "DejaVu-Bold"
    if reg:
        pdfmetrics.registerFont(TTFont("DejaVu", reg))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
    else:
        font_name = "Helvetica"
        font_bold = "Helvetica-Bold"

    styles = getSampleStyleSheet()

    # Стили для PDF
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"], fontName=font_name, fontSize=10, leading=14, alignment=TA_LEFT
    )
    single_style = ParagraphStyle(
        "Single", parent=styles["Normal"], fontName=font_name, fontSize=11, leading=16, spaceAfter=6
    )
    heading_style = ParagraphStyle(
        "Heading", parent=styles["Normal"], fontName=font_bold, fontSize=14, leading=18,
        spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#333333")
    )
    head_style = ParagraphStyle(
        "Head", parent=styles["Normal"], fontName=font_bold, fontSize=10, leading=13
    )
    en_block = ParagraphStyle(
        "EnBlock", parent=styles["Normal"], fontName=font_name, fontSize=10, leading=13,
        textColor=colors.HexColor("#1a73e8"), spaceAfter=2,
    )
    ru_block = ParagraphStyle(
        "RuBlock", parent=styles["Normal"], fontName=font_name, fontSize=10, leading=13,
        textColor=colors.HexColor("#188038"), spaceAfter=10,
    )
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontName=font_bold, fontSize=16,
        spaceAfter=12, alignment=TA_CENTER,
    )

    def p(text: str, style: ParagraphStyle) -> Paragraph:
        safe = strip_md_light(text)
        safe = html.escape(safe).replace("\n", "<br/>")
        return Paragraph(safe or "—", style)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=0,
        rightMargin=0,
        topMargin=0,
        bottomMargin=0,
        title=title,
    )
    story = [Paragraph(html.escape(title), title_style), Spacer(1, 12)]

    if is_single:
        paras = split_paragraphs(en_text)
        for para in paras:
            style = heading_style if is_heading(para) else single_style
            story.append(p(para, style))

    elif layout == "table":
        data = [[Paragraph("Оригинал", head_style), Paragraph("Перевод", head_style)]]
        for en, ru in pairs:
            data.append([p(en, cell_style), p(ru, cell_style)])

        table = Table(data, colWidths=[270, 270])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ]
            )
        )
        story.append(table)
    else:
        # interlinear
        for en, ru in pairs:
            block = [p(en, en_block), p(ru, ru_block)]
            story.append(KeepTogether(block))

    doc.build(story)


# ---------------------------------------------------------------------------
# EPUB (zip + XHTML, без внешних зависимостей)
# ---------------------------------------------------------------------------


def _format_epub_para(text: str) -> str:
    safe = xml_escape(strip_md_light(text)).replace("\n", "<br/>")
    if is_heading(text):
        return f"<h2>{safe or '—'}</h2>"
    return f"<p>{safe or '—'}</p>"


def export_epub(
    path: str | Path,
    en_text: str,
    ru_text: str = "",
    layout: str = "table",
    title: str = "Document",
) -> None:
    path = Path(path)

    is_single = (not ru_text.strip()) or (layout == "single")
    pairs = pair_paragraphs(en_text, ru_text) if not is_single else []

    book_id = "doc-" + re.sub(r"[^a-zA-Z0-9]+", "-", title)[:40]

    if is_single:
        paras = split_paragraphs(en_text)
        blocks = [_format_epub_para(p) for p in paras]
        body = f"<h1>{xml_escape(title)}</h1>\n" + "\n".join(blocks)
        css = """
        body { font-family: serif; line-height: 1.6; padding: 1em; }
        h1 { font-size: 1.6em; text-align: center; margin-bottom: 1.5em; }
        h2 { font-size: 1.3em; margin-top: 1.5em; margin-bottom: 0.5em; color: #333; }
        p { margin-bottom: 1em; text-align: justify; }
        """
    elif layout == "table":
        rows = []
        for en, ru in pairs:
            rows.append(
                "<tr>"
                f"<td class='en'>{_format_epub_para(en)}</td>"
                f"<td class='ru'>{_format_epub_para(ru)}</td>"
                "</tr>"
            )
        body = (
            f"<h1>{xml_escape(title)}</h1>\n"
            "<table>\n"
            "<thead><tr><th>Original</th><th>Translation</th></tr></thead>\n"
            "<tbody>\n" + "\n".join(rows) + "\n</tbody></table>"
        )
        css = """
        body { font-family: sans-serif; padding: 1em; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #bbb; padding: 0.5em; vertical-align: top; width: 50%; }
        th { background: #e8eaed; font-size: 0.9em; }
        tr:nth-child(even) td { background: #f8f9fa; }
        .en { color: #1a73e8; }
        .ru { color: #188038; }
        h1 { font-size: 1.4em; text-align: center; }
        h2 { font-size: 1.1em; margin-top: 0; }
        p { margin: 0; }
        """
    else:
        # interlinear
        blocks = []
        for en, ru in pairs:
            blocks.append(
                f"<div class='pair'>"
                f"{_format_epub_para(en)}"
                f"{_format_epub_para(ru)}"
                f"</div>"
            )
        body = f"<h1>{xml_escape(title)}</h1>\n" + "\n".join(blocks)
        css = """
        body { font-family: sans-serif; padding: 1em; }
        .pair { margin-bottom: 1.2em; padding-bottom: 0.6em; border-bottom: 1px solid #e0e0e0; }
        .en { color: #1a73e8; margin: 0 0 0.3em 0; }
        .ru { color: #188038; margin: 0; }
        h1 { font-size: 1.4em; text-align: center; }
        h2 { font-size: 1.1em; margin: 0 0 0.3em 0; }
        p { margin: 0; }
        """

    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru">
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
    <dc:language>ru</dc:language>
    <meta property="dcterms:modified">2024-01-01T00:00:00Z</meta>
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
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ru">
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
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/chapter.xhtml", chapter)
        zf.writestr("OEBPS/style.css", css)


def export_file(
    path: str | Path,
    text: str,
    text2: str = "",
    layout: str = "single",
    title: str = "Document",
) -> None:
    """
    Универсальная функция экспорта.
    Если text2 пуст или layout == "single", экспортирует как один документ.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        export_pdf(path, text, text2, layout=layout, title=title)
    elif suffix == ".epub":
        export_epub(path, text, text2, layout=layout, title=title)
    else:
        raise ValueError(f"Неподдерживаемый формат: {suffix}")
