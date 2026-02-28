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
        hidden: bool = False,
    ) -> None:
        self._handlers[name] = handler
        self._meta[name] = {
            "name": name,
            "description": description,
            "streaming": streaming,
            "hidden": hidden,
        }

    def get(self, name: str) -> SkillHandler | None:
        return self._handlers.get(name)

    def names(self) -> list[str]:
        return list(self._handlers)

    def list_skills(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        if include_hidden:
            return list(self._meta.values())
        return [meta for meta in self._meta.values() if not bool(meta.get("hidden", False))]
