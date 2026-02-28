from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from localclaw.config import AgentConfig
from localclaw.node import AgentNode

from .models import PeerHealthStore


PAIR_START_SKILL = "localclaw.pair.start"
PAIR_VERIFY_SKILL = "localclaw.pair.verify"
PAIR_STATUS_SKILL = "localclaw.pair.status"


class _EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self, queue_size: int = 200) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)


class PortalService:
    def __init__(
        self,
        config: AgentConfig,
        *,
        node: AgentNode | None = None,
        ping_interval_s: float | None = None,
        ping_timeout_s: float = 4.0,
        max_concurrent_pings: int = 16,
        with_discovery: bool = True,
    ) -> None:
        self.config = config
        self.node = node or AgentNode(config)
        self.ping_interval_s = float(ping_interval_s or config.portal_ping_interval_s)
        self.ping_timeout_s = ping_timeout_s
        self.max_concurrent_pings = max_concurrent_pings
        self.with_discovery = with_discovery

        self.health = PeerHealthStore()
        self.events = _EventBus()

        self._next_ping_due: dict[str, float] = {}
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._inflight: set[str] = set()
        self._ping_semaphore = asyncio.Semaphore(self.max_concurrent_pings)

    async def chat_state(self, peer_id: str) -> dict[str, Any]:
        peer = self.health.get(peer_id)
        if peer is None:
            raise KeyError(peer_id)

        pairing = await self._pair_status(peer_id)
        return {
            "peer_id": peer_id,
            "peer_name": peer.name,
            "skills": list(peer.caps),
            "default_skill": self._default_chat_skill(peer.caps),
            "pairing": pairing,
        }

    async def start_pairing(self, peer_id: str) -> dict[str, Any]:
        if self.health.get(peer_id) is None:
            raise KeyError(peer_id)
        result = await self._run_pair_task(
            peer_id,
            PAIR_START_SKILL,
            {
                "requester_id": self.config.agent_id,
                "requester_name": self.config.agent_name,
            },
        )
        self._publish("pairing_update", {"peer_id": peer_id, **result})
        return result

    async def verify_pairing(self, peer_id: str, pin: str) -> dict[str, Any]:
        if self.health.get(peer_id) is None:
            raise KeyError(peer_id)
        result = await self._run_pair_task(
            peer_id,
            PAIR_VERIFY_SKILL,
            {
                "requester_id": self.config.agent_id,
                "requester_name": self.config.agent_name,
                "pin": pin,
            },
        )
        self._publish("pairing_update", {"peer_id": peer_id, **result})
        return result

    async def send_chat(
        self,
        peer_id: str,
        *,
        message: str,
        skill: str | None = None,
        timeout_s: float = 45.0,
    ) -> dict[str, Any]:
        peer = self.health.get(peer_id)
        if peer is None:
            raise KeyError(peer_id)

        pairing = await self._pair_status(peer_id)
        if pairing.get("pair_required") and not pairing.get("paired"):
            return {
                "ok": False,
                "error": "pairing_required",
                "message": "Pairing is required before chat.",
                "pairing": pairing,
            }

        selected_skill = (skill or "").strip() or self._default_chat_skill(peer.caps)
        if selected_skill not in peer.caps:
            return {
                "ok": False,
                "error": "skill_not_supported",
                "message": f"Peer does not advertise skill '{selected_skill}'.",
                "pairing": pairing,
            }

        response = await self.node.send_task(
            peer_id,
            selected_skill,
            message,
            timeout=timeout_s,
        )
        if not bool(response.get("ok", False)):
            err = response.get("error") or {}
            return {
                "ok": False,
                "error": str(err.get("code") or "task_failed"),
                "message": str(err.get("message") or "Task failed"),
                "raw": response,
                "pairing": pairing,
            }

        output = response.get("output")
        if isinstance(output, dict) and "text" in output:
            rendered = str(output.get("text", ""))
        else:
            rendered = str(output)
        return {
            "ok": True,
            "peer_id": peer_id,
            "skill": selected_skill,
            "message": rendered,
            "raw_output": output,
            "ms": int(response.get("ms", 0)),
            "pairing": pairing,
        }

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return

        await self.node.start(
            with_discovery=self.with_discovery,
            with_transport=False,
            discovery_advertise=False,
            discovery_browse=self.with_discovery,
        )
        self._running = True
        await self._sync_peers_once()
        self._tasks = [
            asyncio.create_task(self._peer_sync_loop()),
            asyncio.create_task(self._ping_loop()),
        ]

    async def stop(self) -> None:
        if not self._running:
            return

        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        await self.node.stop()
        self._running = False

    def snapshot(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.health.all()]

    def subscribe_events(self, queue_size: int = 200, *, include_snapshot: bool = True) -> asyncio.Queue[dict[str, Any]]:
        queue = self.events.subscribe(queue_size=queue_size)
        if include_snapshot:
            queue.put_nowait(self._make_event("snapshot", {"peers": self.snapshot()}))
        return queue

    def unsubscribe_events(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.events.unsubscribe(queue)

    async def manual_ping(self, peer_id: str) -> dict[str, Any]:
        if self.health.get(peer_id) is None:
            raise KeyError(peer_id)
        return await self._ping_peer(peer_id, reason="manual")

    def meta(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "agent_id": self.config.agent_id,
            "agent_name": self.config.agent_name,
            "with_discovery": self.with_discovery,
            "ping_interval_s": self.ping_interval_s,
            "ping_timeout_s": self.ping_timeout_s,
            "max_concurrent_pings": self.max_concurrent_pings,
            "peer_count": len(self.snapshot()),
        }

    async def _peer_sync_loop(self) -> None:
        while True:
            await self._sync_peers_once()
            await asyncio.sleep(0.5)

    async def _sync_peers_once(self) -> None:
        now = time.time()
        seen: set[str] = set()
        for peer in self.node.peer_store.all():
            seen.add(peer.peer_id)
            record, changed = self.health.upsert_discovered(peer)
            if record.peer_id not in self._next_ping_due:
                self._next_ping_due[record.peer_id] = now + random.uniform(0.0, 1.0)
            if changed:
                self._publish("peer_upsert", record.to_dict())

        for peer_id in list(self.health.ids()):
            if peer_id in seen:
                continue
            removed = self.health.remove(peer_id)
            self._next_ping_due.pop(peer_id, None)
            if removed is not None:
                self._publish("peer_remove", {"peer_id": peer_id})

    async def _ping_loop(self) -> None:
        while True:
            now = time.time()
            due = [
                peer_id
                for peer_id, due_at in self._next_ping_due.items()
                if due_at <= now and self.health.get(peer_id) is not None
            ]
            random.shuffle(due)
            if not due:
                await asyncio.sleep(0.2)
                continue

            tasks = [asyncio.create_task(self._scheduled_ping(peer_id)) for peer_id in due]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _scheduled_ping(self, peer_id: str) -> None:
        async with self._ping_semaphore:
            await self._ping_peer(peer_id, reason="scheduled")

    async def _ping_peer(self, peer_id: str, *, reason: str) -> dict[str, Any]:
        if peer_id in self._inflight:
            return {"peer_id": peer_id, "ok": False, "reason": reason, "error": "ping_in_progress"}

        self._inflight.add(peer_id)
        started_at = time.time()
        ok = False
        error: str | None = None
        rtt_ms: int | None = None
        try:
            begin = time.perf_counter()
            await self.node.ping(peer_id, timeout=self.ping_timeout_s)
            rtt_ms = int((time.perf_counter() - begin) * 1000)
            ok = True
        except Exception as exc:
            error = str(exc)
        finally:
            next_due = time.time() + max(1.0, self.ping_interval_s + random.uniform(-1.0, 1.0))
            self._next_ping_due[peer_id] = next_due
            self._inflight.discard(peer_id)

        rec = self.health.mark_ping_result(
            peer_id,
            reachable=ok,
            rtt_ms=rtt_ms,
            error=error,
            at=started_at,
        )

        payload = {
            "peer_id": peer_id,
            "ok": ok,
            "reason": reason,
            "rtt_ms": rtt_ms,
            "error": error,
            "at": started_at,
            "state": rec.to_dict() if rec is not None else None,
        }
        self._publish("ping_result", payload)
        return payload

    async def _pair_status(self, peer_id: str) -> dict[str, Any]:
        try:
            result = await self._run_pair_task(
                peer_id,
                PAIR_STATUS_SKILL,
                {
                    "requester_id": self.config.agent_id,
                    "requester_name": self.config.agent_name,
                },
            )
        except Exception:
            return {"paired": True, "pair_required": False, "compat_mode": True}

        if result.get("ok") is False and result.get("error") == "unknown_skill":
            return {"paired": True, "pair_required": False, "compat_mode": True}
        return {
            "paired": bool(result.get("paired", False)),
            "pair_required": bool(result.get("pair_required", False)),
            "already_paired": bool(result.get("already_paired", False)),
            "expires_at": result.get("expires_at"),
        }

    async def _run_pair_task(self, peer_id: str, skill: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.node.send_task(peer_id, skill, payload, timeout=15.0)
        if not bool(response.get("ok", False)):
            err = response.get("error") or {}
            return {
                "ok": False,
                "error": str(err.get("code") or "task_failed"),
                "message": str(err.get("message") or "Task failed"),
            }
        output = response.get("output")
        if isinstance(output, dict):
            return output
        return {"ok": False, "error": "invalid_pair_payload", "message": "Pairing payload was invalid"}

    @staticmethod
    def _default_chat_skill(skills: list[str]) -> str:
        if "ask" in skills:
            return "ask"
        if "summarize" in skills:
            return "summarize"
        if "explain" in skills:
            return "explain"
        if "echo" in skills:
            return "echo"
        if skills:
            return skills[0]
        return "echo"

    def _publish(self, event_name: str, payload: dict[str, Any]) -> None:
        self.events.publish(self._make_event(event_name, payload))

    @staticmethod
    def _make_event(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": event_name,
            "ts": int(time.time() * 1000),
            "data": payload,
        }
