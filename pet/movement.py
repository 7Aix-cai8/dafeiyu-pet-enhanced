from __future__ import annotations

import random
import time
import math
from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QWidget


class MovementController:
    """Movement modes are independent from rendering and slow context sensing."""

    modes = {"wander", "follow", "still"}

    # A four-frame chibi gait needs room to read. Short, intentional walks at
    # this speed read as steps instead of a sprite being translated by code.
    def __init__(
        self,
        mode: str = "wander",
        speed: float = 82.0,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.mode = mode if mode in self.modes else "wander"
        self.speed = speed
        self.rng = rng or random.Random()
        self.target: tuple[float, float] | None = None
        self.rest_until = 0.0
        self.current_speed = 0.0
        self.direction = "down"
        self.facing = 1
        # "up" / "down" while roaming the free desktop, None while walking flat.
        self.climb: str | None = None

    def set_speed(self, speed: float) -> None:
        self.speed = max(48.0, min(128.0, float(speed)))

    @property
    def is_walking(self) -> bool:
        return self.target is not None

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in self.modes else "wander"
        self.cancel_target()

    def cancel_target(self) -> None:
        self.target = None
        self.current_speed = 0.0
        self.climb = None

    def set_direction(self, direction: str, facing: int | None = None) -> bool:
        changed_view = direction != self.direction
        self.direction = direction
        if facing is not None:
            self.facing = -1 if facing < 0 else 1
        return changed_view

    def rest_after_drag(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.cancel_target()
        self.rest_until = now + self.rng.uniform(6.0, 14.0)

    def request_short_wander(
        self,
        widget: QWidget,
        *,
        body_widths: float,
        now: float,
        away_from_cursor: bool = False,
        horizontal_bounds: tuple[float, float] | None = None,
        vertical_bounds: tuple[float, float] | None = None,
    ) -> bool:
        """Schedule one nearby walk chosen by the activity director."""

        if self.mode != "wander" or self.target is not None or now < self.rest_until:
            return False
        screen = widget.screen() or QApplication.primaryScreen()
        if screen is None:
            return False
        geometry = screen.availableGeometry()
        if horizontal_bounds is None:
            left = float(geometry.left() + 20)
            right = float(max(left, geometry.right() - widget.width() - 20))
        else:
            left = max(float(geometry.left()), float(horizontal_bounds[0]))
            right = min(
                float(geometry.right() - widget.width() + 1),
                float(horizontal_bounds[1]),
            )
            right = max(left, right)

        body_width = max(80.0, min(widget.width() * 0.82, widget.height() * 0.72))
        distance = body_width * max(0.55, min(2.5, float(body_widths)))
        center_x = widget.x() + widget.width() / 2.0
        cursor = widget.cursor().pos()
        if away_from_cursor:
            preferred_sign = -1.0 if cursor.x() >= center_x else 1.0
        else:
            preferred_sign = self.rng.choice((-1.0, 1.0))

        candidates: list[tuple[float, float, float]] = []
        for sign in (preferred_sign, -preferred_sign):
            for _ in range(4):
                horizontal = sign * distance * self.rng.uniform(0.82, 1.0)
                target_x = max(left, min(right, widget.x() + horizontal))
                if vertical_bounds is not None:
                    top, bottom = vertical_bounds
                    vertical = self.rng.uniform(-1.0, 1.0) * body_width * self.rng.uniform(0.7, 2.2)
                    target_y = max(top, min(bottom, widget.y() + vertical))
                else:
                    target_y = float(widget.y())
                moved = math.hypot(target_x - widget.x(), target_y - widget.y())
                target_cursor_distance = (
                    (target_x + widget.width() / 2.0 - cursor.x()) ** 2
                    + (target_y + widget.height() / 2.0 - cursor.y()) ** 2
                ) ** 0.5
                score = moved
                if away_from_cursor:
                    current_cursor_distance = (
                        (center_x - cursor.x()) ** 2
                        + (widget.y() + widget.height() / 2.0 - cursor.y()) ** 2
                    ) ** 0.5
                    score += (target_cursor_distance - current_cursor_distance) * 1.8
                elif target_cursor_distance < 140.0:
                    score -= 120.0
                candidates.append((score, target_x, target_y))

        if not candidates:
            return False
        _, target_x, target_y = max(candidates, key=lambda candidate: candidate[0])
        actual_distance = math.hypot(target_x - widget.x(), target_y - widget.y())
        if actual_distance < 28.0:
            return False
        self.target = (target_x, target_y)
        return True

    def update(
        self,
        widget: QWidget,
        *,
        now: float,
        delta_seconds: float,
        set_direction: Callable[[str, int | None], None],
        energy: float = 0.78,
        horizontal_bounds: tuple[float, float] | None = None,
        vertical_bounds: tuple[float, float] | None = None,
    ) -> bool:
        if self.mode == "follow":
            self._set_follow_target(widget)
        elif self.mode == "wander":
            # Wander targets now come from ActivityDirector. This keeps movement
            # short and purposeful instead of selecting any point on the screen.
            pass
        else:
            self.target = None
            self.current_speed = 0.0
            self.climb = None
            return False

        if self.target is None:
            self.current_speed = max(0.0, self.current_speed - self.speed * delta_seconds * 4)
            return False

        target_x, target_y = self.target
        if horizontal_bounds is not None:
            lower, upper = horizontal_bounds
            target_x = max(lower, min(upper, target_x))
        if vertical_bounds is not None:
            top, bottom = vertical_bounds
            target_y = max(top, min(bottom, target_y))
        else:
            target_y = float(widget.y())
        self.target = (target_x, target_y)

        delta_x = target_x - widget.x()
        delta_y = target_y - widget.y()
        distance = math.hypot(delta_x, delta_y)
        if distance < 8.0:
            self.target = None
            self.current_speed = 0.0
            self.climb = None
            self.rest_until = now + self.rng.uniform(3.0, 7.0)
            return False

        energy = max(0.0, min(1.0, float(energy)))
        effective_speed = self.speed * (0.62 + energy * 0.48)
        self.current_speed += (effective_speed - self.current_speed) * min(1.0, delta_seconds * 15.0)
        step = min(distance, self.current_speed * delta_seconds)
        ratio = step / distance
        next_x = widget.x() + delta_x * ratio
        next_y = widget.y() + delta_y * ratio
        if horizontal_bounds is not None:
            lower, upper = horizontal_bounds
            next_x = max(lower, min(upper, next_x))
        if vertical_bounds is not None:
            top, bottom = vertical_bounds
            next_y = max(top, min(bottom, next_y))
        widget.move(round(next_x), round(next_y))

        # Steep travel is a climb or a descent, but the back/front views only
        # carry a single frame, so switching to them froze the sprite. Keep the
        # four-frame side gait and let the renderer tilt it into the slope.
        if abs(delta_y) > abs(delta_x) * 0.75:
            self.climb = "up" if delta_y < 0 else "down"
            if abs(delta_x) > 1.0:
                set_direction("left" if delta_x < 0 else "right", -1 if delta_x < 0 else 1)
        else:
            self.climb = None
            set_direction("left" if delta_x < 0 else "right", -1 if delta_x < 0 else 1)
        return True

    def _set_follow_target(self, widget: QWidget) -> None:
        cursor = widget.cursor().pos()
        screen = QApplication.screenAt(cursor) or widget.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        near = (
            widget.x() - 100 <= cursor.x() <= widget.x() + widget.width() + 100
            and widget.y() - 100 <= cursor.y() <= widget.y() + widget.height() + 100
        )
        if near:
            self.target = None
            return
        max_x = geometry.right() - widget.width() + 1
        target_x = max(geometry.left(), min(max_x, cursor.x() - widget.width() / 2))
        self.target = (target_x, float(widget.y()))
