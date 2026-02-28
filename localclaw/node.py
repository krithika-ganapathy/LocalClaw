from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .discovery_mdns import MDNSDiscovery
from .peer_store import PeerStore
from .protocol import make_message
from .router import CapabilityCallback, DelegateCallback, Router, TaskCallback, TransferCallback
from .transport_tcp import PeerConnection, TCPTransport


class AgentNode:
    def __init__(
        self,
        config: AgentConfig,
        *,
        on_task: TaskCallback | None = None,
        on_capability_query: CapabilityCallback | None = None,
        on_transfer_received: TransferCallback | None = None,
        on_delegate: DelegateCallback | None = None,
    ) -> None:
        self.config = config
        self.peer_store = PeerStore()
        self.router = Router(
            config.agent_id or "",
            self.peer_store,
            on_task=on_task,
            on_capability_query=on_capability_query,
            on_transfer_received=on_transfer_received,
            on_delegate=on_delegate,
            max_transfer_bytes=config.max_transfer_bytes,
            stream_queue_size=config.stream_queue_size,
        )
        self.transport = TCPTransport(on_message=self.router.handle_message)
        self.router.attach_transport(self.transport)

        self.discovery = MDNSDiscovery(config, self.peer_store)

        self._maintenance_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(
        self,
        *,
        with_discovery: bool = True,
        with_transport: bool = True,
        discovery_advertise: bool = True,
        discovery_browse: bool = True,
    ) -> None:
        if self._started:
            return

        if with_transport:
            await self.transport.start(host=self.config.bind_host, port=self.config.agent_port)
        if with_discovery:
            await self.discovery.start(advertise=discovery_advertise, browse=discovery_browse)

        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return

        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None

        await self.discovery.stop()
        await self.transport.stop()
        self._started = False

    async def wait_for_peer(self, peer_id: str, timeout: float = 5.0) -> None:
        start = time.time()
        while time.time() - start <= timeout:
            self.peer_store.expire_stale()
            if self.peer_store.get(peer_id) is not None:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Peer '{peer_id}' was not discovered within {timeout:.1f}s")

    async def ping(self, peer_id: str, timeout: float = 5.0) -> dict[str, Any]:
        return await self.router.ping(peer_id, timeout=timeout)

    async def send_task(
        self,
        peer_id: str,
        skill: str,
        input_data: Any,
        *,
        priority: int = 0,
        trace: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        handle = await self.router.send_task(
            peer_id,
            skill,
            input_data,
            priority=priority,
            trace=trace,
            timeout=timeout,
        )
        return await asyncio.wait_for(handle.result, timeout=timeout)

    async def send_task_streaming(
        self,
        peer_id: str,
        skill: str,
        input_data: Any,
        *,
        priority: int = 0,
        trace: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> tuple[str, asyncio.Future[dict[str, Any]]]:
        handle = await self.router.send_task(
            peer_id,
            skill,
            input_data,
            priority=priority,
            trace=trace,
            timeout=timeout,
        )
        return handle.task_id, handle.result

    async def send_file(self, peer_id: str, path: Path, timeout: float = 60.0) -> dict[str, Any]:
        return await self.router.send_file(peer_id, path, timeout=timeout)

    async def capability_query(self, peer_id: str, timeout: float = 10.0) -> dict[str, Any]:
        return await self.router.capability_query(peer_id=peer_id, timeout=timeout)

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(max(0.5, self.config.heartbeat_interval_s / 2.0))
            self.peer_store.expire_stale()

            now = time.time()
            for conn in self.transport.all_connections():
                if conn.peer_id is None or conn.is_closing():
                    continue

                silent_for = now - conn.last_activity
                if silent_for >= self.config.heartbeat_timeout_s:
                    await conn.close()
                    continue

                if silent_for >= self.config.heartbeat_interval_s:
                    heartbeat = make_message("heartbeat", self.config.agent_id or "")
                    with contextlib.suppress(Exception):
                        await conn.send_json(heartbeat)
