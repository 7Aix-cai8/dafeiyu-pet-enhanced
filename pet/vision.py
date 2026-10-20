"""Background screenshot -> change detection -> DeepSeek vision -> one comment.

Capturing the screen and diffing it against the previous frame costs nothing.
Only when the picture has actually changed does the pet pay for a model call, so
a static desktop produces no token usage at all.
"""

from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover
    ImageGrab = None

_API_URL = "https://api.deepseek.com/chat/completions"
# Only deepseek-flash actually accepts images: v4-pro silently drops them, so a
# vision call to it costs a request and answers from imagination.
VISION_MODEL = "deepseek-flash"

DEFAULT_PROMPT = (
    "你是「大肥鱼」，一条住在用户桌面上的鲸鱼娘，就像坐在他旁边一起看屏幕的朋友："
    "俏皮、嘴甜、爱接梗，损人但从不刻薄。说话像平时闲聊，不是汇报，也不是解说。\n"
    "先在心里扫一眼他的屏幕：这是单帧截图，但屏幕上的东西很可能正在播放、正在操作，"
    "别当它停住了，更不要念出「正在播放 XX」这种解说腔。\n"
    "然后只挑一个最好玩、最有说头的点，随口吐槽一句：\n"
    "- 一个细节就够了：某个数字、某句弹幕、某件装备、某个战绩或 ID、某个操作、某个标题、"
    "甚至界面上一个奇怪的字；\n"
    "- 顺着这个点聊下去，不用把画面说完，也不用交代「你在看什么」；\n"
    "- 像朋友在旁边搭话：可以有情绪、可以损他、可以接梗、可以反问一句；\n"
    "- 分清是谁的画面：别人的直播或视频就说「他 / 这主播」，他本人在玩才说「你」，"
    "拿不准就用「这波」；\n"
    "- 画面上的人和物能用大家熟知的外号就叫外号，别死抠原文；拿不准就照原文说；\n"
    "- 只说画面里真实存在的东西，没有的就别提，也不要硬编。\n"
    "除了吐槽，也可以顺口带一句你联想到的东西：对比、经验、冷知识，"
    "或者你对这事的看法——像真人聊天时突然想到什么就说什么。\n"
    "长度：平时 40 字以内；如果画面上槽点特别多，可以放宽到 70 字左右，"
    "把两三个点串起来一口气说完。\n"
    "只输出一句中文，口语；不要引号、不要列表、不要解释、不要提「截图」「图片」。"
)


@dataclass(slots=True)
class VisionConfig:
    enabled: bool = True
    poll_seconds: float = 10.0
    prompt: str = DEFAULT_PROMPT


def credentials_api_key() -> str | None:
    """Read DEEPSEEK_API_KEY out of the harness credentials file."""

    path = Path.home() / ".dsh" / ".credentials.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^\s*DEEPSEEK_API_KEY:\s*([^\s#]+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


