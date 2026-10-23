#!/usr/bin/env python3
"""
Markdown Editor
Простой редактор Markdown-файлов с подсветкой синтаксиса,
номерами строк, боковым оглавлением, поиском/заменой и экспортом.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPainter,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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
    QSizePolicy,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from collapsible_widget import CollapsibleWidget
from export_parallel import export_file
from text_corrector import normalize_text


class NumberBar(QWidget):
    """Панель номеров строк."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor
        self._theme = "light"
        self._apply_theme()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._apply_theme()

    def _apply_theme(self) -> None:
        if self._theme == "dark":
            self.setStyleSheet("background-color: #252526;")
        else:
            self.setStyleSheet("background-color: #f0f0f0;")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._theme == "dark":
            bg = QColor("#252526")
            fg = QColor("#858585")
        else:
            bg = QColor("#f0f0f0")
            fg = QColor("#888888")
        painter.fillRect(event.rect(), bg)
        painter.setFont(self.editor.font())
        painter.setPen(fg)

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
        self.code_fmt.setFontFamily("Consolas")

        self.link_fmt = QTextCharFormat()
        self.link_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)

        self.active_fmt = QTextCharFormat()

        self.rules = [
            (re.compile(r"^#{1,6}\s.+$"), "heading"),
            (re.compile(r"\*\*\*(.+?)\*\*\*"), "bold_italic"),
            (re.compile(r"\*\*(.+?)\*\*"), "bold"),
            (re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)"), "italic"),
            (re.compile(r"`([^`]+)`"), "code"),
            (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), "link"),
        ]
        self._theme = "light"
        self._apply_theme_colors()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._apply_theme_colors()
        self.rehighlight()

    def _apply_theme_colors(self) -> None:
        if self._theme == "dark":
            heading_color = QColor("#569cd6")
            code_color = QColor("#ce9178")
            link_color = QColor("#4ec9b0")
            active_bg = QColor("#3a3d41")
        else:
            heading_color = QColor("#0066cc")
            code_color = QColor("#cc0000")
            link_color = QColor("#0066cc")
            active_bg = QColor("#fffacd")

        for level in self.heading_fmts:
            self.heading_fmts[level].setForeground(heading_color)

        self.code_fmt.setForeground(code_color)
        self.link_fmt.setForeground(link_color)
        self.active_fmt.setBackground(active_bg)

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
        self._theme = "light"
        self._apply_theme()
        self.itemClicked.connect(self._on_item_clicked)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._apply_theme()

    def _apply_theme(self) -> None:
        if self._theme == "dark":
            self.setStyleSheet(
                """
                QListWidget {
                    background: #252526;
                    border: 1px solid #3c3c3c;
                    font-size: 12px;
                    outline: none;
                }
                QListWidget::item {
                    padding: 3px 6px;
                    color: #cccccc;
                }
                QListWidget::item:selected {
                    background: #094771;
                    color: white;
                }
                """
            )
        else:
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

    def rebuild(self, text: str) -> None:
        """Пересобрать список заголовков из текста документа."""
        # Собрать новые заголовки
        new_headings: list[tuple[int, int, str]] = []

        def _iter_headings(text):
            for m in self._ATX_HEADING_RE.finditer(text):
                yield m.start(), len(m.group(1)), m.group(2)
            for m in self._BOLD_HEADING_RE.finditer(text):
                yield m.start(), 4, m.group(1)

        for pos, level, title in sorted(_iter_headings(text), key=lambda t: t[0]):
            title = title.strip()
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
            title = re.sub(r"`([^`]+)`", r"\1", title)
            new_headings.append((pos, level, title))

        # Сравнить с предыдущими заголовками
        if new_headings == self._headings:
            # Заголовки не изменились — не пересобирать
            return

        # Запомнить текущий выбранный элемент (по позиции в документе)
        selected_pos = None
        current_item = self.currentItem()
        if current_item is not None:
            selected_pos = current_item.data(Qt.ItemDataRole.UserRole)

        # Пересобрать список
        self._updating = True
        self.clear()
        self._headings = new_headings

        for pos, level, title in self._headings:
            indent = "    " * (level - 2)
            item = QListWidgetItem(f"{indent}{title}")
            item.setData(Qt.ItemDataRole.UserRole, pos)
            item.setToolTip(title)
            self.addItem(item)

        # Восстановить выбор
        if selected_pos is not None:
            # Найти заголовок с ближайшей позицией
            best_idx = -1
            best_diff = float("inf")
            for i, (hpos, _level, _title) in enumerate(self._headings):
                diff = abs(hpos - selected_pos)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i

            if best_idx >= 0:
                self.setCurrentRow(best_idx)
                item = self.item(best_idx)
                if item is not None:
                    # Центрировать элемент
                    self.scrollToItem(
                        item, QAbstractItemView.ScrollHint.PositionAtCenter
                    )

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

        # Цвета подсветки поиска (зависят от темы)
        self._search_all_color = QColor("#fff59d")
        self._search_current_bg = QColor("#ff9800")
        self._search_current_fg = QColor("#000000")

        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.blockCountChanged.connect(self._update_number_bar_width)
        self.updateRequest.connect(self._update_number_bar)
        self._update_number_bar_width()

    def set_theme(self, theme: str) -> None:
        self.number_bar.set_theme(theme)
        self.highlighter.set_theme(theme)

        # Цвета подсветки поиска для разных тем
        if theme == "dark":
            self._search_all_color = QColor("#554b00")  # тёмно-жёлтый
            self._search_current_bg = QColor("#d97706")  # оранжевый
            self._search_current_fg = QColor("#ffffff")  # белый текст
        else:
            self._search_all_color = QColor("#fff59d")  # светло-жёлтый
            self._search_current_bg = QColor("#ff9800")  # оранжевый
            self._search_current_fg = QColor("#000000")  # чёрный текст

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
        all_fmt.setBackground(self._search_all_color)

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(self._search_current_bg)
        cur_fmt.setForeground(self._search_current_fg)

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
        repl = self.replace_edit.currentText()
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
        repl = self.replace_edit.currentText()
        editor = self.main.editor
        text = editor.toPlainText()

        # Собираем все совпадения заранее
        matches = list(pat.finditer(text))
        if not matches:
            self.status.setText("Нечего заменять")
            self.status.setStyleSheet("color: #c5221f;")
            return

        # Запоминаем позицию курсора до замены
        old_pos = editor.textCursor().position()

        # Начинаем единый undo-блок
        cursor = editor.textCursor()
        cursor.beginEditBlock()

        replaced_count = 0
        try:
            # Заменяем с конца к началу, чтобы позиции не сдвигались
            for m in reversed(matches):
                try:
                    if self.regex_cb.isChecked():
                        new = pat.sub(repl, m.group(), count=1)
                    else:
                        new = repl
                except re.error as e:
                    self.status.setText(f"Ошибка замены: {e}")
                    self.status.setStyleSheet("color: #c5221f;")
                    cursor.endEditBlock()
                    return

                cursor.setPosition(m.start())
                cursor.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(new)
                replaced_count += 1
        except Exception as e:
            cursor.endEditBlock()
            self.status.setText(f"Ошибка замены: {e}")
            self.status.setStyleSheet("color: #c5221f;")
            return

        cursor.endEditBlock()

        # Восстанавливаем позицию курсора (с защитой от выхода за границы)
        new_text = editor.toPlainText()
        restore_pos = min(old_pos, len(new_text))
        c = editor.textCursor()
        c.setPosition(restore_pos)
        editor.setTextCursor(c)

        self._remember(self.find_edit, self._combo_text(self.find_edit))
        self.status.setText(f"Заменено: {replaced_count}")
        self.status.setStyleSheet("color: #188038;")

        # Обновляем подсветку оставшихся вхождений
        pat2 = self._pattern()
        if pat2 is not None:
            self._highlight_all(pat2)


