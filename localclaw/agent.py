from __future__ import annotations

import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .node import AgentNode
from .peer_store import PeerRecord
from .protocol import build_error
from .skills.registry import SkillRegistry


class Agent:
    """Person B runtime: wires a SkillRegistry into an AgentNode.

    Handles task dispatch, auto-delegation to peers, capability reporting,
    and file receive. Instantiate via build_agent(config) for standard use.
    """

    def __init__(self, config: AgentConfig, registry: SkillRegistry) -> None:
        self.config = config
        self.registry = registry
        self.node = AgentNode(
            config,
            on_task=self._on_task,
            on_capability_query=self._on_capability_query,
            on_transfer_received=self._on_transfer_received,
        )

    async def start(self, **kwargs: Any) -> None:
        await self.node.start(**kwargs)

    async def stop(self) -> None:
        await self.node.stop()

    # ------------------------------------------------------------------
    # Task handler (async generator so it can yield stream + result events)
    # ------------------------------------------------------------------

    async def _on_task(
        self, task_msg: dict[str, Any], peer: PeerRecord | None
    ) -> AsyncIterator[dict[str, Any]]:
        skill = task_msg.get("skill", "")
        handler = self.registry.get(skill)

        if handler is None:
            # Try to delegate to a peer that advertises this skill
            target = self._find_peer_for_skill(skill)
            if target is not None:
                try:
                    result = await self.node.send_task(
                        target.peer_id,
                        skill,
                        task_msg.get("input"),
                        trace=task_msg.get("trace"),
                        timeout=30.0,
                    )
                    yield result
                except Exception as exc:
                    yield {
                        "type": "result",
                        "ok": False,
                        "error": build_error("delegation_failed", str(exc)),
                        "ms": 0,
                    }
            else:
                yield {
                    "type": "result",
                    "ok": False,
                    "error": build_error("unknown_skill", f"Skill '{skill}' is not available"),
                    "ms": 0,
                }
            return

        started = time.perf_counter()
        try:
            handler_out = handler(task_msg.get("input"))
            if hasattr(handler_out, "__aiter__"):
                async for event in handler_out:  # type: ignore[union-attr]
                    yield event
            else:
                yield await handler_out  # type: ignore[misc]
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            yield {
                "type": "result",
                "ok": False,
                "error": build_error("skill_error", str(exc)),
                "ms": ms,
            }

    def _find_peer_for_skill(self, skill: str) -> PeerRecord | None:
        self.node.peer_store.expire_stale()
        for peer in self.node.peer_store.all():
            if peer.peer_id != self.config.agent_id and skill in peer.caps:
                return peer
        return None

    # ------------------------------------------------------------------
    # Capability query handler
    # ------------------------------------------------------------------

    async def _on_capability_query(
        self, query_msg: dict[str, Any], peer: PeerRecord | None
    ) -> dict[str, Any]:
        return {
            "skills": self.registry.list_skills(),
            "limits": {"max_transfer_bytes": self.config.max_transfer_bytes},
            "agent": {
                "name": self.config.agent_name,
                "id": self.config.agent_id,
                "model": self.config.model,
                "version": self.config.version,
            },
        }

    # ------------------------------------------------------------------
    # Transfer received handler
    # ------------------------------------------------------------------

    async def _on_transfer_received(
        self, meta: dict[str, Any], temp_path: Path, peer: PeerRecord | None
    ) -> dict[str, Any]:
        downloads = Path.home() / ".LocalClaw" / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)

        name = meta.get("name") or temp_path.name
        dest = downloads / name
        # Avoid clobbering existing files
        if dest.exists():
            dest = downloads / f"{temp_path.stem}_{int(time.time())}{temp_path.suffix}"

        temp_path.rename(dest)
        return {"saved_to": str(dest), "name": name, "bytes": meta.get("bytes")}


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from ~/.LocalClaw/.env (then ./.env) into os.environ.
    Skips keys that are already set. No external dependency required.
    """
    candidates = [Path.home() / ".LocalClaw" / ".env", Path(".env")]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def build_agent(config: AgentConfig) -> Agent:
    """Create an Agent with skills wired from config.caps and config.model."""
    _load_dotenv()

    from .skills.builtins import register_builtins
    from .skills.registry import SkillRegistry

    registry = SkillRegistry()
    register_builtins(registry)

    MISTRAL_SKILLS = {"summarize", "code", "ask", "translate", "explain"}
    wants_mistral = MISTRAL_SKILLS.intersection(config.caps)

    if wants_mistral and config.model not in ("none", "", None):
        from .backends.mistral_api import MistralAPIBackend
        from .skills.mistral_skills import register_mistral_skills
        from .skills.summarize import register_summarize

        backend = MistralAPIBackend(model=config.model)
        if "summarize" in config.caps:
            register_summarize(registry, backend)
        register_mistral_skills(registry, backend, only=wants_mistral - {"summarize"})
    elif wants_mistral:
        print(
            f"Warning: {sorted(wants_mistral)} in caps but no model configured. "
            "Run 'localclaw setup' and set a Mistral model.",
            file=sys.stderr,
        )

    return Agent(config, registry)
