from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..backends.mistral_api import MistralAPIBackend

from .registry import SkillRegistry


def _text_from(input_data: Any, key: str = "text") -> str:
    if isinstance(input_data, str):
        return input_data
    if isinstance(input_data, dict):
        return input_data.get(key) or input_data.get("content") or str(input_data)
    return str(input_data)


async def _stream(
    messages: list[dict], backend: "MistralAPIBackend"
) -> AsyncIterator[dict[str, Any]]:
    started = time.perf_counter()
    parts: list[str] = []
    seq = 0
    async for token in backend.stream(messages):
        parts.append(token)
        yield {"type": "stream", "delta": token, "seq": seq, "done": False}
        seq += 1
    ms = int((time.perf_counter() - started) * 1000)
    yield {"type": "result", "ok": True, "output": {"text": "".join(parts)}, "ms": ms}


def register_mistral_skills(
    registry: SkillRegistry,
    backend: "MistralAPIBackend",
    only: set[str] | None = None,
) -> None:
    """Register Mistral-powered skills. Pass `only` to limit which ones are registered."""

    # ------------------------------------------------------------------
    # code — generate code from a description
    # input: "make a flask hello world" or {"prompt": "...", "language": "python"}
    # ------------------------------------------------------------------
    async def code(input_data: Any) -> AsyncIterator[dict[str, Any]]:
        if isinstance(input_data, dict):
            prompt = input_data.get("prompt") or input_data.get("text") or str(input_data)
            language = input_data.get("language", "")
        else:
            prompt = str(input_data)
            language = ""

        lang_hint = f" in {language}" if language else ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a coding assistant. Write clean, working code. "
                    "Include only the code and brief inline comments where helpful."
                ),
            },
            {"role": "user", "content": f"Write code{lang_hint} for: {prompt}"},
        ]
        async for event in _stream(messages, backend):
            yield event

    if only is None or "code" in only:
        registry.register("code", code, description="Generate code from a description", streaming=True)

    # ------------------------------------------------------------------
    # ask — general Q&A
    # input: any question as a string
    # ------------------------------------------------------------------
    async def ask(input_data: Any) -> AsyncIterator[dict[str, Any]]:
        question = _text_from(input_data)
        messages = [{"role": "user", "content": question}]
        async for event in _stream(messages, backend):
            yield event

    if only is None or "ask" in only:
        registry.register("ask", ask, description="Ask any question (general Q&A)", streaming=True)

    # ------------------------------------------------------------------
    # translate — translate text to a target language
    # input: "hello world" (translates to English) or
    #        {"text": "bonjour", "to": "english"}
    # ------------------------------------------------------------------
    async def translate(input_data: Any) -> AsyncIterator[dict[str, Any]]:
        if isinstance(input_data, dict):
            text = input_data.get("text") or input_data.get("content") or ""
            target = input_data.get("to") or input_data.get("language") or "English"
        else:
            text = str(input_data)
            target = "English"

        messages = [
            {
                "role": "system",
                "content": "You are a translator. Provide only the translated text, no explanations.",
            },
            {"role": "user", "content": f"Translate to {target}:\n\n{text}"},
        ]
        async for event in _stream(messages, backend):
            yield event

    if only is None or "translate" in only:
        registry.register(
            "translate", translate, description="Translate text to a target language", streaming=True
        )

    # ------------------------------------------------------------------
    # explain — explain code or a concept
    # input: code snippet or concept as a string
    # ------------------------------------------------------------------
    async def explain(input_data: Any) -> AsyncIterator[dict[str, Any]]:
        content = _text_from(input_data)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful teacher. Explain clearly and concisely. "
                    "If given code, explain what it does step by step."
                ),
            },
            {"role": "user", "content": f"Explain:\n\n{content}"},
        ]
        async for event in _stream(messages, backend):
            yield event

    if only is None or "explain" in only:
        registry.register(
            "explain", explain, description="Explain code or a concept", streaming=True
        )
