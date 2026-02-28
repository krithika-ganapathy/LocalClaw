from __future__ import annotations

import asyncio

import pytest

from localclaw.config import AgentConfig
from localclaw.peer_store import PeerRecord, PeerStore
from localclaw.portal.service import PortalService


class FakeNode:
    def __init__(self) -> None:
        self.peer_store = PeerStore()
        self.started = False
        self.start_kwargs: dict | None = None
        self.ping_errors: dict[str, Exception] = {}

    async def start(self, **kwargs) -> None:
        self.started = True
        self.start_kwargs = kwargs

    async def stop(self) -> None:
        self.started = False

    async def ping(self, peer_id: str, timeout: float = 0) -> dict:
        if peer_id in self.ping_errors:
            raise self.ping_errors[peer_id]
        await asyncio.sleep(0)
        return {"type": "heartbeat", "from": peer_id}


@pytest.mark.asyncio
async def test_discovery_upsert_and_remove_events() -> None:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    node = FakeNode()
    service = PortalService(cfg, node=node, with_discovery=True)
    await service.start()
    assert node.start_kwargs is not None
    assert node.start_kwargs["with_transport"] is False
    assert node.start_kwargs["discovery_advertise"] is False
    assert node.start_kwargs["discovery_browse"] is True

    queue = service.subscribe_events(include_snapshot=False)
    try:
        node.peer_store.upsert(
            PeerRecord(
                peer_id="lc_a",
                name="A",
                host="127.0.0.1",
                port=4117,
                caps=["echo"],
                source="mdns",
            )
        )
        await service._sync_peers_once()
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["event"] == "peer_upsert"
        assert event["data"]["peer_id"] == "lc_a"

        node.peer_store.remove("lc_a")
        await service._sync_peers_once()
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["event"] == "peer_remove"
        assert event["data"]["peer_id"] == "lc_a"
    finally:
        service.unsubscribe_events(queue)
        await service.stop()


@pytest.mark.asyncio
async def test_manual_ping_success_and_failure_updates_state() -> None:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    node = FakeNode()
    node.peer_store.upsert(PeerRecord(peer_id="lc_b", name="B", host="127.0.0.1", port=4118))

    service = PortalService(cfg, node=node, with_discovery=False)
    await service.start()
    try:
        await service._sync_peers_once()
        success = await service.manual_ping("lc_b")
        assert success["ok"] is True

        rec = service.health.get("lc_b")
        assert rec is not None
        assert rec.reachable is True
        assert rec.consecutive_failures == 0

        node.ping_errors["lc_b"] = RuntimeError("timeout")
        failed = await service.manual_ping("lc_b")
        assert failed["ok"] is False

        rec = service.health.get("lc_b")
        assert rec is not None
        assert rec.reachable is False
        assert rec.consecutive_failures == 1
        assert rec.last_error == "timeout"
    finally:
        await service.stop()


def test_config_defaults_include_portal_values() -> None:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    assert cfg.portal_bind_host == "0.0.0.0"
    assert cfg.portal_port == 7420
    assert cfg.portal_ping_interval_s == 10.0
    assert cfg.portal_session_ttl_s == 86400
