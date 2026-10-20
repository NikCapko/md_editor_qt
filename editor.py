#!/usr/bin/env python3
"""
Parallel Markdown Editor
Редактор для параллельного перевода с синхронизацией по абзацам.
Файлы: .en.md (оригинал, слева) и .ru.md (перевод, справа)

Синхронизация прокрутки — рабочий алгоритм:
  клик → выделить абзац → найти пару ¶N → выровнять по Y через scrollbar.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
    QTextFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


class NumberBar(QWidget):
    """Панель номеров строк (+ ¶N на первой строке абзаца)."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor
        self.setStyleSheet("background-color: #f0f0f0;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        painter.setFont(self.editor.font())

        block = self.editor.firstVisibleBlock()
        offset = self.editor.contentOffset()
        block_rect = self.editor.blockBoundingGeometry(block).translated(offset)
        top = int(block_rect.top())
        line_height = self.editor.fontMetrics().height()
        block_number = block.blockNumber() + 1
        para_starts = self.editor.paragraph_start_lines()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and top + line_height >= event.rect().top():
                painter.setPen(QColor("#888"))
                painter.drawText(
                    0, top, self.width() - 4, line_height,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block_number),
                )
                bn0 = block_number - 1
                if bn0 in para_starts:
                    painter.setPen(QColor("#1a73e8"))
                    f = painter.font()
                    f.setBold(True)
                    f.setPointSize(max(8, f.pointSize() - 1))
                    painter.setFont(f)
                    painter.drawText(
                        2, top, self.width() // 2, line_height,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        f"¶{para_starts[bn0]}",
                    )
                    painter.setFont(self.editor.font())

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


class CodeEditor(QPlainTextEdit):
    """Редактор с номерами строк; синхронизация только по клику мыши."""

    paragraph_clicked = Signal(int, int)  # editor_idx, cursor_position

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
        width = self.fontMetrics().horizontalAdvance("9") * (digits + 3) + 12
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

    def paragraph_start_lines(self) -> dict:
        result = {}
        b = self.document().begin()
        in_para = False
        idx = 0
        while b.isValid():
            if b.text().strip():
                if not in_para:
                    in_para = True
                    idx += 1
                    result[b.blockNumber()] = idx
            else:
                in_para = False
            b = b.next()
        return result

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

        if self._mouse_click:
            self._mouse_click = False
            if not self._syncing:
                self._select_paragraph()
                pos = self.textCursor().position()
                self.paragraph_clicked.emit(self.editor_idx, pos)

    def _select_paragraph(self):
        cursor = self.textCursor()
        pos = cursor.position()
        block = self.document().findBlock(pos)
        if not block.isValid():
            return

        start_block = block
        while start_block.previous().isValid() and start_block.previous().text().strip():
            start_block = start_block.previous()

        end_block = block
        while end_block.next().isValid() and end_block.next().text().strip():
            end_block = end_block.next()

        cursor.setPosition(start_block.position())
        cursor.setPosition(
            end_block.position() + len(end_block.text()),
            QTextCursor.MoveMode.KeepAnchor,
        )
        self.setTextCursor(cursor)

    def highlight_paragraph_at(self, pos: int, color: QColor) -> None:
        block = self.document().findBlock(pos)
        if not block.isValid():
            self.setExtraSelections([])
            return

        start_block = block
        while start_block.previous().isValid() and start_block.previous().text().strip():
            start_block = start_block.previous()
        end_block = block
        while end_block.next().isValid() and end_block.next().text().strip():
            end_block = end_block.next()

        extras = []
        b = start_block
        while b.isValid():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(color)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            c = QTextCursor(b)
            c.clearSelection()
            sel.cursor = c
            extras.append(sel)
            if b == end_block:
                break
            b = b.next()
        self.setExtraSelections(extras)

    def clear_paragraph_highlight(self) -> None:
        self.setExtraSelections([])

    def get_paragraph_index(self, pos: int) -> int:
        block = self.document().findBlock(pos)
        if not block.isValid():
            return -1

        start_block = block
        while start_block.previous().isValid() and start_block.previous().text().strip():
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

    def get_paragraph_position(self, idx: int):
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
        self.left_editor = CodeEditor(0)
        self.right_editor = CodeEditor(1)
        editors_row.addWidget(self.left_editor)
        editors_row.addWidget(self.right_editor)
        root.addLayout(editors_row, 1)

        self.editors = [self.left_editor, self.right_editor]
        self.left_editor.paragraph_clicked.connect(self._sync_paragraph)
        self.right_editor.paragraph_clicked.connect(self._sync_paragraph)
        self.left_editor.textChanged.connect(lambda: self._on_text_changed("en"))
        self.right_editor.textChanged.connect(lambda: self._on_text_changed("ru"))

        self.setStatusBar(QStatusBar())
        self._create_menu()
        self._create_toolbar()
        self.statusBar().showMessage(
            "Клик по абзацу — выделить и выровнять парный абзац"
        )

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
        quit_action = QAction("Выход", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _create_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        open_btn = QAction("Открыть", self)
        open_btn.triggered.connect(self._open_files)
        tb.addAction(open_btn)
        save_btn = QAction("Сохранить", self)
        save_btn.triggered.connect(self._save_files)
        tb.addAction(save_btn)

    def _sync_paragraph(self, editor_idx: int, pos: int) -> None:
        """Рабочий алгоритм выравнивания абзацев по Y."""
        source = self.editors[editor_idx]
        target = self.editors[1 - editor_idx]

        para_idx = source.get_paragraph_index(pos)
        if para_idx < 0:
            return

        target_pos = target.get_paragraph_position(para_idx)
        if target_pos is None:
            self.statusBar().showMessage(
                f"¶{para_idx + 1}: нет пары на другой стороне", 3000
            )
            target.clear_paragraph_highlight()
            return

        # Y начала абзаца в source
        src_para_pos = source.get_paragraph_position(para_idx)
        source_cursor = source.textCursor()
        if src_para_pos is not None:
            source_cursor.setPosition(src_para_pos)
        source_rect = source.cursorRect(source_cursor)
        source_y = source_rect.top()

        # Курсор в target на начало парного абзаца
        target._syncing = True
        target_cursor = QTextCursor(target.document())
        target_cursor.setPosition(target_pos)
        target.setTextCursor(target_cursor)

        # Подсветка обоих абзацев
        source.highlight_paragraph_at(pos, QColor(200, 230, 255))
        target.highlight_paragraph_at(target_pos, QColor(200, 245, 200))

        target_rect = target.cursorRect(target_cursor)
        target_y = target_rect.top()

        # Прокрутка: diff в пикселях → единицы scrollbar (как в рабочем примере)
        diff = source_y - target_y
        if abs(diff) > 5:
            line_height = target_rect.height() if target_rect.height() > 0 else 16
            scrollbar = target.verticalScrollBar()
            scroll_units = int(diff / line_height)
            scrollbar.setValue(scrollbar.value() - scroll_units)

        target._syncing = False
        self.statusBar().showMessage(f"¶{para_idx + 1} выровнен", 2500)

    def _on_text_changed(self, side: str) -> None:
        if side == "en":
            if self.left_editor._syncing:
                return
            self._en_modified = True
            name = self._en_path.name if self._en_path else "Оригинал (.en.md)"
            self.lbl_en.setText(f"Оригинал — {name} *")
        else:
            if self.right_editor._syncing:
                return
            self._ru_modified = True
            name = self._ru_path.name if self._ru_path else "Перевод (.ru.md)"
            self.lbl_ru.setText(f"Перевод — {name} *")
        self._update_title()

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
