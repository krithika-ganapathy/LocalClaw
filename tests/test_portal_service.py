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
        self.pair_pin = "654321"
        self.is_paired = False

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

    async def send_task(self, peer_id: str, skill: str, input_data, *, timeout: float = 0, **kwargs) -> dict:
        _ = timeout
        _ = kwargs
        requester = (input_data or {}).get("requester_id") if isinstance(input_data, dict) else None
        if skill == "localclaw.pair.status":
            if self.is_paired and requester:
                return {"ok": True, "output": {"ok": True, "paired": True, "pair_required": False}}
            return {"ok": True, "output": {"ok": True, "paired": False, "pair_required": True}}
        if skill == "localclaw.pair.start":
            return {"ok": True, "output": {"ok": True, "pair_required": True, "already_paired": False}}
        if skill == "localclaw.pair.verify":
            pin = (input_data or {}).get("pin")
            if pin == self.pair_pin:
                self.is_paired = True
                return {"ok": True, "output": {"ok": True, "paired": True}}
            return {"ok": True, "output": {"ok": False, "error": "invalid_pin"}}
        if skill == "ask":
            return {"ok": True, "output": {"text": f"reply:{input_data}"}}
        return {"ok": False, "error": {"code": "unknown_skill", "message": skill}}


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


@pytest.mark.asyncio
async def test_chat_pairing_and_message_flow() -> None:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    node = FakeNode()
    node.peer_store.upsert(PeerRecord(peer_id="lc_c", name="C", host="127.0.0.1", port=4119, caps=["ask"]))

    service = PortalService(cfg, node=node, with_discovery=False)
    await service.start()
    try:
        await service._sync_peers_once()
        state = await service.chat_state("lc_c")
        assert state["pairing"]["pair_required"] is True

        start = await service.start_pairing("lc_c")
        assert start["ok"] is True
        assert start["pair_required"] is True

        blocked = await service.send_chat("lc_c", message="hello", skill="ask")
        assert blocked["ok"] is False
        assert blocked["error"] == "pairing_required"

        bad = await service.verify_pairing("lc_c", "000000")
        assert bad["ok"] is False

        good = await service.verify_pairing("lc_c", node.pair_pin)
        assert good["ok"] is True

        sent = await service.send_chat("lc_c", message="hello", skill="ask")
        assert sent["ok"] is True
        assert sent["skill"] == "ask"
        assert sent["message"] == "reply:hello"
    finally:
        await service.stop()
