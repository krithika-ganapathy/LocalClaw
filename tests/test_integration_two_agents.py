from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from localclaw.config import AgentConfig
from localclaw.node import AgentNode
from localclaw.peer_store import PeerRecord


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wire_peers(node_a: AgentNode, node_b: AgentNode, port_a: int, port_b: int) -> None:
    node_a.peer_store.upsert(
        PeerRecord(peer_id=node_b.config.agent_id or "", name=node_b.config.agent_name, host="127.0.0.1", port=port_b)
    )
    node_b.peer_store.upsert(
        PeerRecord(peer_id=node_a.config.agent_id or "", name=node_a.config.agent_name, host="127.0.0.1", port=port_a)
    )


@pytest.mark.asyncio
async def test_echo_task_end_to_end() -> None:
    port_a = _free_port()
    port_b = _free_port()

    node_a = AgentNode(AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a"))
    node_b = AgentNode(AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b"))
    _wire_peers(node_a, node_b, port_a, port_b)

    await node_a.start(with_discovery=False, with_transport=True)
    await node_b.start(with_discovery=False, with_transport=True)

    try:
        result = await node_a.send_task("lc_b", "echo", {"hello": "world"}, timeout=5.0)
        assert result["type"] == "result"
        assert result["ok"] is True
        assert result["output"] == {"hello": "world"}
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_capability_query_returns_manifest() -> None:
    port_a = _free_port()
    port_b = _free_port()

    node_a = AgentNode(AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a"))
    node_b = AgentNode(AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b"))
    _wire_peers(node_a, node_b, port_a, port_b)

    await node_a.start(with_discovery=False, with_transport=True)
    await node_b.start(with_discovery=False, with_transport=True)

    try:
        response = await node_a.capability_query("lc_b", timeout=5.0)
        assert response["type"] == "capability_response"
        assert "skills" in response
        assert "limits" in response
        assert "agent" in response
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_stream_multiplexing_isolated_by_task_id() -> None:
    async def streamy(task_msg: dict, _peer) -> AsyncIterator[dict]:
        prefix = str(task_msg["input"]["prefix"])
        for i in range(3):
            yield {"type": "stream", "delta": f"{prefix}{i}", "seq": i}
            await asyncio.sleep(0.01)
        yield {
            "type": "result",
            "ok": True,
            "output": {"done": prefix},
            "ms": 5,
        }

    port_a = _free_port()
    port_b = _free_port()

    node_a = AgentNode(AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a"))
    node_b = AgentNode(AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b"), on_task=streamy)
    _wire_peers(node_a, node_b, port_a, port_b)

    await node_a.start(with_discovery=False, with_transport=True)
    await node_b.start(with_discovery=False, with_transport=True)

    try:
        h1 = await node_a.router.send_task("lc_b", "stream", {"prefix": "A"})
        h2 = await node_a.router.send_task("lc_b", "stream", {"prefix": "B"})

        async def collect(task_id: str) -> list[str]:
            out: list[str] = []
            async for item in node_a.router.stream(task_id):
                out.append(item["delta"])
            return out

        c1 = asyncio.create_task(collect(h1.task_id))
        c2 = asyncio.create_task(collect(h2.task_id))

        r1 = await asyncio.wait_for(h1.result, timeout=5.0)
        r2 = await asyncio.wait_for(h2.result, timeout=5.0)
        d1 = await c1
        d2 = await c2

        assert r1["ok"] is True
        assert r2["ok"] is True
        assert d1 == ["A0", "A1", "A2"]
        assert d2 == ["B0", "B1", "B2"]
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_send_file_and_verify_hash() -> None:
    port_a = _free_port()
    port_b = _free_port()

    node_a = AgentNode(AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a", max_transfer_bytes=5 * 1024 * 1024))
    node_b = AgentNode(AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b", max_transfer_bytes=5 * 1024 * 1024))
    _wire_peers(node_a, node_b, port_a, port_b)

    await node_a.start(with_discovery=False, with_transport=True)
    await node_b.start(with_discovery=False, with_transport=True)

    fd, p = tempfile.mkstemp(prefix="localclaw_test_", suffix=".bin")
    os.close(fd)
    path = Path(p)
    payload = os.urandom(1 * 1024 * 1024)
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()

    try:
        result = await node_a.send_file("lc_b", path, timeout=10.0)
        assert result["ok"] is True
        saved_to = Path(result["output"]["saved_to"])
        assert saved_to.exists()
        actual = hashlib.sha256(saved_to.read_bytes()).hexdigest()
        assert actual == expected
    finally:
        path.unlink(missing_ok=True)
        await node_a.stop()
        await node_b.stop()


HAS_ZEROCONF = True
try:
    import zeroconf  # noqa: F401
except Exception:
    HAS_ZEROCONF = False


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_ZEROCONF, reason="zeroconf dependency is unavailable")
@pytest.mark.skipif(os.environ.get("LOCALCLAW_RUN_MDNS_TESTS") != "1", reason="opt-in mDNS integration test")
async def test_mdns_discovery_two_nodes() -> None:
    port_a = _free_port()
    port_b = _free_port()

    cfg_a = AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a", advertise_host="127.0.0.1")
    cfg_b = AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b", advertise_host="127.0.0.1")

    node_a = AgentNode(cfg_a)
    node_b = AgentNode(cfg_b)

    await node_a.start(with_discovery=True, with_transport=False)
    await node_b.start(with_discovery=True, with_transport=False)

    try:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if node_a.peer_store.get("lc_b") and node_b.peer_store.get("lc_a"):
                break
            await asyncio.sleep(0.2)
        assert node_a.peer_store.get("lc_b") is not None
        assert node_b.peer_store.get("lc_a") is not None

        await node_b.stop()

        remove_deadline = time.time() + 5.0
        while time.time() < remove_deadline:
            if node_a.peer_store.get("lc_b") is None:
                break
            await asyncio.sleep(0.2)
        assert node_a.peer_store.get("lc_b") is None
    finally:
        await node_a.stop()
