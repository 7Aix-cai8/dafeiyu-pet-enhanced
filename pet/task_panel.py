"""A small, local task list the pet can open on a double click.

Rows are paged three at a time, each with its own dark shade.  Checking a task
runs a short green tick animation, then dims the text, strikes it through, adds
a delete mark and moves the task to the end of the list.  Tasks can be added,
edited inline, deleted and dragged to reorder.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

PAGE_SIZE = 3
ROW_HEIGHT = 44
ROW_GAP = 6
PANEL_WIDTH = 372

# Light row shades (kept distinct) that read cleanly against the light panel.
ROW_SHADES = (
    "rgb(250, 253, 255)",
    "rgb(238, 245, 251)",
    "rgb(226, 237, 247)",
)
ROW_BORDER = "rgba(58, 122, 170, 135)"
ROW_BORDER_ACTIVE = "rgba(40, 110, 170, 210)"
TEXT_COLOR = "#173650"
DONE_TEXT_COLOR = "#92a9bb"
ACCENT = "#48d597"
DANGER = "#e05060"

FONT_FAMILY = "Microsoft YaHei UI"


def _font(size: int = 10, bold: bool = False) -> QFont:
    font = QFont(FONT_FAMILY, size)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


@dataclass(slots=True)
class Task:
    id: str
    text: str
    done: bool = False
    created_at: float = 0.0


class TaskStore:
    """JSON-backed task list kept beside the pet's other local state."""

    def __init__(self, path) -> None:
        self.path = path
        self._items: list[Task] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = []
        items: list[Task] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            items.append(
                Task(
                    id=str(entry.get("id") or uuid.uuid4().hex),
                    text=text,
                    done=bool(entry.get("done")),
                    created_at=float(entry.get("created_at") or 0.0),
                )
            )
        self._items = items

    def save(self) -> None:
        payload = [
            {"id": t.id, "text": t.text, "done": t.done, "created_at": t.created_at}
            for t in self._items
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)
        except OSError:
            pass

    @property
    def items(self) -> list[Task]:
        return list(self._items)

    def add(self, text: str) -> Task | None:
        text = text.strip()
        if not text:
            return None
        task = Task(id=uuid.uuid4().hex, text=text, done=False, created_at=time.time())
        # New tasks slot in right after the last pending task — i.e. before any
        # completed task that has already been sunk to the end of the list.
        index = len(self._items)
        for position, existing in enumerate(self._items):
            if existing.done:
                index = position
                break
        self._items.insert(index, task)
        return task

    def update_text(self, task_id: str, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        for task in self._items:
            if task.id == task_id:
                task.text = text
                return True
        return False

    def remove(self, task_id: str) -> bool:
        before = len(self._items)
        self._items = [task for task in self._items if task.id != task_id]
        return len(self._items) != before

    def toggle(self, task_id: str) -> bool:
        for index, task in enumerate(self._items):
            if task.id != task_id:
                continue
            task.done = not task.done
            if task.done:
                self._items.append(self._items.pop(index))
            return True
        return False

    def move(self, from_index: int, to_index: int) -> bool:
        total = len(self._items)
        if not (0 <= from_index < total) or not (0 <= to_index < total):
            return False
        if from_index == to_index:
            return False
        task = self._items.pop(from_index)
        self._items.insert(to_index, task)
        return True


class TaskRow(QFrame):
    """One task row: dark shade, inline editing, tick or delete on the right."""

    def __init__(self, panel: "TaskPanel", index: int, task: Task) -> None:
        super().__init__()
        self.panel = panel
        self.index = index
        self.task = task
        self._press_pos: QPoint | None = None
        self._editing = False
        self._animation: QVariantAnimation | None = None

        self.setObjectName("taskRow")
        self.setFixedHeight(ROW_HEIGHT)
        shade = ROW_SHADES[index % len(ROW_SHADES)]
        self.setStyleSheet(
            f"#taskRow{{background:{shade};border:2px solid {ROW_BORDER};border-radius:11px;}}"
        )

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 5, 7, 5)
        self.layout.setSpacing(7)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.dot.setFont(_font(7))
        self.dot.setStyleSheet(f"color:{ACCENT if task.done else '#3d7ea6'};")

        self.label = QLabel()
        self.label.setFont(_font(10))
        self.label.setWordWrap(False)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setStyleSheet("background:transparent;")
        self.label.mouseDoubleClickEvent = self._begin_edit  # type: ignore[assignment]

        self.editor = QLineEdit(self)
        self.editor.setFont(_font(10))
        self.editor.setFrame(False)
        self.editor.hide()
        self.editor.setStyleSheet(
            "QLineEdit{background:rgba(255,255,255,160);border:1px solid rgba(60,130,190,120);"
            "border-radius:6px;color:#173650;padding:2px 6px;}"
        )
        self.editor.returnPressed.connect(self._commit_edit)
        self.editor.editingFinished.connect(self._commit_edit)

        self.action = QToolButton(self)
        self.action.setFixedSize(30, 30)
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.setFont(_font(11, bold=True))
        self.action.clicked.connect(self._on_action)

        self.edit_button = QToolButton(self)
        self.edit_button.setFixedSize(26, 26)
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.setText("✎")
        self.edit_button.setFont(_font(10, bold=True))
        self.edit_button.setToolTip("编辑这条任务")
        self.edit_button.setStyleSheet(
            "QToolButton{background:transparent;border:none;border-radius:7px;color:#4a7ba0;font-size:13px;}"
            "QToolButton:hover{background:rgba(60,130,190,40);color:#173650;}"
        )
        self.edit_button.clicked.connect(self._begin_edit_from_button)

        self.layout.addWidget(self.dot, 0)
        self.layout.addWidget(self.label, 1)
        self.layout.addWidget(self.editor, 1)
        self.layout.addWidget(self.edit_button, 0)
        self.layout.addWidget(self.action, 0)

        self._render()

    # ---- rendering ---------------------------------------------------------

    def _render(self) -> None:
        task = self.task
        if task.done:
            self.label.setText(
                f'<span style="color:{DONE_TEXT_COLOR};text-decoration:line-through;">{_escape(task.text)}</span>'
            )
            self.action.setText("✕")
            self.dot.setStyleSheet(f"color:{ACCENT};")
            self.action.setStyleSheet(
                "QToolButton{background:transparent;border:none;border-radius:8px;"
                f"color:{DONE_TEXT_COLOR};font-size:13px;}}"
                "QToolButton:hover{background:rgba(224,80,96,45);color:#c23b48;}"
            )
        else:
            self.label.setText(
                f'<span style="color:{TEXT_COLOR};text-decoration:none;">{_escape(task.text)}</span>'
            )
            self.action.setText("✓")
            self.dot.setStyleSheet("color:#3d7ea6;")
            self.action.setStyleSheet(
                "QToolButton{background:rgba(60,130,190,26);border:1px solid rgba(60,130,190,120);"
                "border-radius:8px;color:#2f6f9e;font-size:13px;}"
                "QToolButton:hover{background:rgba(60,130,190,60);color:#173650;}"
            )

    def _apply_tick_progress(self, progress: float) -> None:
        """Blend the tick button toward green while the animation runs."""

        green = QColor(72, 213, 151)
        base = QColor(0, 0, 0, 0)
        blended = QColor(
            int(base.red() * (1 - progress) + green.red() * progress),
            int(base.green() * (1 - progress) + green.green() * progress),
            int(base.blue() * (1 - progress) + green.blue() * progress),
        )
        self.action.setStyleSheet(
            f"QToolButton{{background:rgba({blended.red()},{blended.green()},{blended.blue()},"
            f"{int(90 + 120 * progress)});border:1px solid {ACCENT};border-radius:8px;"
            f"color:{'#06251a' if progress > 0.7 else '#173650'};font-size:13px;}}"
        )
        self.action.setText("✓")

    # ---- interactions ------------------------------------------------------

    def _on_action(self) -> None:
        if self.task.done:
            self.panel.delete_task(self.task.id)
            return
        self._animate_tick()

    def _animate_tick(self) -> None:
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(240)
        animation.valueChanged.connect(self._apply_tick_progress)
        animation.finished.connect(self._finish_tick)
        self._animation = animation
        self.action.setEnabled(False)
        animation.start()

    def _finish_tick(self) -> None:
        self.action.setEnabled(True)
        if not self.task.done:
            self.panel.toggle_task(self.task.id)

    def _begin_edit_from_button(self) -> None:
        self._begin_edit(None)

    def _begin_edit(self, event=None) -> None:
        if self._editing:
            return
        self._editing = True
        self.label.hide()
        self.editor.setText(self.task.text)
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()
        if event is not None:
            event.accept()

    def _commit_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        text = self.editor.text().strip()
        self.editor.hide()
        self.label.show()
        if text and text != self.task.text:
            self.panel.update_task(self.task.id, text)
        else:
            self._render()

    # ---- drag to reorder ---------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._editing:
            self._press_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is None:
            return
        self.panel.preview_drag(self.task.id, event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._press_pos is not None:
            self.panel.finish_drag(self.task.id, event.globalPosition().toPoint())
            self._press_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class DragHeader(QWidget):
    """Header strip that moves the whole panel while it is dragged."""

    def __init__(self, panel: "TaskPanel") -> None:
        super().__init__(panel)
        self._panel = panel
        self._grab: QPoint | None = None
        self.setObjectName("taskHeader")
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet("background:transparent;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.globalPosition().toPoint() - self._panel.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._grab is not None:
            self._panel.move(event.globalPosition().toPoint() - self._grab)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._grab is not None:
            self._grab = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TaskPanel(QWidget):
    """Floating task list opened by a double click on the pet."""

    def __init__(self, store: TaskStore, on_close=None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.store = store
        self.on_close = on_close
        self._page = 0
        self._rows: list[TaskRow] = []
        self._drag_id: str | None = None
        self._drag_preview_index: int | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.store.save)

        self.setObjectName("taskPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(PANEL_WIDTH)
        self.setStyleSheet("#taskPanel{background:transparent;}")
        self._panel_fill = QColor(228, 238, 247)
        self._panel_border = QColor(70, 130, 175, 120)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(7)

        # header -------------------------------------------------------------
        self.header_bar = DragHeader(self)
        header = QHBoxLayout(self.header_bar)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.title = QLabel("任务清单")
        self.title.setFont(_font(11, bold=True))
        self.title.setStyleSheet("color:#173650;background:transparent;")
        self.counter = QLabel("")
        self.counter.setFont(_font(8))
        self.counter.setStyleSheet("color:#5c7f99;background:transparent;")
        self.prev_button = self._arrow("‹")
        self.next_button = self._arrow("›")
        close = self._arrow("✕")
        close.clicked.connect(self._dismiss)
        self.prev_button.clicked.connect(lambda: self._turn(-1))
        self.next_button.clicked.connect(lambda: self._turn(1))
        header.addWidget(self.title, 0)
        header.addWidget(self.counter, 0)
        header.addStretch(1)
        header.addWidget(self.prev_button, 0)
        header.addWidget(self.next_button, 0)
        header.addWidget(close, 0)
        outer.addWidget(self.header_bar, 0)

        # rows ---------------------------------------------------------------
        self.rows_host = QWidget(self)
        self.rows_host.setStyleSheet("background:transparent;")
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(ROW_GAP)
        outer.addWidget(self.rows_host, 1)

        # empty hint ---------------------------------------------------------
        self.empty_hint = QLabel("还没有任务，双击下面的输入框添加第一条～")
        self.empty_hint.setFont(_font(9))
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setStyleSheet("color:#5f7f96;background:transparent;padding:8px 4px;")
        outer.addWidget(self.empty_hint, 1)
        self.empty_hint.hide()

        # add row ------------------------------------------------------------
        self.input = QLineEdit(self)
        self.input.setFont(_font(10))
        self.input.setPlaceholderText("＋ 添加任务，回车确认")
        self.input.setFixedHeight(34)
        self.input.setStyleSheet(
            "QLineEdit{background:#ffffff;border:1px solid rgba(70,130,175,120);"
            "border-radius:10px;color:#173650;padding:2px 10px;}"
            "QLineEdit:focus{border:1px solid rgba(50,130,200,200);}"
        )
        self.input.returnPressed.connect(self._add_from_input)
        outer.addWidget(self.input, 0)

        self.setFixedHeight(56 + PAGE_SIZE * (ROW_HEIGHT + ROW_GAP) + 46)
        self.refresh()

    @staticmethod
    def _arrow(glyph: str) -> QToolButton:
        button = QToolButton()
        button.setText(glyph)
        button.setFixedSize(24, 24)
        button.setFont(_font(11, bold=True))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QToolButton{background:rgba(80,140,190,22);border:none;border-radius:7px;color:#2f6f9e;}"
            "QToolButton:hover{background:rgba(80,140,190,52);color:#173650;}"
            "QToolButton:disabled{color:#aec3d2;}"
        )
        return button

    def paintEvent(self, event) -> None:
        """Paint the opaque rounded panel body behind the child rows."""

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._panel_border, 1))
        painter.setBrush(self._panel_fill)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)

    # ---- public actions ----------------------------------------------------

    def popup_near(self, pet_rect: QRect) -> None:
        """Open beside the pet, on whichever side leaves more room."""

        self.refresh()
        application = QApplication.instance()
        screen = None
        if application is not None:
            for candidate in application.screens():
                if candidate.availableGeometry().contains(pet_rect.center()):
                    screen = candidate
                    break
            if screen is None:
                screen = application.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)

        gap = 12
        panel_width = self.width()
        panel_height = self.height()
        pet_center = pet_rect.center()
        top_y = pet_center.y() - panel_height // 2

        right_x = pet_rect.right() + gap
        left_x = pet_rect.left() - panel_width - gap
        prefer_right = pet_center.x() <= area.center().x()
        order = [(right_x, top_y), (left_x, top_y)]
        if not prefer_right:
            order.reverse()

        def fits(x: int, y: int) -> bool:
            return (
                x >= area.left()
                and x + panel_width <= area.right()
                and y >= area.top()
                and y + panel_height <= area.bottom()
            )

        position = next(((x, y) for x, y in order if fits(x, y)), order[0])
        x = max(area.left() + 6, min(position[0], area.right() - panel_width - 6))
        y = max(area.top() + 6, min(position[1], area.bottom() - panel_height - 6))
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.clear()

    def popup_at(self, x: float, y: float) -> None:
        """Backward-compatible helper: treat (x, y) as the pet's centre."""

        self.popup_near(QRect(int(x) - 60, int(y) - 130, 120, 170))

    # ---- panel dragging ----------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._panel_grab = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        grab = getattr(self, "_panel_grab", None)
        if grab is not None:
            self.move(event.globalPosition().toPoint() - grab)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if getattr(self, "_panel_grab", None) is not None:
            self._panel_grab = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def toggle_task(self, task_id: str) -> None:
        if self.store.toggle(task_id):
            self._page = min(self._page, self.page_count() - 1) if self.page_count() else 0
            self._schedule_save()
            self.refresh()

    def delete_task(self, task_id: str) -> None:
        if self.store.remove(task_id):
            if self.store.items and self._page * PAGE_SIZE >= len(self.store.items):
                self._page = max(0, self.page_count() - 1)
            self._schedule_save()
            self.refresh()

    def update_task(self, task_id: str, text: str) -> None:
        if self.store.update_text(task_id, text):
            self._schedule_save()
            self.refresh()

    # ---- internals ---------------------------------------------------------

    def page_count(self) -> int:
        return max(1, (len(self.store.items) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _schedule_save(self) -> None:
        self._save_timer.start(250)

    def _turn(self, direction: int) -> None:
        target = max(0, min(self.page_count() - 1, self._page + direction))
        if target != self._page:
            self._page = target
            self.refresh()

    def _dismiss(self) -> None:
        self.hide()
        if self.on_close is not None:
            self.on_close()

    def _add_from_input(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        task = self.store.add(text)
        self.input.clear()
        if task is not None:
            index = next(
                (i for i, item in enumerate(self.store.items) if item.id == task.id),
                0,
            )
            self._page = index // PAGE_SIZE
            self._schedule_save()
            self.refresh()

    def refresh(self) -> None:
        self.counter.setText(f" {self._page + 1}/{self.page_count()} ")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < self.page_count() - 1)

        self.rows_host.setUpdatesEnabled(False)
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        items = self.store.items
        start = self._page * PAGE_SIZE
        visible = items[start : start + PAGE_SIZE]

        if not items:
            self.rows_host.hide()
            self.empty_hint.show()
        else:
            self.rows_host.show()
            self.empty_hint.hide()
            for offset, task in enumerate(visible):
                row = TaskRow(self, offset, task)
                self.rows_layout.addWidget(row)
                self._rows.append(row)
            for _ in range(PAGE_SIZE - len(visible)):
                spacer = QWidget()
                spacer.setFixedHeight(ROW_HEIGHT)
                spacer.setStyleSheet("background:transparent;")
                self.rows_layout.addWidget(spacer)

        self.rows_host.setUpdatesEnabled(True)

    # ---- drag preview / commit --------------------------------------------

    def _row_under(self, global_pos: QPoint) -> int | None:
        for row in self._rows:
            top = row.mapToGlobal(QPoint(0, 0)).y()
            if top <= global_pos.y() <= top + row.height():
                return row.index
        return None

    def preview_drag(self, task_id: str, global_pos: QPoint) -> None:
        target = self._row_under(global_pos)
        if target is None or target == self._drag_preview_index:
            return
        self._drag_id = task_id
        self._drag_preview_index = target
        for row in self._rows:
            if row.index == target:
                row.setStyleSheet(
                    f"#taskRow{{background:{ROW_SHADES[row.index % len(ROW_SHADES)]};"
                    f"border:2px solid {ROW_BORDER_ACTIVE};border-radius:11px;}}"
                )
            else:
                row.setStyleSheet(
                    f"#taskRow{{background:{ROW_SHADES[row.index % len(ROW_SHADES)]};"
                    f"border:2px solid {ROW_BORDER};border-radius:11px;}}"
                )

    def finish_drag(self, task_id: str, global_pos: QPoint) -> None:
        self._drag_preview_index = None
        target = self._row_under(global_pos)
        if target is None:
            self.refresh()
            return
        items = self.store.items
        try:
            current = next(i for i, task in enumerate(items) if task.id == task_id)
        except StopIteration:
            self.refresh()
            return
        absolute_target = self._page * PAGE_SIZE + target
        if self.store.move(current, absolute_target):
            self._schedule_save()
        self.refresh()
