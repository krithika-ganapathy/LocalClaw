from __future__ import annotations

import socket

import pytest

from localclaw.config import AgentConfig
from localclaw.node import AgentNode
from localclaw.peer_store import PeerRecord
from localclaw.portal.service import PortalService


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_portal_can_ping_agent_over_transport() -> None:
    port_b = _free_port()
    port_portal = _free_port()

    node_b = AgentNode(AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b"))

    portal_cfg = AgentConfig(agent_name="Portal", agent_port=port_portal, agent_id="lc_portal")
    portal_node = AgentNode(portal_cfg)
    portal_node.peer_store.upsert(
        PeerRecord(
            peer_id="lc_b",
            name="B",
            host="127.0.0.1",
            port=port_b,
            caps=["echo"],
            source="direct",
        )
    )
    service = PortalService(portal_cfg, node=portal_node, with_discovery=False)

    await node_b.start(with_discovery=False, with_transport=True)
    await service.start()
    try:
        result = await service.manual_ping("lc_b")
        assert result["ok"] is True

        record = service.health.get("lc_b")
        assert record is not None
        assert record.reachable is True
    finally:
        await service.stop()
        await node_b.stop()
