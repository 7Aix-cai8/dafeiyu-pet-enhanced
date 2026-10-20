from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    timestamp: float
    process_name: str
    window_title: str
    pid: int
    idle_seconds: float
    is_fullscreen: bool
    category: str
    window_handle: int = 0
    is_private: bool = False

    @property
    def identity(self) -> tuple[str, str, int]:
        """A short-lived in-memory identity used only for dwell detection.

        The classified category — not the window title — participates here: a
        title that changes on its own (video progress, unread counts, tab
        switches) must not keep resetting the dwell timer and silence every
        context line.
        """
        return (self.process_name.casefold().strip(), self.category, self.pid)