DEFAULT_BNF = {
    "title": "",
    "orig_name": "",
    "author": "",
    "lang": "ru",
    "tags": [],
    "favorite": False,
    "description": "",
}

TAG_HISTORY_PATH = Path.home() / ".bnf_tag_history.json"


def _load_tag_history() -> list[str]:
    """Загрузить историю тегов из ~/.bnf_tag_history.json."""
    try:
        if TAG_HISTORY_PATH.exists():
            data = json.loads(TAG_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(t).strip() for t in data if str(t).strip()]
    except Exception:
        pass
    return []


def _save_tag_history(tags: list[str]) -> None:
    """Сохранить уникальные теги в историю (сортировка по алфавиту)."""
    existing = set(_load_tag_history())
    for t in tags:
        t = str(t).strip()
        if t:
            existing.add(t)
    try:
        TAG_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        sorted_tags = sorted(existing, key=lambda s: s.casefold())
        TAG_HISTORY_PATH.write_text(
            json.dumps(sorted_tags, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _parse_filename_title_author(base_name: str) -> tuple[str, str]:
    """Из имени файла вида «Название [Автор]» извлечь title и author.

    base_name — stem без .en.md / .ru.md / .bnf.
    """
    name = base_name.strip()
    for suf in (".en.md", ".ru.md", ".md", ".bnf"):
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
    m = re.match(r"^(.+?)\s*\[([^\]]+)\]\s*$", name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name, ""


class FlowLayout(QLayout):
    """Layout, который размещает виджеты в несколько строк с переносом."""

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def spacing(self):
        if self._spacing >= 0:
            return self._spacing
        return (
            self.parentWidget()
            and self.parentWidget()
            .style()
            .pixelMetric(self.parentWidget().style().PM_LayoutHorizontalSpacing)
            or 6
        )

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective_rect = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            item_size = item.sizeHint()

            next_x = x + item_size.width() + spacing
            if next_x - spacing > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + spacing
                next_x = x + item_size.width() + spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + m.bottom()


class TagChip(QWidget):
    """Виджет-тег в виде 'пилюли'. Кликабельный, меняет цвет."""

    clicked = Signal(str, bool)  # tag_name, is_selected

    def __init__(self, text: str, selected: bool = False, parent=None):
        super().__init__(parent)
        self.text = text
        self.selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = False
        self._update_style()

    def _update_style(self):
        """Обновить стиль и перерисовать."""
        self.update()  # вызвать перерисовку

    def paintEvent(self, event):
        """Отрисовка чипа."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.selected:
            if self._hover:
                bg = QColor("#1557b0")
            else:
                bg = QColor("#1a73e8")
            fg = QColor("white")
        else:
            if self._hover:
                bg = QColor("#d0d0d0")
            else:
                bg = QColor("#e0e0e0")
            fg = QColor("#333")

        # рисуем скруглённый прямоугольник
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect(), 12, 12)

        # рисуем текст
        painter.setPen(fg)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected = not self.selected
            self._update_style()
            self.clicked.emit(self.text, self.selected)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._update_style()
        super().leaveEvent(event)

    def sizeHint(self):
        font_metrics = self.fontMetrics()
        text_width = font_metrics.horizontalAdvance(self.text)
        return QSize(text_width + 24, 28)


class MetadataDialog(QDialog):
    """Редактор метаданных книги (.bnf).

    schema:
      title, orig_name, author, lang (статично «ru»),
      tags (list), favorite (bool), description
    """

    def __init__(
        self,
        parent,
        data: dict | None = None,
        path: str = "",
        file_base_name: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Метаданные книги (.bnf)")
        self.setMinimumWidth(1100)
        self.setMinimumHeight(800)

        data = {**DEFAULT_BNF, **(data or {})}
        tags_raw = data.get("tags") or []
        if isinstance(tags_raw, str):
            current_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            current_tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        else:
            current_tags = []

        # предзаполнение title/author из имени файла, если поля пусты
        title = str(data.get("title") or "").strip()
        author = str(data.get("author") or "").strip()
        if file_base_name and (not title or not author):
            parsed_title, parsed_author = _parse_filename_title_author(file_base_name)
            if not title and parsed_title:
                title = parsed_title
            if not author and parsed_author:
                author = parsed_author

        layout = QVBoxLayout(self)
        if path:
            layout.addWidget(QLabel(f"Файл: {path}"))

        form = QFormLayout()

        self.title_edit = QLineEdit(title)
        form.addRow("Название:", self.title_edit)

        # self.orig_name_edit = QLineEdit(str(data.get("orig_name") or ""))
        # form.addRow("Оригинальное название:", self.orig_name_edit)

        self.author_edit = QLineEdit(author)
        form.addRow("Автор:", self.author_edit)

        # lang — статично en-ru
        self.lang_edit = QLineEdit("ru")
        self.lang_edit.setReadOnly(True)
        self.lang_edit.setStyleSheet("background: #f0f0f0; color: #555;")
        self.lang_edit.setToolTip("Пока фиксированное значение ru")
        form.addRow("Язык:", self.lang_edit)

        self.favorite_cb = QCheckBox()
        fav = data.get("favorite")
        if isinstance(fav, str):
            fav = fav.lower() in ("1", "true", "yes", "да")
        self.favorite_cb.setChecked(bool(fav))
        form.addRow("Избранное", self.favorite_cb)

        # --- теги: история + чекбоксы + добавление ---
        tags_box = QGroupBox("Теги")
        tags_layout = QVBoxLayout(tags_box)

        self.tags_container = QWidget()
        self.tags_flow = FlowLayout(self.tags_container, margin=4, spacing=4)
        self.tags_container.setLayout(self.tags_flow)
        self.tags_container.setMinimumHeight(80)
        self.tags_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        tags_layout.addWidget(self.tags_container)

        self._tag_chips: dict[str, TagChip] = {}

        history = _load_tag_history()
        all_known = sorted(
            set(history) | set(current_tags),
            key=lambda s: s.casefold(),
        )

        for tag in all_known:
            self._add_tag_chip(tag, tag in current_tags)

        add_row = QHBoxLayout()
        self.new_tag_edit = QLineEdit()
        self.new_tag_edit.setPlaceholderText("Новый тег…")
        self.new_tag_edit.returnPressed.connect(self._add_new_tag)
        btn_add_tag = QPushButton("Добавить")
        btn_add_tag.clicked.connect(self._add_new_tag)
        add_row.addWidget(self.new_tag_edit, 1)
        add_row.addWidget(btn_add_tag)
        tags_layout.addLayout(add_row)

        form.addRow(tags_box)

        self.description_edit = QTextEdit()
        self.description_edit.setPlainText(str(data.get("description") or ""))
        self.description_edit.setMaximumHeight(120)
        form.addRow("Описание:", self.description_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keyPressEvent(self, event):
        """Обработка Ctrl+S для сохранения."""
        if (
            event.modifiers() == Qt.KeyboardModifier.ControlModifier
            and event.key() == Qt.Key.Key_S
        ):
            self.accept()  # эквивалентно нажатию кнопки Save
            return
        super().keyPressEvent(event)

    def _add_tag_chip(self, tag: str, selected: bool) -> None:
        """Добавить чип тега в FlowLayout."""
        if tag in self._tag_chips:
            # уже есть — просто обновить состояние
            chip = self._tag_chips[tag]
            if chip.selected != selected:
                chip.selected = selected
                chip._update_style()
            return

        chip = TagChip(tag, selected)
        chip.clicked.connect(self._on_tag_clicked)
        self._tag_chips[tag] = chip
        self.tags_flow.addWidget(chip)

    def _on_tag_clicked(self, tag: str, selected: bool) -> None:
        """Обработчик клика по чипу."""
        pass  # состояние уже обновлено в TagChip

    def _add_new_tag(self) -> None:
        text = self.new_tag_edit.text().strip()
        if not text:
            return
        self._add_tag_chip(text, True)
        self.new_tag_edit.clear()

    def result_data(self) -> dict:
        selected_tags = [tag for tag, chip in self._tag_chips.items() if chip.selected]
        all_tags = list(self._tag_chips.keys())
        _save_tag_history(all_tags)

        return {
            "title": self.title_edit.text().strip(),
            "author": self.author_edit.text().strip(),
            "lang": "ru",
            "tags": selected_tags,
            "favorite": self.favorite_cb.isChecked(),
            "description": self.description_edit.toPlainText().strip(),
        }


class MarkdownEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Markdown Editor")
        self.resize(1200, 800)
        self._current_theme = "light"

        self._file_path: Path | None = None
        self._modified = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        self.outline = HeadingOutline()
        self.collapsible = CollapsibleWidget(
            "Оглавление",
            settings_key="outline",
            orientation="vertical",
        )
        self.collapsible.add_widget(self.outline)
        # Фиксируем ширину контейнера — как у самого Outline
        self.collapsible.setMinimumWidth(0)
        self.collapsible.setMaximumWidth(200)

        self.editor = CodeEditor()

        root.addWidget(self.collapsible, 0)
        root.addWidget(self.editor, 1)

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_active_heading)
        self.outline.heading_activated.connect(self._goto_heading)

        self._setup_status_bar()
        self._create_menu()

        # Применяем сохранённую тему
        saved_theme = QSettings().value("ui/theme", "light")
        self.apply_theme(saved_theme)

    def apply_theme(self, theme: str) -> None:
        """Применяет светлую или тёмную тему ко всему приложению."""
        app = QApplication.instance()
        if theme == "dark":
            app.setStyle("Fusion")
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
            palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(35, 35, 35))
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.Text,
                QColor(128, 128, 128),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.ButtonText,
                QColor(128, 128, 128),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.WindowText,
                QColor(128, 128, 128),
            )
            app.setPalette(palette)
            app.setStyleSheet(
                """
                QToolTip { color: #ffffff; background-color: #2b2b2b; border: 1px solid #555555; padding: 4px; }
                QMenuBar { background-color: #353535; color: #ffffff; }
                QMenuBar::item:selected { background-color: #505050; }
                QMenu { background-color: #353535; color: #ffffff; border: 1px solid #555555; }
                QMenu::item:selected { background-color: #2a82da; }
                QStatusBar { background-color: #252526; color: #cccccc; }
                """
            )
        else:
            app.setStyle("Fusion")
            app.setPalette(app.style().standardPalette())
            app.setStyleSheet("")

        # Обновляем кастомные виджеты
        self.collapsible.set_theme(theme)
        self.editor.set_theme(theme)
        self.outline.set_theme(theme)
        self._update_status_bar_theme()

        # Сохраняем выбор
        QSettings().setValue("ui/theme", theme)
        self._current_theme = theme

        # Обновляем галочки в меню
        self._update_theme_menu()

    MAX_RECENT_FILES = 10
    _RECENT_FILES_KEY = "recent_files"

    def _load_recent_files(self) -> list[str]:
        """Загрузить список недавних файлов из QSettings."""
        s = QSettings()
        files = s.value(self._RECENT_FILES_KEY, [], type=list) or []
        return [str(f) for f in files]

    def _save_recent_files(self, files: list[str]) -> None:
        """Сохранить список недавних файлов в QSettings."""
        QSettings().setValue(self._RECENT_FILES_KEY, files)

    def _add_recent_file(self, path: str) -> None:
        """Добавить файл в начало списка недавних (без дубликатов, макс. MAX_RECENT_FILES)."""
        if not path:
            return
        files = self._load_recent_files()
        # Убираем существующее вхождение
        files = [f for f in files if f != path]
        # Вставляем в начало
        files.insert(0, path)
        # Ограничиваем длину
        files = files[: self.MAX_RECENT_FILES]
        self._save_recent_files(files)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        """Перестроить подменю «Недавние файлы»."""
        if not hasattr(self, "_recent_menu"):
            return

        self._recent_menu.clear()
        files = self._load_recent_files()

        if not files:
            empty_action = self._recent_menu.addAction("(пусто)")
            empty_action.setEnabled(False)
        else:
            for path in files:
                # Показываем только имя файла, полный путь — в tooltip
                name = Path(path).name
                action = self._recent_menu.addAction(name)
                action.setToolTip(path)
                action.triggered.connect(lambda checked=False, p=path: self._open_recent_file(p))

            self._recent_menu.addSeparator()
            clear_action = self._recent_menu.addAction("Очистить список")
            clear_action.triggered.connect(self._clear_recent_files)

    def _open_recent_file(self, path: str) -> None:
        """Открыть файл из списка недавних."""
        p = Path(path)
        if not p.exists():
            reply = QMessageBox.warning(
                self,
                "Файл не найден",
                f"Файл не существует:\n{path}\n\nУдалить его из списка недавних?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                files = self._load_recent_files()
                files = [f for f in files if f != path]
                self._save_recent_files(files)
                self._rebuild_recent_menu()
            return

        self._file_path = p
        self._load_file()
        self._add_recent_file(str(p))

    def _clear_recent_files(self) -> None:
        """Очистить список недавних файлов."""
        self._save_recent_files([])
        self._rebuild_recent_menu()

    def _setup_status_bar(self) -> None:
        """Создать статус-бар с информативными виджетами."""
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        # Виджеты для статистики
        self._lbl_position = QLabel()
        self._lbl_position.setMinimumWidth(160)

        self._lbl_words = QLabel()
        self._lbl_words.setMinimumWidth(120)

        self._lbl_chars = QLabel()
        self._lbl_chars.setMinimumWidth(160)

        self._lbl_reading_time = QLabel()
        self._lbl_reading_time.setMinimumWidth(120)

        # Добавляем виджеты в статус-бар с разделителями
        status_bar.addPermanentWidget(self._lbl_position)
        status_bar.addPermanentWidget(self._make_separator())
        status_bar.addPermanentWidget(self._lbl_words)
        status_bar.addPermanentWidget(self._make_separator())
        status_bar.addPermanentWidget(self._lbl_chars)
        status_bar.addPermanentWidget(self._make_separator())
        status_bar.addPermanentWidget(self._lbl_reading_time)

        # Подключаем сигналы
        self.editor.cursorPositionChanged.connect(self._update_position_info)
        self.editor.textChanged.connect(self._update_text_stats)

        # Применяем текущую тему
        self._update_status_bar_theme()

        # Первичное обновление
        self._update_position_info()
        self._update_text_stats()

    def _make_separator(self) -> QLabel:
        """Создать вертикальный разделитель."""
        sep = QLabel("|")
        return sep

    def _update_status_bar_theme(self) -> None:
        """Обновить цвета статус-бара в зависимости от темы."""
        # Текст наследует цвет от темы приложения — ничего не задаём
        # Только разделители делаем заметными, но ненавязчивыми
        if self._current_theme == "dark":
            sep_color = "#555555"
        else:
            sep_color = "#bbbbbb"

        sep_style = f"color: {sep_color}; padding: 0 4px;"

        # Убираем фон и цвет текста — они наследуются от темы
        for label in (self._lbl_position, self._lbl_words, self._lbl_chars, self._lbl_reading_time):
            label.setStyleSheet("padding: 0 4px;")

        # Обновляем разделители
        layout = self.statusBar().layout()
        if layout:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget and isinstance(widget, QLabel) and widget.text() == "|":
                        widget.setStyleSheet(sep_style)

    def _update_position_info(self) -> None:
        """Обновить информацию о позиции курсора."""
        cursor = self.editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self._lbl_position.setText(f"Строка {line}, Столбец {col}")

    def _update_text_stats(self) -> None:
        """Обновить статистику текста (слова, символы, время чтения)."""
        text = self.editor.toPlainText()

        # Символы
        total_chars = len(text)
        chars_no_spaces = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

        # Слова — разбиваем по пробелам и переводу строк, фильтруем пустые
        words = [w for w in re.split(r"[\s\n\r\t]+", text) if w]
        word_count = len(words)

        # Время чтения (средняя скорость ~200 слов/мин)
        reading_minutes = max(1, round(word_count / 200)) if word_count > 0 else 0

        # Форматирование
        self._lbl_words.setText(f"Слов: {word_count:,}".replace(",", " "))
        self._lbl_chars.setText(f"Символов: {total_chars:,} / {chars_no_spaces:,}".replace(",", " "))

        if word_count == 0:
            self._lbl_reading_time.setText("Чтение: —")
        elif reading_minutes < 60:
            self._lbl_reading_time.setText(f"~{reading_minutes} мин чтения")
        else:
            hours = reading_minutes // 60
            mins = reading_minutes % 60
            self._lbl_reading_time.setText(f"~{hours} ч {mins} мин чтения")

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
        meta_action = QAction("Метаданные книги (.bnf)…", self)
        meta_action.setShortcut(QKeySequence("Ctrl+M"))
        meta_action.triggered.connect(self._edit_metadata)
        file_menu.addAction(meta_action)

        # --- Недавние файлы ---
        self._recent_menu = file_menu.addMenu("Недавние файлы")
        self._rebuild_recent_menu()

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

        edit_menu.addSeparator()
        correct_action = QAction("Исправить текст…", self)
        correct_action.setShortcut(QKeySequence("Ctrl+Alt+L"))
        correct_action.triggered.connect(self._correct_text)
        edit_menu.addAction(correct_action)

        # --- Меню "Вид" ---
        view_menu = menubar.addMenu("&Вид")
        theme_menu = view_menu.addMenu("Тема")

        self._theme_light_action = QAction("Светлая", self)
        self._theme_light_action.setCheckable(True)
        self._theme_light_action.triggered.connect(lambda: self.apply_theme("light"))
        theme_menu.addAction(self._theme_light_action)

        self._theme_dark_action = QAction("Тёмная", self)
        self._theme_dark_action.setCheckable(True)
        self._theme_dark_action.triggered.connect(lambda: self.apply_theme("dark"))
        theme_menu.addAction(self._theme_dark_action)

        self._theme_group = QActionGroup(self)
        self._theme_group.addAction(self._theme_light_action)
        self._theme_group.addAction(self._theme_dark_action)
        self._theme_group.setExclusive(True)

    def _update_theme_menu(self) -> None:
        """Синхронизирует галочки в меню с текущей темой."""
        self._theme_light_action.setChecked(self._current_theme == "light")
        self._theme_dark_action.setChecked(self._current_theme == "dark")

    def _correct_text(self) -> None:
        #reply = QMessageBox.question(
        #    self,
        #    "Исправить текст",
        #    "Применить правила корректировки ко всему тексту?",
        #    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        #)
        #if reply != QMessageBox.StandardButton.Yes:
        #    return

        # --- Сохраняем позицию курсора и скролла ---
        cursor_pos = self.editor.textCursor().position()
        scrollbar = self.editor.verticalScrollBar()
        old_scroll_value = scrollbar.value()
        old_scroll_max = scrollbar.maximum()
        # Запоминаем относительную позицию скролла (0.0 ... 1.0)
        scroll_ratio = old_scroll_value / old_scroll_max if old_scroll_max > 0 else 0.0

        text = self.editor.toPlainText()
        path = self._file_path or Path("document.md")
        new_text = normalize_text(text, path)

        # --- Применяем коррекцию ---
        self.editor.setPlainText(new_text)

        # --- Восстанавливаем курсор ---
        new_pos = min(cursor_pos, max(0, len(new_text)))
        c = self.editor.textCursor()
        c.setPosition(new_pos)
        self.editor.setTextCursor(c)

        # --- Восстанавливаем скролл ---
        new_scroll_max = scrollbar.maximum()
        if new_scroll_max > 0:
            # Если текст сильно изменился, используем относительную позицию
            # Иначе — пытаемся восстановить абсолютное значение
            if abs(new_scroll_max - old_scroll_max) < old_scroll_max * 0.3:
                scrollbar.setValue(min(old_scroll_value, new_scroll_max))
            else:
                scrollbar.setValue(int(scroll_ratio * new_scroll_max))

        # Центрируем курсор на экране, чтобы пользователь видел контекст
        self.editor.centerCursor()

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
                dlg.find_edit.setEditText(sel)

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
            export_file(path, text, title=title)
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
        self._add_recent_file(file_path)

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

    def _bnf_path(self) -> Path | None:
        """document.md → document.bnf"""
        if self._file_path:
            return self._file_path.with_suffix(".bnf")
        return None

    def _load_bnf(self) -> dict:
        path = self._bnf_path()
        if path is None or not path.exists():
            return dict(DEFAULT_BNF)
        try:
            return {**DEFAULT_BNF, **json.loads(path.read_text(encoding="utf-8"))}
        except Exception:
            return dict(DEFAULT_BNF)

    def _save_bnf(self, data: dict) -> bool:
        path = self._bnf_path()
        print("save to file" + str(path))
        if path is None:
            QMessageBox.warning(
                self,
                "Метаданные",
                "Сначала откройте или сохраните пару .en.md / .ru.md.",
            )
            return False
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.statusBar().showMessage(f"Метаданные: {path}", 4000)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить .bnf:\n{e}")
            return False

    def _edit_metadata(self) -> None:
        path = self._bnf_path()
        data = self._load_bnf()
        # имя файла для предзаполнения title/author: «Название [Автор]»
        file_base_name = ""
        if self._file_path:
            file_base_name = Path(str(self._file_path)).name
        elif self._en_path:
            name = self._en_path.name
            if name.endswith(".en.md"):
                file_base_name = name[:-6]
            else:
                file_base_name = self._en_path.stem
        self.dlg = MetadataDialog(
            self,
            data=data,
            path=str(path) if path else "",
            file_base_name=file_base_name,
        )
        self.dlg.finished.connect(self._on_dialog_finished)
        self.dlg.show()

    def _on_dialog_finished(self, result: int):
        if result != QDialog.DialogCode.Accepted:
            return
        self._save_bnf(self.dlg.result_data())

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
        window._add_recent_file(file_path)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
