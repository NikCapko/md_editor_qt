#!/usr/bin/env python3
"""
Parallel Markdown Editor
Редактор для параллельного перевода с синхронизацией по абзацам.
Файлы: .en.md (оригинал, слева) и .ru.md (перевод, справа)

Синхронизация прокрутки — рабочий алгоритм:
  клик по строке → выделить только её → выровнять парный абзац на другой стороне.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from export_parallel import export_file


class NumberBar(QWidget):
    """Панель номеров строк."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor
        self.setStyleSheet("background-color: #f0f0f0;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        painter.setFont(self.editor.font())
        painter.setPen(QColor("#888"))

        block = self.editor.firstVisibleBlock()
        offset = self.editor.contentOffset()
        block_rect = self.editor.blockBoundingGeometry(block).translated(offset)
        top = int(block_rect.top())
        line_height = self.editor.fontMetrics().height()
        block_number = block.blockNumber() + 1

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and top + line_height >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self.width() - 4,
                    line_height,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_number),
                )
            block = block.next()
            if block.isValid():
                block_rect = self.editor.blockBoundingGeometry(block).translated(offset)
                top = int(block_rect.top())
            block_number += 1


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor.document())
        self.editor = editor
        self.prev_block_number = -1

        self.heading_fmt = QTextCharFormat()
        self.heading_fmt.setForeground(QColor("#0066cc"))
        self.heading_fmt.setFontWeight(QFont.Weight.Bold)

        self.bold_fmt = QTextCharFormat()
        self.bold_fmt.setFontWeight(QFont.Weight.Bold)

        self.italic_fmt = QTextCharFormat()
        self.italic_fmt.setFontItalic(True)

        self.code_fmt = QTextCharFormat()
        self.code_fmt.setForeground(QColor("#cc0000"))
        self.code_fmt.setFontFamily("Consolas")

        self.link_fmt = QTextCharFormat()
        self.link_fmt.setForeground(QColor("#0066cc"))
        self.link_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)

        self.active_fmt = QTextCharFormat()
        self.active_fmt.setBackground(QColor("#fffacd"))

        self.rules = [
            (re.compile(r"^#{1,6}\s.+$"), "heading"),
            (re.compile(r"\*\*(.+?)\*\*"), "bold"),
            (re.compile(r"(?<!\*)\*([^\*]+?)\*(?!\*)"), "italic"),
            (re.compile(r"`([^`]+)`"), "code"),
            (re.compile(r"\[([^\]]+)\]\(([^\)]+)\)"), "link"),
        ]

    def highlightBlock(self, text: str) -> None:
        is_active = (
            self.currentBlock().blockNumber()
            == self.editor.textCursor().block().blockNumber()
        )
        n = len(text)
        formats: list = [None] * n

        for pattern, fmt_name in self.rules:
            for match in pattern.finditer(text):
                fmt = getattr(self, f"{fmt_name}_fmt")
                if fmt_name == "bold":
                    start, end = match.start() + 2, match.end() - 2
                elif fmt_name in ("italic", "code"):
                    start, end = match.start() + 1, match.end() - 1
                else:
                    start, end = match.start(), match.end()
                for i in range(max(0, start), min(n, end)):
                    formats[i] = fmt

        if is_active:
            for i in range(n):
                if formats[i] is None:
                    formats[i] = self.active_fmt
                else:
                    combined = QTextCharFormat(formats[i])
                    combined.setBackground(self.active_fmt.background())
                    formats[i] = combined

        i = 0
        while i < n:
            if formats[i] is not None:
                j = i
                while j < n and formats[j] is not None and formats[j] == formats[i]:
                    j += 1
                self.setFormat(i, j - i, formats[i])
                i = j
            else:
                i += 1


