from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

SkillHandler = Callable[[Any], Awaitable[dict[str, Any]] | AsyncIterator[dict[str, Any]]]


class SkillRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, SkillHandler] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        handler: SkillHandler,
        *,
        description: str = "",
        streaming: bool = False,
    ) -> None:
        self._handlers[name] = handler
        self._meta[name] = {"name": name, "description": description, "streaming": streaming}

    def get(self, name: str) -> SkillHandler | None:
        return self._handlers.get(name)

    def names(self) -> list[str]:
        return list(self._handlers)

    def list_skills(self) -> list[dict[str, Any]]:
        return list(self._meta.values())
