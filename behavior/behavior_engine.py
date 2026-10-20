from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from awareness.context_snapshot import ContextSnapshot
from behavior.cooldown import Cooldown
from behavior.events import ContextEvent, EventType
from dialogue.local_rules import DialogueManager


@dataclass(frozen=True, slots=True)
class Reaction:
    text: str
    event_type: EventType
    category: str
    inner: bool = False


class BehaviorEngine:
    """Select one low-interruption local reply from a batch of context events."""

    _priority = {
        EventType.USER_RETURN: 100,
        EventType.LATE_NIGHT: 90,
        EventType.USER_IDLE: 80,
        EventType.APP_STAY: 50,
    }

    def __init__(self, dialogue: DialogueManager, cooldown: Cooldown) -> None:
        self.dialogue = dialogue
        self.cooldown = cooldown
        self._rng = random.Random()

    def react(
        self,
        snapshot: ContextSnapshot,
        events: Iterable[ContextEvent],
        *,
        personality: str,
    ) -> Reaction | None:
        if snapshot.is_private or snapshot.category in {"unknown", "game"}:
            return None
        candidates = sorted(
            (event for event in events if event.type in self._priority),
            key=lambda event: self._priority[event.type],
            reverse=True,
        )
        for event in candidates:
            context_key = f"{event.type.value}:{snapshot.category}"
            if not self.cooldown.can_fire(context_key, now=event.occurred_at):
                continue
            line = None
            # Half of the context lines mention the page the user is on, so the
            # pet reacts to the actual video/document instead of only its kind.
            if event.type is EventType.APP_STAY and self._rng.random() < 0.55:
                line = self.dialogue.pick_topic(
                    snapshot.category,
                    snapshot.window_title,
                    personality=personality,
                )
            if not line:
                line = self.dialogue.pick_for_event(
                    event.type,
                    snapshot.category,
                    personality=personality,
                )
            if not line:
                continue
            self.cooldown.record(context_key, now=event.occurred_at)
            return Reaction(
                text=line,
                event_type=event.type,
                category=snapshot.category,
            )
        return None
