#!/usr/bin/env python3
"""
Markdown Editor
Простой редактор Markdown-файлов с подсветкой синтаксиса,
номерами строк, боковым оглавлением, поиском/заменой и экспортом.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRect, QSettings, QSize, Qt, QUrl, Signal
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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
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

# Опциональные импорты — если модулей нет, соответствующие функции отключатся.
try:
    from export_parallel import export_file  # type: ignore

    _HAS_EXPORT = True
except Exception:
    _HAS_EXPORT = False

try:
    from text_corrector import normalize_text  # type: ignore

    _HAS_CORRECTOR = True
except Exception:
    _HAS_CORRECTOR = False


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

        base_pt = self.editor.font().pointSize() or 11
        self.heading_fmts = {}
        for level, delta in [(1, 8), (2, 6), (3, 4), (4, 2), (5, 1), (6, 0)]:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#0066cc"))
            fmt.setFontWeight(QFont.Weight.Bold)
            fmt.setFontPointSize(base_pt + delta)
            self.heading_fmts[level] = fmt

        self.bold_italic_fmt = QTextCharFormat()
        self.bold_italic_fmt.setFontWeight(QFont.Weight.Bold)
        self.bold_italic_fmt.setFontItalic(True)

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
            (re.compile(r"\*\*\*(.+?)\*\*\*"), "bold_italic"),
            (re.compile(r"\*\*(.+?)\*\*"), "bold"),
            (re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)"), "italic"),
            (re.compile(r"`([^`]+)`"), "code"),
            (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), "link"),
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
                if fmt_name == "heading":
                    level = 0
                    for ch in text:
                        if ch == "#":
                            level += 1
                        else:
                            break
                    level = max(1, min(6, level))
                    fmt = self.heading_fmts.get(level, self.heading_fmts[6])
                    start, end = match.start(), match.end()
                elif fmt_name == "bold_italic":
                    fmt = self.bold_italic_fmt
                    start, end = match.start() + 3, match.end() - 3
                elif fmt_name == "bold":
                    fmt = self.bold_fmt
                    start, end = match.start() + 2, match.end() - 2
                elif fmt_name in ("italic", "code"):
                    fmt = getattr(self, f"{fmt_name}_fmt")
                    start, end = match.start() + 1, match.end() - 1
                else:
                    fmt = getattr(self, f"{fmt_name}_fmt")
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
    """Боковой список markdown-заголовков для навигации."""

    heading_activated = Signal(int)  # позиция в документе

    _ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    _BOLD_HEADING_RE = re.compile(
        r"^(?<!\*)\*\*(?!\*)(?P<bold_text>.+?)(?<!\*)\*\*(?!\*)$", re.MULTILINE
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._headings: list[tuple[int, int, str]] = []
        self._updating = False
        self.setMinimumWidth(220)
        self.setMaximumWidth(260)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            """
            QListWidget {
                background: #fafafa;
                border: 1px solid #e0e0e0;
                font-size: 12px;
                outline: none;
            }
            QListWidget::item {
                padding: 3px 6px;
                color: #333;
            }
            QListWidget::item:selected {
                background: #1a73e8;
                color: white;
            }
            """
        )
        self.itemClicked.connect(self._on_item_clicked)

    def rebuild(self, text: str) -> None:
        self._updating = True
        self.clear()
        self._headings = []

        def _iter_headings(text):
            for m in self._ATX_HEADING_RE.finditer(text):
                yield m.start(), len(m.group(1)), m.group(2)
            for m in self._BOLD_HEADING_RE.finditer(text):
                yield m.start(), 4, m.group(1)

        for pos, level, title in sorted(_iter_headings(text), key=lambda t: t[0]):
            title = title.strip()
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
            title = re.sub(r"`([^`]+)`", r"\1", title)
            self._headings.append((pos, level, title))
            indent = "    " * (level - 2)
            item = QListWidgetItem(f"{indent}{title}")
            item.setData(Qt.ItemDataRole.UserRole, pos)
            item.setToolTip(title)
            self.addItem(item)
        self._updating = False

    def highlight_for_position(self, pos: int) -> None:
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
    """Редактор с номерами строк и подсветкой Markdown."""

    def __init__(self):
        super().__init__()
        self.number_bar = NumberBar(self)
        self.highlighter = MarkdownHighlighter(self)

        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.blockCountChanged.connect(self._update_number_bar_width)
        self.updateRequest.connect(self._update_number_bar)
        self._update_number_bar_width()

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

    def keyPressEvent(self, event):
        mods = event.modifiers()
        key = event.key()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if ctrl and not shift and key == Qt.Key.Key_B:
            self.format_line("bold")
            return
        if ctrl and not shift and key == Qt.Key.Key_I:
            self.format_line("italic")
            return
        if ctrl and shift and key == Qt.Key.Key_B:
            self.format_line("bold_italic")
            return
        if ctrl and key in (
            Qt.Key.Key_1,
            Qt.Key.Key_2,
            Qt.Key.Key_3,
            Qt.Key.Key_4,
            Qt.Key.Key_5,
        ):
            level = {
                Qt.Key.Key_1: 1,
                Qt.Key.Key_2: 2,
                Qt.Key.Key_3: 3,
                Qt.Key.Key_4: 4,
                Qt.Key.Key_5: 5,
            }[key]
            self.format_line(f"h{level}")
            return

        super().keyPressEvent(event)

    def format_line(self, kind: str) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return

        text = block.text()
        start = block.position()

        if kind.startswith("h") and kind[1:].isdigit():
            level = int(kind[1])
            level = max(1, min(6, level))
            body = re.sub(r"^#{1,6}\s*", "", text)
            new_text = (
                f"{'#' * level} {body.lstrip()}" if body.strip() else f"{'#' * level} "
            )
            cursor.beginEditBlock()
            cursor.setPosition(start)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock,
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.insertText(new_text)
            cursor.endEditBlock()
            return

        markers = {
            "bold_italic": "***",
            "bold": "**",
            "italic": "*",
        }
        if kind not in markers:
            return
        m = markers[kind]

        core = text
        core = re.sub(r"^(\*{1,3})", "", core)
        core = re.sub(r"(\*{1,3})$", "", core)
        stripped = text.strip()
        if (
            stripped.startswith(m)
            and stripped.endswith(m)
            and len(stripped) >= 2 * len(m)
        ):
            new_text = core
        else:
            new_text = f"{m}{core}{m}"

        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.insertText(new_text)
        cursor.endEditBlock()

    def highlight_search_matches(self, ranges, current=None, select_all=False):
        if not select_all:
            self.setExtraSelections([])
            return

        all_fmt = QTextCharFormat()
        all_fmt.setBackground(QColor("#fff59d"))

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#ff9800"))
        cur_fmt.setForeground(QColor("#000000"))

        cur_span = (current[0], current[1]) if current is not None else None

        extras = []
        for start, end in ranges:
            if start >= end:
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format = cur_fmt if cur_span == (start, end) else all_fmt
            cursor = self.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            extras.append(sel)

        self.setExtraSelections(extras)

    def clear_search_highlight(self) -> None:
        self.setExtraSelections([])


class FindReplaceDialog(QDialog):
    """Поиск и замена: regex, учёт регистра."""

    HISTORY_LIMIT = 20
    _HISTORY_KEY_FIND = "find_replace/history_find"
    _HISTORY_KEY_REPL = "find_replace/history_replace"

    def __init__(self, parent: "MarkdownEditor"):
        super().__init__(parent)
        self.main = parent
        self.setMinimumWidth(480)
        self._replace_mode = True

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.find_edit = self._setup_combo("Что искать…")
        self.find_edit.lineEdit().returnPressed.connect(self.find_next)
        self.find_edit.lineEdit().setPlaceholderText("Что искать…")
        form.addRow("Найти:", self.find_edit)

        self.replace_label = QLabel("Заменить на:")
        self.replace_edit = self._setup_combo("На что заменить…")
        self.replace_edit.lineEdit().returnPressed.connect(self.replace_one)
        self.replace_edit.lineEdit().setPlaceholderText("На что заменить…")
        form.addRow(self.replace_label, self.replace_edit)
        layout.addLayout(form)

        opts = QHBoxLayout()
        self.case_cb = QCheckBox("Учитывать регистр")
        self.case_cb.setToolTip("Если включено — Hello и hello считаются разными")
        self.regex_cb = QCheckBox("Регулярное выражение")
        self.regex_cb.setToolTip(
            "Синтаксис Python re; в замене доступны \\1, \\g<name>"
        )
        self.select_all_cb = QCheckBox("Выделить все")
        self.select_all_cb.setToolTip("Подсвечивать все найденные совпадения")
        opts.addWidget(self.case_cb)
        opts.addWidget(self.regex_cb)
        opts.addWidget(self.select_all_cb)
        opts.addStretch()
        layout.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_prev = QPushButton("Найти ранее")
        self.btn_prev.clicked.connect(self.find_prev)
        self.btn_next = QPushButton("Найти далее")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self.find_next)
        self.btn_repl = QPushButton("Заменить")
        self.btn_repl.clicked.connect(self.replace_one)
        self.btn_all = QPushButton("Заменить все")
        self.btn_all.clicked.connect(self.replace_all)
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_next)
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
        self._load_history()

    def _on_select_all_toggled(self, checked: bool) -> None:
        pat = self._pattern()
        if pat is None:
            self.main.editor.clear_search_highlight()
            return
        cur_span = self._current_cursor_span()
        self._highlight_all(pat, current_span=cur_span)

    def _current_cursor_span(self):
        cur = self.main.editor.textCursor()
        if cur.hasSelection():
            return (cur.selectionStart(), cur.selectionEnd())
        return None

    def _setup_combo(self, placeholder: str) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        cb.setMaxVisibleItems(12)
        cb.lineEdit().setPlaceholderText(placeholder)
        cb.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        cb.setMinimumContentsLength(24)
        cb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        cb.customContextMenuRequested.connect(
            lambda pos, c=cb: self._history_context_menu(c, pos)
        )
        return cb

    def _history_context_menu(self, combo: QComboBox, pos) -> None:
        menu = QMenu(self)
        act_clear = menu.addAction("Очистить историю")
        act_clear.setEnabled(combo.count() > 0)
        chosen = menu.exec(combo.mapToGlobal(pos))
        if chosen is act_clear:
            combo.clear()
            self._save_history()

    def _combo_text(self, combo: QComboBox) -> str:
        return combo.currentText()

    def _remember(self, combo: QComboBox, value: str) -> None:
        if not value:
            return
        for i in range(combo.count() - 1, -1, -1):
            if combo.itemText(i) == value:
                combo.removeItem(i)
        combo.insertItem(0, value)
        while combo.count() > self.HISTORY_LIMIT:
            combo.removeItem(combo.count() - 1)
        combo.setCurrentIndex(0)
        self._save_history()

    def _load_history(self) -> None:
        s = QSettings()
        for combo, key in (
            (self.find_edit, self._HISTORY_KEY_FIND),
            (self.replace_edit, self._HISTORY_KEY_REPL),
        ):
            items = s.value(key, [], type=list) or []
            combo.blockSignals(True)
            combo.clear()
            for it in items[: self.HISTORY_LIMIT]:
                combo.addItem(str(it))
            combo.setCurrentIndex(-1)
            combo.setEditText("")
            combo.blockSignals(False)

    def _save_history(self) -> None:
        s = QSettings()
        s.setValue(
            self._HISTORY_KEY_FIND,
            [self.find_edit.itemText(i) for i in range(self.find_edit.count())],
        )
        s.setValue(
            self._HISTORY_KEY_REPL,
            [self.replace_edit.itemText(i) for i in range(self.replace_edit.count())],
        )

    def set_replace_mode(self, enabled: bool) -> None:
        self._replace_mode = enabled
        self.setWindowTitle("Заменить" if enabled else "Найти")
        self.replace_label.setVisible(enabled)
        self.replace_edit.setVisible(enabled)
        self.btn_repl.setVisible(enabled)
        self.btn_all.setVisible(enabled)
        self.adjustSize()

    def _pattern(self):
        raw = self._combo_text(self.find_edit)
        if not raw:
            self.status.setText("Введите строку поиска")
            self.status.setStyleSheet("color: #c5221f;")
            return None

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

    def _select_match(self, start: int, end: int) -> None:
        editor = self.main.editor
        cursor = editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.centerCursor()
        editor.setFocus()

    def _highlight_all(self, pat, current_span=None) -> None:
        if pat is None:
            return
        select_all = self.select_all_cb.isChecked()
        editor = self.main.editor
        text = editor.toPlainText()
        ranges = [(m.start(), m.end()) for m in pat.finditer(text)]
        editor.highlight_search_matches(
            ranges, current=current_span, select_all=select_all
        )

    def _clear_all_search_highlights(self) -> None:
        self.main.editor.clear_search_highlight()

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

        editor = self.main.editor
        text = editor.toPlainText()
        cursor = editor.textCursor()

        if forward:
            start_at = (
                cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
            )
            m = pat.search(text, start_at)
            if m is None and start_at > 0:
                m = pat.search(text, 0)
        else:
            start_at = (
                cursor.selectionStart() if cursor.hasSelection() else cursor.position()
            )
            matches = list(pat.finditer(text))
            m = None
            for cand in reversed(matches):
                if cand.start() < start_at:
                    m = cand
                    break
            if m is None and matches:
                m = matches[-1]

        if m is not None:
            self._remember(self.find_edit, self._combo_text(self.find_edit))
            self._select_match(m.start(), m.end())
            self._highlight_all(pat, current_span=(m.start(), m.end()))
            total = len(list(pat.finditer(text)))
            case_info = (
                "с учётом регистра"
                if self.case_cb.isChecked()
                else "без учёта регистра"
            )
            self.status.setText(f"Найдено «{m.group()}» — всего: {total} ({case_info})")
            self.status.setStyleSheet("color: #188038;")
            return

        self._highlight_all(pat)
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
        editor = self.main.editor
        cursor = editor.textCursor()
        if not cursor.hasSelection():
            self.find_next()
            return
        selected = cursor.selectedText().replace(chr(0x2029), chr(10))
        m = pat.fullmatch(selected)
        if m is None:
            self.find_next()
            return
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
        self._remember(self.find_edit, self._combo_text(self.find_edit))
        self.status.setText("Заменено 1")
        self.status.setStyleSheet("color: #188038;")
        self.find_next()

    def replace_all(self) -> None:
        if not self._replace_mode:
            return
        pat = self._pattern()
        if pat is None:
            return
        repl = self.replace_edit.text()
        editor = self.main.editor
        text = editor.toPlainText()
        try:
            new_text, n = pat.subn(repl, text)
        except re.error as e:
            self.status.setText(f"Ошибка замены: {e}")
            self.status.setStyleSheet("color: #c5221f;")
            return
        if n:
            pos = editor.textCursor().position()
            editor.setPlainText(new_text)
            c = editor.textCursor()
            c.setPosition(min(pos, len(new_text)))
            editor.setTextCursor(c)
            self._remember(self.find_edit, self._combo_text(self.find_edit))
            self.status.setText(f"Заменено: {n}")
            self.status.setStyleSheet("color: #188038;")
            pat2 = self._pattern()
            if pat2 is not None:
                self._highlight_all(pat2)
        else:
            self.status.setText("Нечего заменять")
            self.status.setStyleSheet("color: #c5221f;")


class MarkdownEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Markdown Editor")
        self.resize(1200, 800)

        self._file_path: Path | None = None
        self._modified = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        self.editor = CodeEditor()
        self.outline = HeadingOutline()

        root.addWidget(self.editor, 1)
        root.addWidget(self.outline, 0)

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_active_heading)
        self.outline.heading_activated.connect(self._goto_heading)

        self.setStatusBar(QStatusBar())
        self._create_menu()
        self.statusBar().showMessage("Готов")

    def _create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&Файл")

        open_action = QAction("Открыть…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        save_action = QAction("Сохранить", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Сохранить как…", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._save_file_as)
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
        replace_action.setShortcut(QKeySequence("Ctrl+R"))
        replace_action.triggered.connect(self._show_find_replace)
        edit_menu.addAction(replace_action)

        if _HAS_CORRECTOR:
            edit_menu.addSeparator()
            correct_action = QAction("Исправить текст…", self)
            correct_action.setShortcut(QKeySequence("Ctrl+Alt+L"))
            correct_action.triggered.connect(self._correct_text)
            edit_menu.addAction(correct_action)

    def _correct_text(self) -> None:
        if not _HAS_CORRECTOR:
            return
        reply = QMessageBox.question(
            self,
            "Исправить текст",
            "Применить правила корректировки ко всему тексту?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        text = self.editor.toPlainText()
        path = self._file_path or Path("document.md")
        new_text = normalize_text(text, path)

        pos = self.editor.textCursor().position()
        self.editor.setPlainText(new_text)
        c = self.editor.textCursor()
        c.setPosition(min(pos, max(0, len(new_text) - 1)))
        self.editor.setTextCursor(c)

        self._modified = True
        self._update_title()
        self.outline.rebuild(new_text)
        self._update_active_heading()
        self.statusBar().showMessage("Текст исправлен", 4000)

    def _show_find(self) -> None:
        self._open_find_dialog(replace_mode=False)

    def _show_find_replace(self) -> None:
        self._open_find_dialog(replace_mode=True)

    def _open_find_dialog(self, replace_mode: bool = True) -> None:
        if not hasattr(self, "_find_dlg") or self._find_dlg is None:
            self._find_dlg = FindReplaceDialog(self)
        dlg = self._find_dlg

        if self.editor.textCursor().hasSelection():
            sel = self.editor.textCursor().selectedText().replace(chr(0x2029), chr(10))
            if sel and chr(10) not in sel:
                dlg.find_edit.setText(sel)

        dlg.set_replace_mode(replace_mode)
        dlg.find_edit.setFocus()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _export(self, fmt: str) -> None:

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Экспорт в {fmt.upper()}")
        layout = QVBoxLayout(dlg)

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

        should_open = open_after.isChecked()

        if fmt == "pdf":
            filt = "PDF (*.pdf)"
            default_ext = ".pdf"
        else:
            filt = "EPUB (*.epub)"
            default_ext = ".epub"

        default_name = "document"
        if self._file_path:
            default_name = self._file_path.stem

        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Сохранить {fmt.upper()}",
            f"{default_name}{default_ext}",
            filt,
        )
        if not path:
            return
        if not path.lower().endswith(default_ext):
            path += default_ext

        text = self.editor.toPlainText()
        title = default_name

        try:
            # export_file ожидает два текста; для одного файла дублируем
            # или передаём пустую строку — зависит от реализации export_parallel.
            # Здесь передаём один и тот же текст дважды как fallback.
            export_file(path, text, "", layout="single", title=title)
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

    def _on_text_changed(self) -> None:
        self._modified = True
        name = self._file_path.name if self._file_path else "Новый документ"
        self.outline.rebuild(self.editor.toPlainText())
        self._update_active_heading()
        self._update_title()

    def _update_active_heading(self) -> None:
        pos = self.editor.textCursor().position()
        self.outline.highlight_for_position(pos)

    def _goto_heading(self, pos: int) -> None:
        cursor = self.editor.textCursor()
        cursor.setPosition(pos)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()
        self.editor.setFocus()

    def _open_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Открыть Markdown",
            "",
            "Markdown (*.md);;All (*)",
        )
        if not file_path:
            return

        self._file_path = Path(file_path)
        self._load_file()

    def _load_file(self) -> None:
        if self._file_path and self._file_path.exists():
            self.editor.setPlainText(self._file_path.read_text(encoding="utf-8"))
            self._modified = False
            self.outline.rebuild(self.editor.toPlainText())
            self._update_active_heading()
            self._update_title()
            self.statusBar().showMessage(f"Открыт: {self._file_path.name}", 2000)
        else:
            self.editor.setPlainText("")
            self._modified = False
            self.outline.rebuild("")
            self._update_title()

    def _save_file(self) -> None:
        if not self._file_path:
            self._save_file_as()
            return

        try:
            self._file_path.write_text(self.editor.toPlainText(), encoding="utf-8")
            self._modified = False
            self._update_title()
            self.statusBar().showMessage(f"Сохранено: {self._file_path.name}", 2000)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _save_file_as(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить как", "", "Markdown (*.md)"
        )
        if not file_path:
            return
        if not file_path.endswith(".md"):
            file_path += ".md"
        self._file_path = Path(file_path)
        self._save_file()

    def _update_title(self) -> None:
        if self._file_path:
            title = f"{self._file_path.name} — Markdown Editor"
        else:
            title = "Markdown Editor"
        if self._modified:
            title = "• " + title
        self.setWindowTitle(title)

    def closeEvent(self, event) -> None:
        if self._modified:
            reply = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить перед выходом?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._save_file()
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
    app.setOrganizationName("MarkdownEditor")
    app.setApplicationName("MarkdownEditor")
    window = MarkdownEditor()
    window.show()

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        window._file_path = Path(file_path)
        window._load_file()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
