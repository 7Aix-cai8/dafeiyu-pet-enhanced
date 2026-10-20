"""Read today's DeepSeek Harness token usage, its RMB cost and the current phase.

The pet is a pure local helper here: it reads DSH's own session projection cache
(plain JSON under ``$DSH_HOME/storages``) and never talks to the network.

Peak / off-peak follows the current DeepSeek API policy (Beijing time): weekday
09:00-12:00 and 14:00-18:00 are peak, everything else — weekends included — is
off-peak.  Public holidays are not looked up, so a weekday holiday is still
priced as peak.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ¥ per 1M tokens expressed as (cache-hit input, cache-miss input, output).
_PRICING = {
    "flash": {"peak": (0.04, 2.0, 8.0), "offpeak": (0.02, 1.0, 4.0)},
    "pro": {"peak": (0.30, 9.0, 27.0), "offpeak": (0.15, 4.5, 13.5)},
}

# Weekday peak windows in minutes since midnight (Beijing time).
_PEAK_WINDOWS = ((9 * 60, 12 * 60), (14 * 60, 18 * 60))


def _dsh_home() -> Path:
    raw = os.environ.get("DSH_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".dsh"


def _today_start_ms(now: float | None = None) -> int:
    moment = datetime.fromtimestamp(now if now is not None else time.time())
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_offpeak(now: float | None = None) -> bool:
    moment = datetime.fromtimestamp(now if now is not None else time.time())
    if moment.weekday() >= 5:
        return True
    minutes = moment.hour * 60 + moment.minute
    return not any(start <= minutes < end for start, end in _PEAK_WINDOWS)


def _rates(model: str | None, offpeak: bool) -> tuple[float, float, float]:
    family = "flash" if "flash" in (model or "").lower() else "pro"
    return _PRICING[family]["offpeak" if offpeak else "peak"]


def _model_of(rows: dict) -> str | None:
    selection = rows.get("modelSelection") or {}
    value = selection.get("val") if isinstance(selection, dict) else None
    if not isinstance(value, dict):
        return None
    last = value.get("lastUsed")
    if not isinstance(last, dict):
        return None
    model = last.get("model")
    return model if isinstance(model, str) else None


@dataclass(frozen=True)
class DshUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    sessions: int = 0
    offpeak: bool = False
    available: bool = False
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def phase(self) -> str:
        return "谷时" if self.offpeak else "峰时"


def collect(now: float | None = None) -> DshUsage:
    start = _today_start_ms(now)
    sessions_dir = _dsh_home() / "storages" / "session_projcache" / "sessions"
    offpeak = _is_offpeak(now)
    if not sessions_dir.is_dir():
        return DshUsage(offpeak=offpeak, available=False)

    input_tokens = output_tokens = cache_read = cache_write = 0
    cost = 0.0
    count = 0
    try:
        entries = list(sessions_dir.glob("*.json"))
    except OSError:
        entries = []
    for entry in entries:
        payload = _load_json(entry)
        if not isinstance(payload, dict):
            continue
        record = payload.get("record")
        if not isinstance(record, dict):
            continue
        identity = record.get("identity") or {}
        rows = record.get("rows") or {}
        if not isinstance(rows, dict):
            continue
        meta = rows.get("sessionListMetadata") or {}
        meta_val = meta.get("val") if isinstance(meta, dict) else None
        created = _num(identity.get("createdAt"))
        last_prompt = _num(meta_val.get("lastPromptAt")) if isinstance(meta_val, dict) else 0
        if max(created, last_prompt) < start:
            continue
        usage_row = rows.get("tokenUsage") or {}
        usage_val = usage_row.get("val") if isinstance(usage_row, dict) else None
        totals = usage_val.get("totals") if isinstance(usage_val, dict) else None
        if not isinstance(totals, dict):
            continue

        session_input = _num(totals.get("uncachedInputTokens"))
        session_output = _num(totals.get("outputTokens"))
        session_cache_read = _num(totals.get("cacheReadTokens"))
        session_cache_write = _num(totals.get("cacheWriteTokens"))
        input_tokens += session_input
        output_tokens += session_output
        cache_read += session_cache_read
        cache_write += session_cache_write
        count += 1

        hit_rate, miss_rate, output_rate = _rates(_model_of(rows), offpeak)
        cost += (
            session_cache_read / 1_000_000 * hit_rate
            + session_input / 1_000_000 * miss_rate
            + session_output / 1_000_000 * output_rate
        )

    return DshUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        sessions=count,
        offpeak=offpeak,
        available=True,
        cost=cost,
    )


def format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_cost(value: float) -> str:
    if value >= 100:
        return f"¥{value:.0f}"
    if value >= 10:
        return f"¥{value:.1f}"
    return f"¥{value:.2f}"


def describe(now: float | None = None) -> str | None:
    """One compact line for the pet bubble, or None when DSH data is absent."""
    usage = collect(now)
    if not usage.available:
        return None
    phase = usage.phase
    if usage.sessions == 0:
        return f"今天还没喂我 token · {phase}"
    return (
        f"今日 {format_tokens(usage.total_tokens)} tok（{format_cost(usage.cost)}）"
        f" · {usage.sessions} 会话 · {phase}"
    )
