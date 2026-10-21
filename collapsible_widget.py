#!/usr/bin/env python3
"""
Сворачиваемый контейнер с адаптивным заголовком.
"""

from PySide6.QtCore import (
    QRect,
    QSettings,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class AdaptiveHeader(QWidget):
    """Адаптивный заголовок: горизонтальный при развёрнутом, вертикальный при свёрнутом."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._status = ""
        self._status_color = "#188038"
        self._expanded = True
        self._theme = "light"
        self._font = QFont()
        self._font.setBold(True)
        self._font.setPointSize(11)
        self.setMinimumHeight(30)
        self._apply_theme()

    def set_theme(self, theme: str) -> None:
        """Применить тему."""
        self._theme = theme
        self._apply_theme()
        self.update()

    def _apply_theme(self) -> None:
        """Обновить стили для темы."""
        if self._theme == "dark":
            self._bg_color = QColor("#2d2d30")
            self._border_color = QColor("#3c3c3c")
            self._text_color = QColor("#cccccc")
        else:
            self._bg_color = QColor("#f5f5f5")
            self._border_color = QColor("#e0e0e0")
            self._text_color = QColor("#333")
        self.update()

    def set_expanded(self, expanded: bool) -> None:
        """Установить состояние развёрнутости."""
        self._expanded = expanded
        self.updateGeometry()
        self.update()

    def set_status(self, text: str, color: str = "#188038") -> None:
        """Установить текст статуса."""
        self._status = text
        self._status_color = color
        self.update()

    def sizeHint(self):
        """Размер зависит от состояния."""
        if self._expanded:
            # Горизонтальный заголовок
            return super().sizeHint()
        else:
            # Вертикальный заголовок — узкий и высокий
            from PySide6.QtCore import QSize

            return QSize(28, 200)

    def paintEvent(self, event) -> None:
        """Отрисовка заголовка."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Фон
        painter.fillRect(self.rect(), self._bg_color)

        if self._expanded:
            # Горизонтальный заголовок
            # Рамка снизу
            painter.setPen(self._border_color)
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

            # Стрелка ▼ слева
            arrow_font = QFont()
            arrow_font.setPointSize(10)
            painter.setFont(arrow_font)
            painter.setPen(self._text_color)
            painter.drawText(
                QRect(8, 0, 20, self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "▼",
            )

            # Текст по центру
            painter.setFont(self._font)
            painter.setPen(self._text_color)
            text_rect = self.rect().adjusted(30, 0, -8, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._title,
            )

            # Статус справа
            if self._status:
                status_font = QFont()
                status_font.setPointSize(9)
                painter.setFont(status_font)
                painter.setPen(QColor(self._status_color))
                status_rect = self.rect().adjusted(0, 0, -8, 0)
                painter.drawText(
                    status_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    self._status,
                )
        else:
            # Вертикальный заголовок
            # Рамка справа
            painter.setPen(self._border_color)
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

            # Стрелка ▼ сверху (повёрнутая)
            painter.save()
            arrow_font = QFont()
            arrow_font.setPointSize(10)
            painter.setFont(arrow_font)
            painter.setPen(self._text_color)
            painter.translate(self.width() / 2, 25)  # Позиция стрелки
            painter.rotate(-90)
            painter.drawText(0, 0, "▼")
            painter.restore()

            # Текст повёрнутый, выровнен сверху
            painter.save()
            painter.translate(self.width() / 2, 130)  # Начинаем сверху
            painter.rotate(-90)
            painter.setFont(self._font)
            painter.setPen(self._text_color)
            painter.drawText(0, 0, self._title)
            painter.restore()

            # Статус внизу (тоже повёрнутый)
            if self._status:
                painter.save()
                status_font = QFont()
                status_font.setPointSize(9)
                painter.setFont(status_font)
                painter.setPen(QColor(self._status_color))
                painter.translate(self.width() / 2, self.height() - 10)
                painter.rotate(-90)
                painter.drawText(0, 0, self._status)
                painter.restore()


class CollapsibleWidget(QWidget):
    """Сворачиваемый контейнер с адаптивным заголовком."""

    toggled = Signal(bool)  # expanded

    def __init__(
        self,
        title: str,
        settings_key: str = "",
        orientation: str = "vertical",  # "vertical" | "horizontal"
        parent=None,
    ):
        super().__init__(parent)
        self._title = title
        self._settings_key = settings_key
        self._orientation = orientation
        self._expanded = True
        self._theme = "light"

        # Основной layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        if orientation == "vertical":
            # Адаптивный заголовок (горизонтальный/вертикальный)
            self.adaptive_header = AdaptiveHeader(title)
            self.adaptive_header.mousePressEvent = self._on_header_click
            self.adaptive_header.setCursor(Qt.CursorShape.PointingHandCursor)
            main_layout.addWidget(self.adaptive_header)

            # Контейнер для содержимого
            self.content = QFrame()
            self.content.setObjectName("collapsibleContent")
            self.content_layout = QVBoxLayout(self.content)
            self.content_layout.setContentsMargins(4, 4, 4, 4)
            self.content_layout.setSpacing(4)
            main_layout.addWidget(self.content, 1)
        else:
            # Горизонтальный заголовок с кнопкой-стрелкой
            header = QFrame()
            header.setObjectName("collapsibleHeader")
            header.setCursor(Qt.CursorShape.PointingHandCursor)
            header.mousePressEvent = self._on_header_click
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(8, 4, 8, 4)
            header_layout.setSpacing(4)

            # Кнопка-стрелка
            self.toggle_btn = QLabel("▼")
            self.toggle_btn.setStyleSheet("font-size: 10px; font-weight: bold;")
            header_layout.addWidget(self.toggle_btn)

            # Заголовок
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
            header_layout.addWidget(self.title_label, 1)

            # Статус
            self.status_label = QLabel("")
            self.status_label.setStyleSheet("color: #188038; font-size: 11px;")
            header_layout.addWidget(self.status_label)

            main_layout.addWidget(header)

            # Контейнер для содержимого
            self.content = QFrame()
            self.content.setObjectName("collapsibleContent")
            self.content_layout = QVBoxLayout(self.content)
            self.content_layout.setContentsMargins(8, 4, 8, 8)
            self.content_layout.setSpacing(4)
            main_layout.addWidget(self.content)

        # Загрузить состояние из настроек
        if settings_key:
            self._expanded = QSettings().value(
                f"ui/{settings_key}_expanded", True, type=bool
            )
            self._update_visuals()

        self._apply_theme()

    def _on_header_click(self, event) -> None:
        """Клик по заголовку — переключить состояние."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()

    def _update_visuals(self) -> None:
        """Обновить визуальные элементы."""
        if self._orientation == "vertical":
            # Обновить адаптивный заголовок
            self.adaptive_header.set_expanded(self._expanded)
            # Показать/скрыть содержимое
            self.content.setVisible(self._expanded)
        else:
            # Для горизонтального — обновить кнопку-стрелку
            if hasattr(self, "toggle_btn"):
                self.toggle_btn.setText("▼" if self._expanded else "▶")
            # Показать/скрыть content
            self.content.setVisible(self._expanded)

    def set_theme(self, theme: str) -> None:
        """Применить тему."""
        self._theme = theme
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Обновить стили для темы."""
        if self._orientation == "vertical":
            self.adaptive_header.set_theme(self._theme)
        else:
            if self._theme == "dark":
                header_bg = "#2d2d30"
                header_border = "#3c3c3c"
                content_bg = "#252526"
                title_color = "#cccccc"
            else:
                header_bg = "#f5f5f5"
                header_border = "#e0e0e0"
                content_bg = "#fafafa"
                title_color = "#333"

            self.findChild(QFrame, "collapsibleHeader").setStyleSheet(
                f"""
                QFrame#collapsibleHeader {{
                    background: {header_bg};
                    border: 1px solid {header_border};
                    border-radius: 4px;
                }}
                """
            )

            self.title_label.setStyleSheet(
                f"font-weight: bold; font-size: 13px; color: {title_color};"
            )

            self.content.setStyleSheet(
                f"""
                QFrame#collapsibleContent {{
                    background: {content_bg};
                    border: 1px solid {header_border};
                    border-top: none;
                    border-radius: 0 0 4px 4px;
                }}
                """
            )

    def add_widget(self, widget: QWidget) -> None:
        """Добавить виджет в содержимое."""
        self.content_layout.addWidget(widget)

    def toggle(self) -> None:
        """Переключить состояние."""
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Установить состояние развёрнутости."""
        if self._expanded == expanded:
            return

        self._expanded = expanded
        self._update_visuals()

        # Сохранить состояние
        if self._settings_key:
            QSettings().setValue(f"ui/{self._settings_key}_expanded", expanded)

        self.toggled.emit(expanded)

    def set_status(self, text: str, color: str = "#188038") -> None:
        """Установить текст статуса."""
        if self._orientation == "vertical":
            self.adaptive_header.set_status(text, color)
        else:
            if hasattr(self, "status_label"):
                self.status_label.setText(text)
                self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