class VisionCommenter:
    """Free screen diffing, paid model calls only when the picture changes."""

    def __init__(
        self,
        api_key: str | None,
        *,
        prompt: str = DEFAULT_PROMPT,
        min_call_gap: float = 30.0,
        change_threshold: float = 9.0,
        max_width: int = 1568,
        daily_call_cap: int = 500,
        memory_path: Path | None = None,
        memory_size: int = 50,
    ) -> None:
        self.api_key = api_key
        self.prompt = prompt
        self.min_call_gap = max(5.0, float(min_call_gap))
        self.change_threshold = max(1.0, float(change_threshold))
        self.max_width = max(320, int(max_width))
        self.daily_call_cap = max(10, int(daily_call_cap))

        self._busy = False
        self._lock = threading.Lock()
        self._signature: bytes | None = None
        self._last_call = 0.0
        self._day = ""
        self._calls_today = 0
        self.last_diff = 0.0

        self.memory_path = Path(memory_path) if memory_path else None
        self._comments: deque[str] = deque(maxlen=max(10, int(memory_size)))
        self._timeline: deque[tuple[float, str, str]] = deque(maxlen=14)
        self._last_label = ""
        self._load_memory()

    @property
    def available(self) -> bool:
        return bool(self.api_key) and ImageGrab is not None

    # ---- public ------------------------------------------------------------

    def poll(self, on_comment) -> bool:
        """Capture in the background; call the model only if the frame changed."""

        if not self.available:
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(
            target=self._worker,
            args=(on_comment,),
            name="pet-vision",
            daemon=True,
        ).start()
        return True

    # ---- memory ------------------------------------------------------------

    def observe(self, category: str, title: str, now: float | None = None) -> None:
        """Record what is on screen so the pet can compare with earlier."""

        label = (title or category or "").strip()
        if not label or label == self._last_label:
            return
        self._last_label = label
        moment = time.time() if now is None else now
        self._timeline.append((moment, time.strftime("%H:%M", time.localtime(moment)), label[:26]))
        self._prune_timeline(moment)

    def _prune_timeline(self, now: float) -> None:
        while self._timeline and now - self._timeline[0][0] > 600.0:
            self._timeline.popleft()

    @property
    def memory_count(self) -> int:
        return len(self._comments)

    def recent_comments(self, count: int = 3) -> list[str]:
        """Newest first, for the small history card."""

        items = list(self._comments)
        return list(reversed(items[-max(1, int(count)):]))

    def _load_memory(self) -> None:
        if self.memory_path is None:
            return
        try:
            payload = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        items = payload.get("comments") if isinstance(payload, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item.strip():
                    self._comments.append(item.strip())

    def _save_memory(self) -> None:
        if self.memory_path is None:
            return
        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.memory_path.write_text(
                json.dumps({"comments": list(self._comments)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _build_prompt(self) -> str:
        """Base rules plus a light memory of past comments and screen activity."""

        parts = [self.prompt]
        if self._comments:
            last = self._comments[-1]
            parts.append(
                "\n【你上一条刚说过】\n「"
                + last[:40]
                + "」\n这次必须换一个角度或换一个细节，不要重复同样的意思，也不要换汤不换药；"
                "实在没有新的可说的，就干脆换个话题损他一句。"
            )
        if self._comments:
            lines = [f"- {item[:26]}" for item in self._comments]
            parts.append(
                "\n【你以前在这里说过的话，按时间从早到晚】\n"
                + "\n".join(lines)
                + "\n只有当它和当前画面明显相关时才拿来接梗或对比；不相关就别提，更不要重复原句。"
            )
        if self._timeline:
            now = time.time()
            self._prune_timeline(now)
            lines = [f"- {stamp} {label}" for _at, stamp, label in self._timeline]
            parts.append(
                "\n【最近十分钟屏幕上的活动】\n"
                + "\n".join(lines)
                + "\n可以用来说明他刚换了什么，比如突然从游戏切到文档；没有对比价值就别提。"
            )
        return "\n".join(parts)

    # ---- worker ------------------------------------------------------------

    def _worker(self, on_comment) -> None:
        text = None
        try:
            image = self._grab()
            if image is not None and self._changed(image):
                if self._may_call():
                    text = self._ask(image)
        except Exception:
            text = None
        finally:
            with self._lock:
                self._busy = False
        if text:
            self._comments.append(text)
            self._save_memory()
            try:
                on_comment(text)
            except Exception:
                pass

    def _grab(self):
        try:
            image = ImageGrab.grab()
        except Exception:
            return None
        if image.width > self.max_width:
            ratio = self.max_width / float(image.width)
            image = image.resize(
                (self.max_width, max(1, int(image.height * ratio))),
                resample=3,
            )
        return image

    def _changed(self, image) -> bool:
        """Cheap grayscale diff; no network, no tokens."""

        try:
            small = image.convert("L").resize((48, 27))
            signature = small.tobytes()
        except Exception:
            return False
        previous = self._signature
        self._signature = signature
        if previous is None or len(previous) != len(signature):
            self.last_diff = 99.0
            return True
        diff = sum(abs(a - b) for a, b in zip(signature, previous)) / len(signature)
        self.last_diff = diff
        return diff >= self.change_threshold

    def _may_call(self) -> bool:
        now = time.monotonic()
        if now - self._last_call < self.min_call_gap:
            return False
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            self._day = day
            self._calls_today = 0
        if self._calls_today >= self.daily_call_cap:
            return False
        self._last_call = now
        self._calls_today += 1
        return True

    def _ask(self, image) -> str | None:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._build_prompt()},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + encoded},
                        },
                    ],
                }
            ],
            # Thinking mode would spend the whole budget on reasoning and return
            # an empty answer, so it is explicitly disabled for these quick looks.
            "thinking": {"type": "disabled"},
            "max_tokens": 300,
            "temperature": 0.9,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _API_URL,
            data=body,
            headers={
                "Authorization": "Bearer " + str(self.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        return _tidy(str(message.get("content") or ""))


def _tidy(text: str) -> str | None:
    cleaned = " ".join(text.split()).strip()
    cleaned = cleaned.strip("\"'“”「」 ").strip()
    if not cleaned:
        return None
    return cleaned[:160]
