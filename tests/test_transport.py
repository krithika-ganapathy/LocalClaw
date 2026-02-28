from __future__ import annotations

import asyncio
import socket

import pytest

from localclaw.protocol import make_message
from localclaw.transport_tcp import PeerConnection, TCPTransport


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@pytest.mark.asyncio
async def test_peer_connection_json_and_blob_roundtrip() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = PeerConnection(reader=reader, writer=writer)
        msg = await conn.recv_json()
        assert msg["kind"] == "x"
        payload = await conn.recv_blob()
        await conn.send_json({"ok": True, "len": len(payload)})
        await conn.close()

    port = _free_port()
    server = await asyncio.start_server(handle, host="127.0.0.1", port=port)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        conn = PeerConnection(reader=reader, writer=writer)
        await conn.send_json({"kind": "x"})
        await conn.send_blob(b"abc123")
        response = await conn.recv_json()
        assert response == {"ok": True, "len": 6}
        await conn.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_malformed_json_closes_connection_gracefully() -> None:
    async def on_message(_conn: PeerConnection, _msg: dict) -> None:
        return None

    port = _free_port()
    transport = TCPTransport(on_message=on_message)
    await transport.start("127.0.0.1", port)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"{not-json}\n")
        await writer.drain()

        await asyncio.sleep(0.2)
        data = await reader.read()
        assert data == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_ping_roundtrip_with_transport_and_router() -> None:
    from localclaw.config import AgentConfig
    from localclaw.node import AgentNode
    from localclaw.peer_store import PeerRecord

    port_a = _free_port()
    port_b = _free_port()

    cfg_a = AgentConfig(agent_name="A", agent_port=port_a, agent_id="lc_a")
    cfg_b = AgentConfig(agent_name="B", agent_port=port_b, agent_id="lc_b")

    node_a = AgentNode(cfg_a)
    node_b = AgentNode(cfg_b)

    node_a.peer_store.upsert(PeerRecord(peer_id="lc_b", name="B", host="127.0.0.1", port=port_b))
    node_b.peer_store.upsert(PeerRecord(peer_id="lc_a", name="A", host="127.0.0.1", port=port_a))

    await node_a.start(with_discovery=False, with_transport=True)
    await node_b.start(with_discovery=False, with_transport=True)

    try:
        response = await node_a.ping("lc_b", timeout=3.0)
        assert response["type"] == "heartbeat"
        assert response.get("reply_to")
    finally:
        await node_a.stop()
        await node_b.stop()
