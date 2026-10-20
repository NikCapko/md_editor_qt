#!/usr/bin/env python3
"""
Parallel Markdown Editor
Редактор для параллельного перевода с синхронизацией по абзацам.
Файлы: .en.md (оригинал, слева) и .ru.md (перевод, справа)
"""

import os
import re
import sys

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QPlainTextEdit,
    QWidget,
)


class NumberBar(QWidget):
    """Панель номеров строк слева от редактора."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setStyleSheet("background-color: #f0f0f0;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        painter.setPen(QColor("#888"))
        painter.setFont(self.editor.font())

        block = self.editor.firstVisibleBlock()
        offset = self.editor.contentOffset()
        block_rect = self.editor.blockBoundingGeometry(block).translated(offset)
        top = int(block_rect.top())
        bottom = int(block_rect.bottom())
        block_number = block.blockNumber() + 1
        line_height = self.editor.fontMetrics().height()

        while block.isValid() and top <= bottom:
            if block.isVisible():
                painter.drawText(
                    0,
                    top,
                    self.width() - 5,
                    line_height,
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(block_number),
                )
            block = block.next()
            if block.isValid():
                block_rect = self.editor.blockBoundingGeometry(block).translated(offset)
                top = int(block_rect.top())
            block_number += 1


class MarkdownHighlighter(QSyntaxHighlighter):
    """Подсветка Markdown + активной строки."""

    def __init__(self, editor):
        super().__init__(editor.document())
        self.editor = editor
        self.prev_block_number = -1

        # Форматы markdown
        self.heading_fmt = QTextCharFormat()
        self.heading_fmt.setForeground(QColor("#0066cc"))
        self.heading_fmt.setFontWeight(QFont.Bold)

        self.bold_fmt = QTextCharFormat()
        self.bold_fmt.setFontWeight(QFont.Bold)

        self.italic_fmt = QTextCharFormat()
        self.italic_fmt.setFontItalic(True)

        self.code_fmt = QTextCharFormat()
        self.code_fmt.setForeground(QColor("#cc0000"))
        self.code_fmt.setFontFamily("Consolas")

        self.link_fmt = QTextCharFormat()
        self.link_fmt.setForeground(QColor("#0066cc"))
        self.link_fmt.setUnderlineStyle(QTextCharFormat.SingleUnderline)

        # Формат активной строки
        self.active_fmt = QTextCharFormat()
        self.active_fmt.setBackground(QColor("#fffacd"))

        # Regex правила
        self.rules = [
            (re.compile(r"^#{1,6}\s.+$"), "heading"),
            (re.compile(r"\*\*(.+?)\*\*"), "bold"),
            (re.compile(r"(?<!\*)\*([^\*]+?)\*(?!\*)"), "italic"),
            (re.compile(r"`([^`]+)`"), "code"),
            (re.compile(r"\[([^\]]+)\]\(([^\)]+)\)"), "link"),
        ]

    def highlightBlock(self, text):
        is_active = (
            self.currentBlock().blockNumber()
            == self.editor.textCursor().block().blockNumber()
        )

        n = len(text)
        formats = [None] * n

        # Применяем markdown правила
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

        # Применяем фон активной строки
        if is_active:
            for i in range(n):
                if formats[i] is None:
                    formats[i] = self.active_fmt
                else:
                    combined = QTextCharFormat(formats[i])
                    combined.setBackground(self.active_fmt.background())
                    formats[i] = combined

        # Применяем форматы
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


class CodeEditor(QPlainTextEdit):
    """Редактор с номерами строк, подсветкой и синхронизацией."""

    paragraph_clicked = Signal(int, int)  # editor_idx, cursor_position

    def __init__(self, editor_idx=0):
        super().__init__()
        self.editor_idx = editor_idx
        self._syncing = False
        self._mouse_click = False

        self.number_bar = NumberBar(self)
        self.highlighter = MarkdownHighlighter(self)

        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)

        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.blockCountChanged.connect(self._update_number_bar_width)
        self.updateRequest.connect(self._update_number_bar)

        self._update_number_bar_width()

    def mousePressEvent(self, event):
        """Отслеживаем клик мышью для синхронизации."""
        self._mouse_click = True
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.number_bar.setGeometry(QRect(cr.left(), cr.top(),
                                          self.number_bar.width(), cr.height()))

    def _update_number_bar_width(self):
        digits = len(str(max(1, self.blockCount())))
        width = self.fontMetrics().horizontalAdvance('9') * (digits + 1) + 10
        self.number_bar.setFixedWidth(width)
        # Устанавливаем margins, чтобы текст не залезал под номера
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
        """Перерисовываем подсветку активной строки."""
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

        # Синхронизация только при клике мышью
        if self._mouse_click:
            self._mouse_click = False
            if not self._syncing:
                self._select_paragraph()
                pos = self.textCursor().position()
                self.paragraph_clicked.emit(self.editor_idx, pos)

    def _select_paragraph(self):
        """Выделяет весь абзац целиком."""
        cursor = self.textCursor()
        pos = cursor.position()
        block = self.document().findBlock(pos)
        if not block.isValid():
            return

        # Находим начало абзаца
        start_block = block
        while (
            start_block.previous().isValid() and start_block.previous().text().strip()
        ):
            start_block = start_block.previous()

        # Находим конец абзаца
        end_block = block
        while end_block.next().isValid() and end_block.next().text().strip():
            end_block = end_block.next()

        # Выделяем диапазон
        cursor.setPosition(start_block.position())
        cursor.setPosition(
            end_block.position() + len(end_block.text()), QTextCursor.KeepAnchor
        )
        self.setTextCursor(cursor)

    def get_paragraph_index(self, pos):
        """Возвращает индекс абзаца по позиции курсора."""
        block = self.document().findBlock(pos)
        if not block.isValid():
            return -1

        start_block = block
        while (
            start_block.previous().isValid() and start_block.previous().text().strip()
        ):
            start_block = start_block.previous()

        idx = 0
        b = self.document().begin()
        in_para = False
        while b.isValid():
            text = b.text()
            if text.strip():
                if not in_para:
                    in_para = True
                    if b == start_block:
                        return idx
                    idx += 1
            else:
                in_para = False
            b = b.next()
        return -1

    def get_paragraph_position(self, idx):
        """Возвращает позицию начала абзаца с индексом idx."""
        count = 0
        b = self.document().begin()
        in_para = False
        while b.isValid():
            text = b.text()
            if text.strip():
                if not in_para:
                    in_para = True
                    if count == idx:
                        return b.position()
                    count += 1
            else:
                in_para = False
            b = b.next()
        return None


class ParallelEditor(QMainWindow):
    """Главное окно с двумя редакторами."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parallel Markdown Editor")
        self.resize(1400, 800)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.left_editor = CodeEditor(0)
        self.right_editor = CodeEditor(1)

        layout.addWidget(self.left_editor)
        layout.addWidget(self.right_editor)

        self.editors = [self.left_editor, self.right_editor]

        # Синхронизация
        self.left_editor.paragraph_clicked.connect(self._sync_paragraph)
        self.right_editor.paragraph_clicked.connect(self._sync_paragraph)

        self._create_menu()
        self.current_file_base = None

    def _create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        open_action = QAction("Open...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_files)
        file_menu.addAction(open_action)

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self._save_files)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _open_files(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open .en.md file",
            "",
            "Markdown Files (*.en.md *.ru.md);;All Files (*)",
        )
        if not file_path:
            return

        if file_path.endswith(".en.md"):
            base = file_path[:-6]
        elif file_path.endswith(".ru.md"):
            base = file_path[:-6]
        else:
            base = file_path

        self.current_file_base = base
        self._load_files()

    def _load_files(self):
        en_path = self.current_file_base + ".en.md"
        ru_path = self.current_file_base + ".ru.md"

        self.left_editor._syncing = True
        self.right_editor._syncing = True

        if os.path.exists(en_path):
            with open(en_path, "r", encoding="utf-8") as f:
                self.left_editor.setPlainText(f.read())
        else:
            self.left_editor.setPlainText("")

        if os.path.exists(ru_path):
            with open(ru_path, "r", encoding="utf-8") as f:
                self.right_editor.setPlainText(f.read())
        else:
            self.right_editor.setPlainText("")

        self.left_editor._syncing = False
        self.right_editor._syncing = False

    def _save_files(self):
        if not self.current_file_base:
            self._save_files_as()
            return

        en_path = self.current_file_base + ".en.md"
        ru_path = self.current_file_base + ".ru.md"

        with open(en_path, "w", encoding="utf-8") as f:
            f.write(self.left_editor.toPlainText())
        with open(ru_path, "w", encoding="utf-8") as f:
            f.write(self.right_editor.toPlainText())

    def _save_files_as(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save as .en.md", "", "Markdown Files (*.en.md)"
        )
        if not file_path:
            return
        if file_path.endswith(".en.md"):
            base = file_path[:-6]
        else:
            base = file_path
        self.current_file_base = base
        self._save_files()

    def _sync_paragraph(self, editor_idx, pos):
        """Синхронизирует скролл так, чтобы абзацы были на одном уровне окна."""
        source = self.editors[editor_idx]
        target = self.editors[1 - editor_idx]

        para_idx = source.get_paragraph_index(pos)
        if para_idx < 0:
            return

        target_pos = target.get_paragraph_position(para_idx)
        if target_pos is None:
            return

        # Получаем Y-позицию курсора в source (в пикселях viewport)
        source_cursor = source.textCursor()
        source_cursor.setPosition(source.get_paragraph_position(para_idx))
        source_rect = source.cursorRect(source_cursor)
        source_y = source_rect.top()

        # Устанавливаем курсор в target на начало абзаца
        target._syncing = True
        target_cursor = QTextCursor(target.document())
        target_cursor.setPosition(target_pos)
        target.setTextCursor(target_cursor)

        # Получаем Y-позицию курсора в target (в пикселях viewport)
        target_rect = target.cursorRect(target_cursor)
        target_y = target_rect.top()

        # Вычисляем разницу и прокручиваем
        diff = source_y - target_y
        if abs(diff) > 5:
            line_height = target_rect.height() if target_rect.height() > 0 else 16
            scroll_pixels = diff
            scrollbar = target.verticalScrollBar()
            current_value = scrollbar.value()
            # Конвертируем пиксели в единицы скролла
            scroll_units = int(scroll_pixels / line_height)
            scrollbar.setValue(current_value - scroll_units)

        target._syncing = False


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = ParallelEditor()
    window.show()

    # Открытие файла из командной строки
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