class HeadingOutline(QListWidget):
    """Боковой список markdown-заголовков (# … ######) для навигации."""

    heading_activated = Signal(int)  # position в документе

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

    def __init__(self, side: str = "left", parent=None):
        super().__init__(parent)
        self._side = side  # left | right — только для стиля
        self._headings: list[tuple[int, int, str]] = []  # (pos, level, title)
        self._updating = False
        self.setMinimumWidth(140)
        self.setMaximumWidth(260)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        accent = "#1a73e8" if side == "left" else "#188038"
        self.setStyleSheet(
            f"""
            QListWidget {{
                background: #fafafa;
                border: 1px solid #e0e0e0;
                font-size: 12px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 3px 6px;
                color: #333;
            }}
            QListWidget::item:selected {{
                background: {accent};
                color: white;
            }}
            """
        )
        self.itemClicked.connect(self._on_item_clicked)

    def rebuild(self, text: str) -> None:
        """Пересобрать список заголовков из текста документа."""
        self._updating = True
        self.clear()
        self._headings = []
        for m in self._HEADING_RE.finditer(text):
            level = len(m.group(1))
            title = m.group(2).strip()
            # убрать простой markdown из заголовка
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
            title = re.sub(r"`([^`]+)`", r"\1", title)
            pos = m.start()
            self._headings.append((pos, level, title))
            indent = "  " * (level - 1)
            item = QListWidgetItem(f"{indent}{'#' * level} {title}")
            item.setData(Qt.ItemDataRole.UserRole, pos)
            item.setToolTip(title)
            self.addItem(item)
        self._updating = False

    def highlight_for_position(self, pos: int) -> None:
        """Подсветить заголовок, в чьей зоне находится курсор (последний с pos_h <= pos)."""
        if not self._headings:
            self.clearSelection()
            return
        active = -1
        for i, (hpos, _level, _title) in enumerate(self._headings):
            if hpos <= pos:
                active = i
            else:
                break
        self._updating = True
        if active < 0:
            self.clearSelection()
        else:
            self.setCurrentRow(active)
            item = self.item(active)
            if item is not None:
                self.scrollToItem(item)
        self._updating = False

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        pos = item.data(Qt.ItemDataRole.UserRole)
        if pos is not None:
            self.heading_activated.emit(int(pos))


