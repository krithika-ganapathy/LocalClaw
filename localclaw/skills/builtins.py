from __future__ import annotations

from typing import Any

from .registry import SkillRegistry


def register_builtins(registry: SkillRegistry) -> None:
    async def echo(input_data: Any) -> dict[str, Any]:
        return {"type": "result", "ok": True, "output": input_data, "ms": 0}

    async def capabilities(input_data: Any) -> dict[str, Any]:
        return {"type": "result", "ok": True, "output": registry.list_skills(), "ms": 0}

    registry.register("echo", echo, description="Returns input unchanged")
    registry.register("capabilities", capabilities, description="List available skills on this agent")
