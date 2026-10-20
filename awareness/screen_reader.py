"""Offline screen text: screenshot -> Windows OCR -> one quotable line.

This is the opt-in "look at what is on screen" mode.  It never leaves the
machine: the capture goes to a temp PNG, Windows' own OCR reads it, and only a
short cleaned line survives.  When the OCR is too noisy the reader returns
nothing, so the pet falls back to its title-based lines instead of quoting
gibberish.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover - Pillow is a hard dependency
    ImageGrab = None

try:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
except ImportError:  # pragma: no cover - optional on non-Windows builds
    BitmapDecoder = None
    OcrEngine = None
    StorageFile = None
    FileAccessMode = None

_CJK = re.compile(r"[\u4e00-\u9fff]")
_CJK_GAP = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_SPACES = re.compile(r"\s{2,}")
_DIGITS_ONLY = re.compile(r"^[\d\W_]+$")
_URLISH = ("http", "www.", ".com", ".cn", "ctrl", "alt+", "esc")

# Characters that Windows OCR produces when it misreads a glyph; a line that
# still carries one of these is not trustworthy enough to quote.
_RARE = set("灬惴厝诖彥彦彳亍氵讠罒罓龴卩卪阝辶衤礻糹纟釒钅飠饣")


@dataclass(slots=True)
class ScreenQuote:
    text: str
    captured_at: float


def normalize(text: str) -> str:
    """Undo OCR's habit of spacing out CJK glyphs and squeeze other runs."""

    text = (text or "").strip()
    text = _CJK_GAP.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip(" ·|—-")


def is_quotable(text: str) -> bool:
    """Reject chrome, URLs, numbers and visibly garbled OCR output."""

    if not (6 <= len(text) <= 22):
        return False
    lowered = text.casefold()
    if any(marker in lowered for marker in _URLISH):
        return False
    if _DIGITS_ONLY.match(text):
        return False
    cjk = len(_CJK.findall(text))
    letters = len(re.findall(r"[A-Za-z]", text))
    if cjk + letters < 4:
        return False
    if cjk and cjk / max(1, len(text)) < 0.45:
        return False
    if cjk < 4 and letters < 6:
        return False
    if any(character in _RARE for character in text):
        return False
    return True


class ScreenTextReader:
    """Rate-limited screenshot + OCR that keeps only the best clean line."""

    def __init__(self, *, interval_seconds: float = 12.0) -> None:
        self.interval_seconds = max(4.0, float(interval_seconds))
        self._last_run = 0.0
        self._engine = None
        self._probe_failed = False

    @property
    def available(self) -> bool:
        return (
            ImageGrab is not None
            and OcrEngine is not None
            and BitmapDecoder is not None
            and StorageFile is not None
        )

    def _engine_or_none(self):
        if self._probe_failed:
            return None
        if self._engine is None:
            try:
                self._engine = OcrEngine.try_create_from_user_profile_languages()
            except Exception:
                self._probe_failed = True
                return None
            if self._engine is None:
                self._probe_failed = True
        return self._engine

    def capture(self, bbox: tuple[int, int, int, int] | None, path) -> ScreenQuote | None:
        """Return one clean screen line, or None when nothing survives."""

        if not self.available:
            return None
        engine = self._engine_or_none()
        if engine is None:
            return None
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=False) if bbox else ImageGrab.grab()
        except Exception:
            return None
        try:
            image.save(path)
        except Exception:
            return None
        try:
            lines = asyncio.run(self._recognize(engine, path))
        except Exception:
            return None
        best = self._pick(lines)
        return ScreenQuote(best, time.time()) if best else None

    async def _recognize(self, engine, path: str) -> list[str]:
        file = await StorageFile.get_file_from_path_async(path)
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return [line.text for line in result.lines]

    @staticmethod
    def _pick(lines: list[str]) -> str | None:
        scored: list[tuple[int, str]] = []
        for raw in lines:
            text = normalize(raw)
            if not is_quotable(text):
                continue
            # Prefer longer, punctuation-bearing lines: those tend to be real
            # page text rather than toolbar labels.
            score = len(text) + (2 if any(mark in text for mark in "，。！？") else 0)
            scored.append((score, text))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def run_once(self, bbox: tuple[int, int, int, int] | None, path) -> ScreenQuote | None:
        """Throttled entry point; callers may call it every poll tick."""

        now = time.time()
        if now - self._last_run < self.interval_seconds:
            return None
        self._last_run = now
        return self.capture(bbox, path)
