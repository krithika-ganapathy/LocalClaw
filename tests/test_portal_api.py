from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from localclaw.config import AgentConfig
from localclaw.peer_store import PeerRecord, PeerStore
from localclaw.portal.api import create_portal_app
from localclaw.portal.auth import PortalAuth
from localclaw.portal.service import PortalService


class FakeNode:
    def __init__(self) -> None:
        self.peer_store = PeerStore()

    async def start(self, **kwargs) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def ping(self, peer_id: str, timeout: float = 0) -> dict:
        await asyncio.sleep(0)
        return {"type": "heartbeat", "from": peer_id}


def _build_client() -> tuple[TestClient, PortalAuth]:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    node = FakeNode()
    node.peer_store.upsert(
        PeerRecord(peer_id="lc_peer", name="peer", host="127.0.0.1", port=4117, caps=["echo"])
    )
    service = PortalService(cfg, node=node, with_discovery=False)
    auth = PortalAuth(pin="123456", session_ttl_s=3600, secret=b"x" * 32)
    app = create_portal_app(service, auth)
    return TestClient(app), auth


def test_api_requires_auth_cookie() -> None:
    client, _auth = _build_client()
    with client:
        response = client.get("/api/peers")
        assert response.status_code == 401


def test_pairing_allows_authenticated_requests() -> None:
    client, _auth = _build_client()
    with client:
        bad = client.post("/api/auth/pair", json={"pin": "000000"})
        assert bad.status_code == 401

        good = client.post("/api/auth/pair", json={"pin": "123456"})
        assert good.status_code == 200
        assert "localclaw_session" in good.cookies

        peers = client.get("/api/peers")
        assert peers.status_code == 200
        payload = peers.json()
        assert payload["count"] == 1
        assert payload["peers"][0]["peer_id"] == "lc_peer"

        meta = client.get("/api/meta")
        assert meta.status_code == 200
        assert meta.json()["running"] is True


def test_manual_ping_endpoint() -> None:
    client, _auth = _build_client()
    with client:
        client.post("/api/auth/pair", json={"pin": "123456"})
        resp = client.post("/api/peers/lc_peer/ping")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


def test_events_endpoint_requires_auth_and_is_registered() -> None:
    client, _auth = _build_client()
    with client:
        unauthorized = client.get("/api/events")
        assert unauthorized.status_code == 401

        routes = {route.path for route in client.app.routes}
        assert "/api/events" in routes
