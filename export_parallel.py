#!/usr/bin/env python3
"""Экспорт текста в PDF и EPUB (одна колонка)."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# Обработка текста
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


def export_pdf(path: str | Path, text: str, title: str = "Document") -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    path = Path(path)
    paras = split_paragraphs(text)

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

    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontName=font_bold, fontSize=16,
        spaceAfter=12, alignment=TA_CENTER,
    )
    heading_style = ParagraphStyle(
        "Heading", parent=styles["Normal"], fontName=font_bold, fontSize=14, leading=18,
        spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#333333")
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName=font_name, fontSize=11, leading=16,
        spaceAfter=6, alignment=TA_LEFT
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

    for para in paras:
        style = heading_style if is_heading(para) else body_style
        story.append(p(para, style))

    doc.build(story)


# ---------------------------------------------------------------------------
# EPUB (zip + XHTML, без внешних зависимостей)
# ---------------------------------------------------------------------------


def _format_epub_para(text: str) -> str:
    safe = xml_escape(strip_md_light(text)).replace("\n", "<br/>")
    if is_heading(text):
        return f"<h2>{safe or '—'}</h2>"
    return f"<p>{safe or '—'}</p>"


def export_epub(path: str | Path, text: str, title: str = "Document") -> None:
    path = Path(path)
    paras = split_paragraphs(text)
    blocks = [_format_epub_para(p) for p in paras]

    book_id = "doc-" + re.sub(r"[^a-zA-Z0-9]+", "-", title)[:40]

    body = f"<h1>{xml_escape(title)}</h1>\n" + "\n".join(blocks)

    css = """
    body { font-family: serif; line-height: 1.6; padding: 1em; color: #111; }
    h1 { font-size: 1.6em; text-align: center; margin-bottom: 1.5em; }
    h2 { font-size: 1.3em; margin-top: 1.5em; margin-bottom: 0.5em; color: #333; }
    p { margin-bottom: 1em; text-align: justify; }
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


# ---------------------------------------------------------------------------
# Главный интерфейс
# ---------------------------------------------------------------------------


def export_file(path: str | Path, text: str, title: str = "Document") -> None:
    """
    Экспортирует один текст в PDF или EPUB в зависимости от расширения файла.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        export_pdf(path, text, title=title)
    elif suffix == ".epub":
        export_epub(path, text, title=title)
    else:
        raise ValueError(f"Неподдерживаемый формат: {suffix}. Используйте .pdf или .epub")
