from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..backends.mistral_api import MistralAPIBackend

from .registry import SkillRegistry


def register_summarize(registry: SkillRegistry, backend: "MistralAPIBackend") -> None:
    async def summarize(input_data: Any) -> AsyncIterator[dict[str, Any]]:
        if isinstance(input_data, str):
            text = input_data
        elif isinstance(input_data, dict):
            text = input_data.get("text") or input_data.get("content") or str(input_data)
        else:
            text = str(input_data)

        messages = [{"role": "user", "content": f"Summarize concisely:\n\n{text}"}]
        started = time.perf_counter()
        parts: list[str] = []
        seq = 0

        async for token in backend.stream(messages):
            parts.append(token)
            yield {"type": "stream", "delta": token, "seq": seq, "done": False}
            seq += 1

        ms = int((time.perf_counter() - started) * 1000)
        yield {"type": "result", "ok": True, "output": {"text": "".join(parts)}, "ms": ms}

    registry.register(
        "summarize",
        summarize,
        description="Summarize text using Mistral AI",
        streaming=True,
    )
