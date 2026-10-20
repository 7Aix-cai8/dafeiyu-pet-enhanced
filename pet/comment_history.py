"""A small card listing the latest comments, styled like the comment card."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

CARD_WIDTH = 330
PADDING = 14
FONT_SIZE = 12


class FadeDivider(QWidget):
    """A hairline that is strongest in the middle and fades out at both ends."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(9)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0.0, 0.0, float(self.width()), 0.0)
        gradient.setColorAt(0.0, QColor(58, 122, 170, 0))
        gradient.setColorAt(0.5, QColor(58, 122, 170, 120))
        gradient.setColorAt(1.0, QColor(58, 122, 170, 0))
        painter.setPen(QPen(QBrush(gradient), 1))
        middle = self.height() // 2
        painter.drawLine(0, middle, self.width(), middle)


class CommentHistoryPanel(QWidget):
    """Read-only view of the last few comments; drag anywhere to move it."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("petCommentHistory")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼点评历史")
        self._fill = QColor(247, 251, 255)
        self._border = QColor(70, 130, 175, 170)
        self._grab: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(PADDING, PADDING - 2, PADDING, PADDING)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel("最近点评")
        title.setStyleSheet(
            "color:#5c7f99;background:transparent;font-size:11px;font-weight:700;"
        )
        header.addWidget(title)
        header.addStretch(1)
        close = QToolButton()
        close.setText("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            "QToolButton{background:transparent;border:none;border-radius:6px;"
            "color:#7d97aa;font-size:11px;padding:0 5px;}"
            "QToolButton:hover{background:rgba(80,140,190,45);color:#173650;}"
        )
        close.clicked.connect(self.hide)
        header.addWidget(close)
        outer.addLayout(header)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 2, 0, 0)
        self._body.setSpacing(2)
        outer.addLayout(self._body)

        font = QFont(self.font().family(), FONT_SIZE)
        self.setFont(font)
        self.hide()

    # ---- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._fill)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

    # ---- content -----------------------------------------------------------

    def show_comments(self, comments, anchor_rect) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        items = [item for item in (comments or []) if item][:3]
        if not items:
            empty = QLabel("还没有点评。")
            empty.setStyleSheet("color:#7d97aa;background:transparent;font-size:11px;")
            self._body.addWidget(empty)
        for index, text in enumerate(items):
            if index:
                self._body.addWidget(FadeDivider(self))
            entry = QLabel(text)
            entry.setWordWrap(True)
            entry.setStyleSheet(
                "color:#173650;background:transparent;font-size:12px;"
            )
            self._body.addWidget(entry)

        self.setFixedWidth(CARD_WIDTH)
        self.adjustSize()
        self.setFixedSize(CARD_WIDTH, self.sizeHint().height())
        self._place(anchor_rect)
        self.show()
        self.raise_()

    # ---- placement ---------------------------------------------------------

    def _place(self, anchor) -> None:
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        if area is None:
            return
        gap = 4
        x = anchor.center().x() - self.width() // 2
        y = anchor.top() - self.height() - gap
        if y < area.top():
            if anchor.right() + gap + self.width() <= area.right():
                x = anchor.right() + gap
                y = anchor.center().y() - self.height() // 2
            else:
                y = anchor.bottom() + gap
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)

    # ---- dragging ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._grab is not None:
            self.move(event.globalPosition().toPoint() - self._grab)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._grab is not None:
            self._grab = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
