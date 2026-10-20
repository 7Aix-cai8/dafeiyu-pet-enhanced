"""A small card that fits exactly one comment: no history, no oversized box."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

MIN_WIDTH = 168
MAX_WIDTH = 336
PADDING_X = 16
PADDING_Y = 12
FONT_SIZE = 12
AUTO_HIDE_MS = 26_000


class ChatPanel(QWidget):
    """Frameless card sized to the current comment; drag anywhere to move it."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("petChatPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼点评")
        self._fill = QColor(247, 251, 255)
        self._border = QColor(70, 130, 175, 170)
        self._grab: QPoint | None = None
        self._pinned = False

        self.label = QLabel(self)
        self.label.setFont(QFont(self.font().family(), FONT_SIZE))
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setStyleSheet("color:#173650;background:transparent;")
        # The card itself must receive the drag, so the text never eats clicks.
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PADDING_X, PADDING_Y, PADDING_X, PADDING_Y)
        layout.addWidget(self.label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    # ---- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._fill)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

    # ---- content -----------------------------------------------------------

    def show_comment(self, text: str, pet_rect) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        self.label.setText(text)
        self._pinned = False
        metrics = QFontMetrics(self.label.font())
        natural = metrics.horizontalAdvance(text) + PADDING_X * 2 + 8
        width = max(MIN_WIDTH, min(MAX_WIDTH, natural))
        inner = width - PADDING_X * 2
        block = metrics.boundingRect(
            0, 0, inner, 10000, Qt.TextFlag.TextWordWrap, text
        )
        height = block.height() + PADDING_Y * 2 + 4
        self.setFixedSize(width, height)
        self._place(pet_rect)
        self.show()
        self.raise_()
        # Reading time scales with the sentence: a short jab fades fast, a long
        # one stays long enough to actually be read.
        duration = max(4_000, min(22_000, 2_800 + len(text) * 330))
        self._hide_timer.start(duration)

    def clear(self) -> None:
        self.label.clear()
        self.hide()

    # ---- placement ---------------------------------------------------------

    @staticmethod
    def position_for(pet_rect, area, width: int, height: int) -> tuple[int, int]:
        """Above the pet first; beside it only when the top has no room."""

        gap = 4
        x = pet_rect.center().x() - width // 2
        y = pet_rect.top() - height - gap
        if y < area.top():
            if pet_rect.right() + gap + width <= area.right():
                x = pet_rect.right() + gap
                y = pet_rect.center().y() - height // 2
            elif pet_rect.left() - gap - width >= area.left():
                x = pet_rect.left() - gap - width
                y = pet_rect.center().y() - height // 2
            else:
                y = pet_rect.bottom() + gap
        x = max(area.left(), min(x, area.right() - width + 1))
        y = max(area.top(), min(y, area.bottom() - height + 1))
        return x, y

    def follow(self, pet_rect) -> None:
        """Keep the card beside the pet while it walks around the desktop."""

        if self._pinned or not self.isVisible():
            return
        screen = (
            QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        )
        area = screen.availableGeometry() if screen else None
        if area is None:
            return
        x, y = self.position_for(pet_rect, area, self.width(), self.height())
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _place(self, pet_rect) -> None:
        screen = (
            QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        )
        area = screen.availableGeometry() if screen else None
        if area is None:
            return
        x, y = self.position_for(pet_rect, area, self.width(), self.height())
        self.move(x, y)

    # ---- dragging ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pinned = True
            self._grab = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
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
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.hide()
        event.accept()