class CodeEditor(QPlainTextEdit):
    """Редактор с номерами строк; синхронизация только по клику мыши."""

    paragraph_clicked = Signal(int, int, bool)  # editor_idx, pos, do_scroll

    def __init__(self, editor_idx: int = 0):
        super().__init__()
        self.editor_idx = editor_idx
        self._syncing = False
        self._mouse_click = False

        self.number_bar = NumberBar(self)
        self.highlighter = MarkdownHighlighter(self)

        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.blockCountChanged.connect(self._update_number_bar_width)
        self.updateRequest.connect(self._update_number_bar)
        self._update_number_bar_width()

    def mousePressEvent(self, event):
        self._mouse_click = True
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.number_bar.setGeometry(
            QRect(cr.left(), cr.top(), self.number_bar.width(), cr.height())
        )

    def _update_number_bar_width(self, _=None):
        digits = len(str(max(1, self.blockCount())))
        width = self.fontMetrics().horizontalAdvance("9") * (digits + 1) + 10
        self.number_bar.setFixedWidth(width)
        self.setViewportMargins(width, 0, 0, 0)
        cr = self.contentsRect()
        self.number_bar.setGeometry(QRect(cr.left(), cr.top(), width, cr.height()))

    def _update_number_bar(self, rect, dy):
        if dy:
            self.number_bar.scroll(0, dy)
        else:
            self.number_bar.update(0, rect.y(), self.number_bar.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_number_bar_width()

    def _on_cursor_changed(self):
        cursor = self.textCursor()
        current_block = cursor.block()
        if self.highlighter.prev_block_number >= 0:
            prev_block = self.document().findBlockByNumber(
                self.highlighter.prev_block_number
            )
            if prev_block.isValid():
                self.highlighter.rehighlightBlock(prev_block)
        self.highlighter.rehighlightBlock(current_block)
        self.highlighter.prev_block_number = current_block.blockNumber()

        if not self._syncing:
            pos = self.textCursor().position()
            # mouse_click=True → ещё и выровнять скролл; иначе только подсветка пары
            do_scroll = self._mouse_click
            self._mouse_click = False
            self.paragraph_clicked.emit(self.editor_idx, pos, do_scroll)

    def highlight_line_at(self, pos: int, color: QColor) -> None:
        """Подсветка одной конкретной строки (на всю ширину), без текстового selection."""
        block = self.document().findBlock(pos)
        if not block.isValid():
            self.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        # курсор на блок, без выделения символов
        c = QTextCursor(block)
        c.clearSelection()
        sel.cursor = c
        self.setExtraSelections([sel])
        # перерисовать viewport — иначе FullWidthSelection иногда не виден
        self.viewport().update()

    def clear_line_highlight(self) -> None:
        self.setExtraSelections([])
        self.viewport().update()

    def highlight_search_matches(
        self,
        ranges: list,
        current: tuple | None = None,
    ) -> None:
        """Подсветка всех вхождений поиска.

        ranges — список (start, end);
        current — текущее совпадение (другой цвет).
        """
        all_fmt = QTextCharFormat()
        all_fmt.setBackground(QColor("#fff59d"))  # светло-жёлтый — все совпадения

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#ff9800"))  # оранжевый — текущее
        cur_fmt.setForeground(QColor("#000000"))

        extras = []
        cur_set = set()
        if current is not None:
            cur_set.add((current[0], current[1]))

        for start, end in ranges:
            if start >= end:
                continue
            sel = QTextEdit.ExtraSelection()
            if (start, end) in cur_set:
                sel.format = cur_fmt
            else:
                sel.format = all_fmt
            cursor = self.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            extras.append(sel)

        self.setExtraSelections(extras)

    def clear_search_highlight(self) -> None:
        self.setExtraSelections([])

    def get_line_info(self, pos: int) -> dict | None:
        """Информация о строке в позиции pos.

        Для непустой строки: абзац (группы непустых строк подряд) + смещение внутри него.
        Для пустой: is_empty=True, привязка 1:1 по номеру строки.
        """
        block = self.document().findBlock(pos)
        if not block.isValid():
            return None

        line_no = block.blockNumber()
        is_empty = not block.text().strip()

        if is_empty:
            return {
                "line_no": line_no,
                "is_empty": True,
                "para_idx": -1,
                "offset": 0,
                "para_start_line": line_no,
                "para_end_line": line_no + 1,
                "position": block.position(),
            }

        # начало абзаца: назад по непустым
        start = block
        while start.previous().isValid() and start.previous().text().strip():
            start = start.previous()
        # конец абзаца
        end = block
        while end.next().isValid() and end.next().text().strip():
            end = end.next()

        para_start_line = start.blockNumber()
        para_end_line = end.blockNumber() + 1
        offset = line_no - para_start_line

        # индекс абзаца среди непустых групп
        idx = 0
        b = self.document().begin()
        in_para = False
        while b.isValid():
            if b.text().strip():
                if not in_para:
                    in_para = True
                    if b == start:
                        break
                    idx += 1
            else:
                in_para = False
            b = b.next()

        return {
            "line_no": line_no,
            "is_empty": False,
            "para_idx": idx,
            "offset": offset,
            "para_start_line": para_start_line,
            "para_end_line": para_end_line,
            "position": block.position(),
        }

    def get_paragraph_range(self, para_idx: int) -> tuple[int, int] | None:
        """(start_line, end_line exclusive) для абзаца para_idx, или None."""
        count = 0
        b = self.document().begin()
        in_para = False
        start_line = None
        while b.isValid():
            if b.text().strip():
                if not in_para:
                    in_para = True
                    if count == para_idx:
                        start_line = b.blockNumber()
                    count += 1
                if start_line is not None and count > para_idx + 1:
                    # следующий абзац начался — но count уже увеличен на следующем
                    pass
            else:
                if in_para and start_line is not None and count == para_idx + 1:
                    return (start_line, b.blockNumber())
                in_para = False
            b = b.next()
        if start_line is not None:
            return (start_line, self.blockCount())
        return None

    def get_paragraph_position(self, idx: int):
        """Позиция начала абзаца idx (для совместимости)."""
        r = self.get_paragraph_range(idx)
        if r is None:
            return None
        block = self.document().findBlockByNumber(r[0])
        return block.position() if block.isValid() else None

    def get_paragraph_index(self, pos: int) -> int:
        info = self.get_line_info(pos)
        if info is None or info["is_empty"]:
            return -1
        return info["para_idx"]


class FindReplaceDialog(QDialog):
    """Поиск и замена: regex, учёт регистра, сторона (лево/право/оба)."""

    def __init__(self, parent: "ParallelEditor"):
        super().__init__(parent)
        self.main = parent
        self.setMinimumWidth(480)
        self._last_editor = None
        self._active_editor = None  # панель, с которой открыли диалог
        self._replace_mode = True

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Что искать…")
        self.find_edit.returnPressed.connect(self.find_next)
        form.addRow("Найти:", self.find_edit)

        self.replace_label = QLabel("Заменить на:")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("На что заменить…")
        self.replace_edit.returnPressed.connect(self.replace_one)
        form.addRow(self.replace_label, self.replace_edit)
        layout.addLayout(form)

        opts = QHBoxLayout()
        self.case_cb = QCheckBox("Учитывать регистр")
        self.case_cb.setToolTip("Если включено — Hello и hello считаются разными")
        self.regex_cb = QCheckBox("Регулярное выражение")
        self.regex_cb.setToolTip(
            "Синтаксис Python re; в замене доступны \\1, \\g<name>"
        )
        opts.addWidget(self.case_cb)
        opts.addWidget(self.regex_cb)
        opts.addStretch()
        layout.addLayout(opts)

        side_row = QHBoxLayout()
        side_row.addWidget(QLabel("Где:"))
        self.scope = QComboBox()
        self.scope.addItem("Активная панель", "focus")
        self.scope.addItem("Оригинал (слева)", "en")
        self.scope.addItem("Перевод (справа)", "ru")
        self.scope.addItem("Обе панели", "both")
        side_row.addWidget(self.scope, 1)
        layout.addLayout(side_row)

        btns = QHBoxLayout()
        self.btn_next = QPushButton("Найти далее")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self.find_next)
        self.btn_prev = QPushButton("Найти ранее")
        self.btn_prev.clicked.connect(self.find_prev)
        self.btn_repl = QPushButton("Заменить")
        self.btn_repl.clicked.connect(self.replace_one)
        self.btn_all = QPushButton("Заменить все")
        self.btn_all.clicked.connect(self.replace_all)
        btns.addWidget(self.btn_next)
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_repl)
        btns.addWidget(self.btn_all)
        layout.addLayout(btns)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #555;")
        layout.addWidget(self.status)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        self.find_edit.setFocus()

    def set_replace_mode(self, enabled: bool) -> None:
        """True — полный диалог замены; False — только поиск."""
        self._replace_mode = enabled
        self.setWindowTitle("Заменить" if enabled else "Найти")
        self.replace_label.setVisible(enabled)
        self.replace_edit.setVisible(enabled)
        self.btn_repl.setVisible(enabled)
        self.btn_all.setVisible(enabled)
        # подогнать размер окна под видимые виджеты
        self.adjustSize()

    def _pattern(self):
        raw = self.find_edit.text()
        if not raw:
            self.status.setText("Введите строку поиска")
            self.status.setStyleSheet("color: #c5221f;")
            return None

        # Учитывать регистр: флажок ВКЛ → case-sensitive (flags=0)
        # флажок ВЫКЛ → игнорировать регистр (re.IGNORECASE)
        flags = 0
        if not self.case_cb.isChecked():
            flags |= re.IGNORECASE

        try:
            if self.regex_cb.isChecked():
                return re.compile(raw, flags)
            return re.compile(re.escape(raw), flags)
        except re.error as e:
            self.status.setText(f"Ошибка regex: {e}")
            self.status.setStyleSheet("color: #c5221f;")
            return None

    def _editors(self):
        mode = self.scope.currentData()
        if mode == "en":
            return [self.main.left_editor]
        if mode == "ru":
            return [self.main.right_editor]
        if mode == "both":
            return [self.main.left_editor, self.main.right_editor]
        # focus: панель, с которой открыли диалог (диалог сам в фокусе)
        if self._active_editor is not None:
            return [self._active_editor]
        w = self.main.focusWidget()
        if w is self.main.right_editor:
            return [self.main.right_editor]
        return [self.main.left_editor]

    def _select_match(self, editor: "CodeEditor", start: int, end: int) -> None:
        cursor = editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.centerCursor()
        editor.setFocus()

    def _highlight_all_in_editors(
        self, pat, current_editor=None, current_span=None
    ) -> None:
        """Подсветить все совпадения во всех целевых редакторах."""
        if pat is None:
            return
        for editor in self._editors():
            text = editor.toPlainText()
            ranges = [(m.start(), m.end()) for m in pat.finditer(text)]
            cur = current_span if editor is current_editor else None
            editor.highlight_search_matches(ranges, current=cur)

    def _clear_all_search_highlights(self) -> None:
        for ed in (self.main.left_editor, self.main.right_editor):
            ed.clear_search_highlight()

    def reject(self) -> None:
        self._clear_all_search_highlights()
        super().reject()

    def closeEvent(self, event) -> None:
        self._clear_all_search_highlights()
        super().closeEvent(event)

    def find_next(self) -> None:
        self._find(forward=True)

    def find_prev(self) -> None:
        self._find(forward=False)

    def _find(self, forward: bool = True) -> None:
        pat = self._pattern()
        if pat is None:
            return

        editors = self._editors()
        for editor in editors:
            text = editor.toPlainText()
            cursor = editor.textCursor()
            if forward:
                start_at = (
                    cursor.selectionEnd()
                    if cursor.hasSelection()
                    else cursor.position()
                )
                if self._last_editor is not editor and len(editors) > 1:
                    start_at = 0
                m = pat.search(text, start_at)
                if m is None and start_at > 0:
                    m = pat.search(text, 0)
            else:
                start_at = (
                    cursor.selectionStart()
                    if cursor.hasSelection()
                    else cursor.position()
                )
                matches = list(pat.finditer(text))
                m = None
                for cand in reversed(matches):
                    if cand.start() < start_at:
                        m = cand
                        break
                if m is None and matches:
                    m = matches[-1]  # wrap

            if m is not None:
                self._select_match(editor, m.start(), m.end())
                self._last_editor = editor
                self._highlight_all_in_editors(
                    pat, current_editor=editor, current_span=(m.start(), m.end())
                )
                total = sum(
                    len(list(pat.finditer(ed.toPlainText()))) for ed in self._editors()
                )
                case_info = (
                    "с учётом регистра"
                    if self.case_cb.isChecked()
                    else "без учёта регистра"
                )
                self.status.setText(
                    f"Найдено «{m.group()}» — совпадение, всего: {total} ({case_info})"
                )
                self.status.setStyleSheet("color: #188038;")
                return

        # ничего не найдено — всё равно обновить подсветку (пусто)
        self._highlight_all_in_editors(pat)
        self.status.setText("Ничего не найдено")
        self.status.setStyleSheet("color: #c5221f;")

    def replace_one(self) -> None:
        if not self._replace_mode:
            self.find_next()
            return
        pat = self._pattern()
        if pat is None:
            return
        repl = self.replace_edit.text()
        for editor in self._editors():
            cursor = editor.textCursor()
            if not cursor.hasSelection():
                continue
            selected = cursor.selectedText().replace(chr(0x2029), chr(10))
            m = pat.fullmatch(selected)
            if m is None:
                continue
            try:
                if self.regex_cb.isChecked():
                    new = pat.sub(repl, selected, count=1)
                else:
                    new = repl
            except re.error as e:
                self.status.setText(f"Ошибка замены: {e}")
                self.status.setStyleSheet("color: #c5221f;")
                return
            cursor.insertText(new)
            self.status.setText("Заменено 1")
            self.status.setStyleSheet("color: #188038;")
            self.find_next()
            return
        self.find_next()

    def replace_all(self) -> None:
        if not self._replace_mode:
            return
        pat = self._pattern()
        if pat is None:
            return
        repl = self.replace_edit.text()
        total = 0
        for editor in self._editors():
            text = editor.toPlainText()
            try:
                new_text, n = pat.subn(repl, text)
            except re.error as e:
                self.status.setText(f"Ошибка замены: {e}")
                self.status.setStyleSheet("color: #c5221f;")
                return
            if n:
                pos = editor.textCursor().position()
                editor._syncing = True
                editor.setPlainText(new_text)
                c = editor.textCursor()
                c.setPosition(min(pos, len(new_text)))
                editor.setTextCursor(c)
                editor._syncing = False
                total += n
        if total:
            self.status.setText(f"Заменено: {total}")
            self.status.setStyleSheet("color: #188038;")
            # обновить подсветку оставшихся вхождений
            pat2 = self._pattern()
            if pat2 is not None:
                self._highlight_all_in_editors(pat2)
        else:
            self.status.setText("Нечего заменять")
            self.status.setStyleSheet("color: #c5221f;")


class ParallelEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parallel Markdown Editor")
        self.resize(1400, 800)

        self._en_path = None
        self._ru_path = None
        self._en_modified = False
        self._ru_modified = False
        self.current_file_base = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        headers = QHBoxLayout()
        self.lbl_en = QLabel("Оригинал (.en.md)")
        self.lbl_en.setStyleSheet("font-weight: bold; color: #1a73e8;")
        self.lbl_ru = QLabel("Перевод (.ru.md)")
        self.lbl_ru.setStyleSheet("font-weight: bold; color: #188038;")
        headers.addWidget(self.lbl_en, 1)
        headers.addWidget(self.lbl_ru, 1)
        root.addLayout(headers)

        editors_row = QHBoxLayout()
        editors_row.setSpacing(2)

        self.left_outline = HeadingOutline("left")
        self.left_editor = CodeEditor(0)
        self.right_editor = CodeEditor(1)
        self.right_outline = HeadingOutline("right")

        editors_row.addWidget(self.left_outline, 0)
        editors_row.addWidget(self.left_editor, 1)
        editors_row.addWidget(self.right_editor, 1)
        editors_row.addWidget(self.right_outline, 0)
        root.addLayout(editors_row, 1)

        self.editors = [self.left_editor, self.right_editor]
        self.left_editor.paragraph_clicked.connect(self._sync_paragraph)
        self.right_editor.paragraph_clicked.connect(self._sync_paragraph)
        self.left_editor.textChanged.connect(lambda: self._on_text_changed("en"))
        self.right_editor.textChanged.connect(lambda: self._on_text_changed("ru"))
        self.left_editor.cursorPositionChanged.connect(
            lambda: self._update_active_heading(self.left_editor, self.left_outline)
        )
        self.right_editor.cursorPositionChanged.connect(
            lambda: self._update_active_heading(self.right_editor, self.right_outline)
        )
        self.left_outline.heading_activated.connect(
            lambda pos: self._goto_heading(self.left_editor, pos)
        )
        self.right_outline.heading_activated.connect(
            lambda pos: self._goto_heading(self.right_editor, pos)
        )

        self.setStatusBar(QStatusBar())
        self._create_menu()
        self.statusBar().showMessage("Клик по строке — подсветка пары и выравнивание")

    def _create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&Файл")

        open_action = QAction("Открыть пару…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_files)
        file_menu.addAction(open_action)

        save_action = QAction("Сохранить", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_files)
        file_menu.addAction(save_action)

        save_as_action = QAction("Сохранить как…", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._save_files_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        export_pdf_action = QAction("Экспорт в PDF…", self)
        export_pdf_action.triggered.connect(lambda: self._export("pdf"))
        file_menu.addAction(export_pdf_action)

        export_epub_action = QAction("Экспорт в EPUB…", self)
        export_epub_action.triggered.connect(lambda: self._export("epub"))
        file_menu.addAction(export_epub_action)

        file_menu.addSeparator()
        quit_action = QAction("Выход", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        edit_menu = menubar.addMenu("&Правка")
        find_action = QAction("Найти…", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self._show_find)
        edit_menu.addAction(find_action)

        replace_action = QAction("Заменить…", self)
        replace_action.setShortcut(QKeySequence.StandardKey.Replace)
        replace_action.triggered.connect(self._show_find_replace)
        edit_menu.addAction(replace_action)

        tr_menu = menubar.addMenu("&Перевод")
        google_action = QAction("Открыть в Google Chrome (Google Translate)…", self)
        google_action.triggered.connect(lambda: self._open_in_translator("google"))
        tr_menu.addAction(google_action)

        yandex_action = QAction("Открыть в Yandex Browser (Yandex Translate)…", self)
        yandex_action.triggered.connect(lambda: self._open_in_translator("yandex"))
        tr_menu.addAction(yandex_action)

    def _show_find(self) -> None:
        self._open_find_dialog(replace_mode=False)

    def _show_find_replace(self) -> None:
        self._open_find_dialog(replace_mode=True)

    def _open_find_dialog(self, replace_mode: bool = True) -> None:
        if not hasattr(self, "_find_dlg") or self._find_dlg is None:
            self._find_dlg = FindReplaceDialog(self)
        dlg = self._find_dlg

        # запомнить активную панель до открытия диалога
        w = self.focusWidget()
        if w is self.right_editor:
            dlg._active_editor = self.right_editor
        elif w is self.left_editor:
            dlg._active_editor = self.left_editor
        elif dlg._active_editor is None:
            dlg._active_editor = self.left_editor

        if isinstance(w, CodeEditor) and w.textCursor().hasSelection():
            sel = w.textCursor().selectedText().replace(chr(0x2029), chr(10))
            if sel and chr(10) not in sel:
                dlg.find_edit.setText(sel)

        dlg.set_replace_mode(replace_mode)
        dlg.find_edit.setFocus()
        dlg.find_edit.selectAll()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _ensure_en_file(self) -> Path | None:
        """Сохраняет текущий оригинал в .en.md и возвращает путь."""
        en_text = self.left_editor.toPlainText()
        if not en_text.strip():
            QMessageBox.warning(self, "Перевод", "Оригинал (.en.md) пуст.")
            return None

        if self._en_path is not None:
            try:
                self._en_path.write_text(en_text, encoding="utf-8")
                self._en_modified = False
                return self._en_path
            except Exception as e:
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось сохранить .en.md:\n{e}"
                )
                return None

        if self.current_file_base:
            p = Path(str(self.current_file_base) + ".en.md")
            try:
                p.write_text(en_text, encoding="utf-8")
                self._en_path = p
                return p
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
                return None

        # временный файл
        try:
            td = Path(tempfile.gettempdir()) / "parallel_editor"
            td.mkdir(parents=True, exist_ok=True)
            p = td / "original.en.md"
            p.write_text(en_text, encoding="utf-8")
            return p
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать временный файл:\n{e}"
            )
            return None

    def _find_browser(self, service: str) -> str | None:
        """Путь к браузеру: google → google-chrome-stable; yandex → yandex-browser-stable."""
        if service == "google":
            candidates = [
                "google-chrome-stable",
                "google-chrome",
                "chromium-browser",
                "chromium",
            ]
        else:
            candidates = [
                "yandex-browser-stable",
                "yandex-browser",
            ]
        for name in candidates:
            path = shutil.which(name)
            if path:
                return path
        return None

    def _open_in_translator(self, service: str) -> None:
        """Открывает .en.md в Chrome (Google) или Yandex Browser.
        service: "google" | "yandex"
        """
        en_path = self._ensure_en_file()
        if en_path is None:
            return
        browser = self._find_browser(service)
        if browser is None:
            if service == "google":
                msg = (
                    "Не найден google-chrome-stable (или google-chrome / chromium).\n"
                    "Установите Google Chrome."
                )
            else:
                msg = (
                    "Не найден yandex-browser-stable (или yandex-browser).\n"
                    "Установите Yandex Browser."
                )
            QMessageBox.warning(self, "Перевод", msg)
            return
        file_url = en_path.resolve().as_uri()
        if service == "google":
            label = "Google Chrome"
        else:
            label = "Yandex Browser"
        try:
            subprocess.Popen(
                [browser, file_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось запустить {label}:\n{e}")
            return
        self.statusBar().showMessage(f"Открыто в {label}: {en_path.name}", 5000)

    def _export(self, fmt: str) -> None:
        """Экспорт в PDF или EPUB: таблица или построчно."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Экспорт в {fmt.upper()}")
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("Формат вёрстки:"))
        radio_table = QRadioButton("Таблица: две колонки (оригинал | перевод)")
        radio_line = QRadioButton("Построчно: оригинал, затем перевод (блоками)")
        radio_table.setChecked(True)
        layout.addWidget(radio_table)
        layout.addWidget(radio_line)

        open_after = QCheckBox("Открыть после сохранения")
        open_after.setChecked(True)
        layout.addWidget(open_after)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        layout_mode = "table" if radio_table.isChecked() else "interlinear"
        should_open = open_after.isChecked()

        if fmt == "pdf":
            filt = "PDF (*.pdf)"
            default_ext = ".pdf"
        else:
            filt = "EPUB (*.epub)"
            default_ext = ".epub"

        default_name = "parallel"
        if self.current_file_base:
            default_name = Path(self.current_file_base).name
        elif self._en_path:
            default_name = self._en_path.stem.replace(".en", "")

        suffix = "_table" if layout_mode == "table" else "_lines"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Сохранить {fmt.upper()}",
            f"{default_name}{suffix}{default_ext}",
            filt,
        )
        if not path:
            return
        if not path.lower().endswith(default_ext):
            path += default_ext

        en_text = self.left_editor.toPlainText()
        ru_text = self.right_editor.toPlainText()
        title = default_name

        try:
            export_file(path, en_text, ru_text, layout=layout_mode, title=title)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))
            return

        self.statusBar().showMessage(f"Экспортировано: {path}", 5000)

        if should_open:
            url = QUrl.fromLocalFile(str(Path(path).resolve()))
            if not QDesktopServices.openUrl(url):
                try:
                    subprocess.Popen(
                        ["xdg-open", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception as e:
                    QMessageBox.warning(
                        self,
                        "Экспорт",
                        f"Файл сохранён, но не удалось открыть:\n{e}",
                    )

    def _sync_paragraph(
        self, editor_idx: int, pos: int, do_scroll: bool = True
    ) -> None:
        """Подсветка строки и пары; выравнивание по Y.
        Пара = тот же абзац по индексу (1↔1) на другой стороне.
        """
        source = self.editors[editor_idx]
        target = self.editors[1 - editor_idx]
        para_idx = source.get_paragraph_index(pos)
        if para_idx < 0:
            return
        target_pos = target.get_paragraph_position(para_idx)
        if target_pos is None:
            self.statusBar().showMessage(
                f"Абзац {para_idx + 1}: нет пары на другой стороне", 3000
            )
            target.clear_line_highlight()
            return
        src_para_pos = source.get_paragraph_position(para_idx)
        source_cursor = source.textCursor()
        if src_para_pos is not None:
            source_cursor.setPosition(src_para_pos)
        source_rect = source.cursorRect(source_cursor)
        source_y = source_rect.top()
        target._syncing = True
        target_cursor = QTextCursor(target.document())
        target_cursor.setPosition(target_pos)
        target.setTextCursor(target_cursor)
        source.highlight_line_at(pos, QColor(200, 230, 255))
        target.highlight_line_at(target_pos, QColor(200, 245, 200))
        target_rect = target.cursorRect(target_cursor)
        target_y = target_rect.top()
        if do_scroll:
            diff = source_y - target_y
            if abs(diff) > 5:
                line_height = target_rect.height() if target_rect.height() > 0 else 16
                scrollbar = target.verticalScrollBar()
                scroll_units = int(diff / line_height)
                scrollbar.setValue(scrollbar.value() - scroll_units)
        target._syncing = False
        self.statusBar().showMessage(f"Абзац {para_idx + 1} выровнен", 2500)

    def _on_text_changed(self, side: str) -> None:
        if side == "en":
            if self.left_editor._syncing:
                return
            self._en_modified = True
            name = self._en_path.name if self._en_path else "Оригинал (.en.md)"
            self.lbl_en.setText(f"Оригинал — {name} *")
            self.left_outline.rebuild(self.left_editor.toPlainText())
            self._update_active_heading(self.left_editor, self.left_outline)
        else:
            if self.right_editor._syncing:
                return
            self._ru_modified = True
            name = self._ru_path.name if self._ru_path else "Перевод (.ru.md)"
            self.lbl_ru.setText(f"Перевод — {name} *")
            self.right_outline.rebuild(self.right_editor.toPlainText())
            self._update_active_heading(self.right_editor, self.right_outline)
        self._update_title()

    def _update_active_heading(
        self, editor: CodeEditor, outline: HeadingOutline
    ) -> None:
        pos = editor.textCursor().position()
        outline.highlight_for_position(pos)

    def _goto_heading(self, editor: CodeEditor, pos: int) -> None:
        """Перейти к заголовку по клику в оглавлении."""
        cursor = editor.textCursor()
        cursor.setPosition(pos)
        editor.setTextCursor(cursor)
        editor.centerCursor()
        editor.setFocus()

    def _open_files(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Открыть .en.md / .ru.md",
            "",
            "Markdown (*.en.md *.ru.md *.md);;All (*)",
        )
        if not file_path:
            return

        if file_path.endswith(".en.md"):
            base = file_path[:-6]
            self._en_path = Path(file_path)
            self._ru_path = Path(base + ".ru.md")
        elif file_path.endswith(".ru.md"):
            base = file_path[:-6]
            self._ru_path = Path(file_path)
            self._en_path = Path(base + ".en.md")
        else:
            base = file_path
            self._en_path = Path(file_path)
            self._ru_path = Path(file_path + ".ru.md")

        self.current_file_base = base
        self._load_files()

    def _load_files(self) -> None:
        self.left_editor._syncing = True
        self.right_editor._syncing = True

        en_path = (
            Path(self.current_file_base + ".en.md")
            if self.current_file_base
            else self._en_path
        )
        ru_path = (
            Path(self.current_file_base + ".ru.md")
            if self.current_file_base
            else self._ru_path
        )

        if en_path and en_path.exists():
            self.left_editor.setPlainText(en_path.read_text(encoding="utf-8"))
            self._en_path = en_path
            self._en_modified = False
            self.lbl_en.setText(f"Оригинал — {en_path.name}")
        else:
            self.left_editor.setPlainText("")
            self.lbl_en.setText("Оригинал (.en.md)")

        if ru_path and ru_path.exists():
            self.right_editor.setPlainText(ru_path.read_text(encoding="utf-8"))
            self._ru_path = ru_path
            self._ru_modified = False
            self.lbl_ru.setText(f"Перевод — {ru_path.name}")
        else:
            self.right_editor.setPlainText("")
            self.lbl_ru.setText("Перевод (.ru.md)")

        self.left_editor._syncing = False
        self.right_editor._syncing = False
        self.left_outline.rebuild(self.left_editor.toPlainText())
        self.right_outline.rebuild(self.right_editor.toPlainText())
        self._update_active_heading(self.left_editor, self.left_outline)
        self._update_active_heading(self.right_editor, self.right_outline)
        self._update_title()
        self.statusBar().showMessage("Файлы загружены", 2000)

    def _save_files(self) -> None:
        if not self.current_file_base and not self._en_path:
            self._save_files_as()
            return

        if self.current_file_base:
            en_path = Path(self.current_file_base + ".en.md")
            ru_path = Path(self.current_file_base + ".ru.md")
        else:
            en_path = self._en_path
            ru_path = self._ru_path

        try:
            if en_path:
                en_path.write_text(self.left_editor.toPlainText(), encoding="utf-8")
                self._en_path = en_path
                self._en_modified = False
                self.lbl_en.setText(f"Оригинал — {en_path.name}")
            if ru_path:
                ru_path.write_text(self.right_editor.toPlainText(), encoding="utf-8")
                self._ru_path = ru_path
                self._ru_modified = False
                self.lbl_ru.setText(f"Перевод — {ru_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return

        self._update_title()
        self.statusBar().showMessage("Сохранено", 2000)

    def _save_files_as(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить как .en.md", "", "Markdown (*.en.md)"
        )
        if not file_path:
            return
        if file_path.endswith(".en.md"):
            base = file_path[:-6]
        else:
            base = file_path
        self.current_file_base = base
        self._save_files()

    def _update_title(self) -> None:
        parts = []
        if self._en_path:
            parts.append(self._en_path.name)
        if self._ru_path:
            parts.append(self._ru_path.name)
        title = "Parallel Markdown Editor"
        if parts:
            title = " | ".join(parts) + " — " + title
        if self._en_modified or self._ru_modified:
            title = "• " + title
        self.setWindowTitle(title)

    def closeEvent(self, event) -> None:
        if self._en_modified or self._ru_modified:
            reply = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить перед выходом?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_files()
                event.accept()
            elif reply == QMessageBox.StandardButton.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ParallelEditor()
    window.show()

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if file_path.endswith(".en.md") or file_path.endswith(".ru.md"):
            base = file_path[:-6]
        else:
            base = file_path
        window.current_file_base = base
        window._load_files()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
