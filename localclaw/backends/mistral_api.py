from __future__ import annotations

import os
from collections.abc import AsyncIterator


class MistralAPIBackend:
    """Mistral API backend using the official mistralai SDK.

    API key is read from the MISTRAL_API_KEY environment variable (or passed directly).
    Set model via config; defaults to mistral-small-latest.
    """

    def __init__(self, api_key: str | None = None, model: str = "mistral-small-latest") -> None:
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY", "")
        self.model = model
        self._client: object | None = None

    def _client_instance(self) -> object:
        if self._client is None:
            try:
                from mistralai import Mistral  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError(
                    "mistralai package is not installed. Run: pip install mistralai"
                ) from exc
            if not self.api_key:
                raise RuntimeError(
                    "MISTRAL_API_KEY is not set. "
                    "Add it to your environment or a .env file before running."
                )
            self._client = Mistral(api_key=self.api_key)
        return self._client

    async def complete(self, messages: list[dict]) -> str:
        client = self._client_instance()
        response = await client.chat.complete_async(  # type: ignore[attr-defined]
            model=self.model,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        client = self._client_instance()
        async with await client.chat.stream_async(  # type: ignore[attr-defined]
            model=self.model,
            messages=messages,
        ) as stream_response:
            async for event in stream_response:
                choices = event.data.choices
                if not choices:
                    continue
                delta = choices[0].delta.content
                if delta:
                    yield delta
