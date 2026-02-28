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
        self.pair_pin = "654321"
        self.is_paired = False

    async def start(self, **kwargs) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def ping(self, peer_id: str, timeout: float = 0) -> dict:
        await asyncio.sleep(0)
        return {"type": "heartbeat", "from": peer_id}

    async def send_task(self, peer_id: str, skill: str, input_data, *, timeout: float = 0, **kwargs) -> dict:
        _ = peer_id
        _ = timeout
        _ = kwargs
        if skill == "localclaw.pair.status":
            if self.is_paired:
                return {"ok": True, "output": {"ok": True, "paired": True, "pair_required": False}}
            return {"ok": True, "output": {"ok": True, "paired": False, "pair_required": True}}
        if skill == "localclaw.pair.start":
            return {"ok": True, "output": {"ok": True, "pair_required": True, "already_paired": False}}
        if skill == "localclaw.pair.verify":
            pin = (input_data or {}).get("pin")
            if pin == self.pair_pin:
                self.is_paired = True
                return {"ok": True, "output": {"ok": True, "paired": True, "pair_required": False}}
            return {"ok": True, "output": {"ok": False, "error": "invalid_pin"}}
        if skill == "ask":
            return {"ok": True, "output": {"text": "mock response"}}
        return {"ok": False, "error": {"code": "unknown_skill", "message": skill}}


def _build_client(*, auth_enabled: bool = True) -> tuple[TestClient, PortalAuth]:
    cfg = AgentConfig(agent_name="portal", agent_id="lc_portal")
    node = FakeNode()
    node.peer_store.upsert(
        PeerRecord(peer_id="lc_peer", name="peer", host="127.0.0.1", port=4117, caps=["echo", "ask"])
    )
    service = PortalService(cfg, node=node, with_discovery=False)
    auth = PortalAuth(pin="123456", enabled=auth_enabled, session_ttl_s=3600, secret=b"x" * 32)
    app = create_portal_app(service, auth)
    return TestClient(app), auth


def test_api_requires_auth_cookie() -> None:
    client, _auth = _build_client(auth_enabled=True)
    with client:
        response = client.get("/api/peers")
        assert response.status_code == 401


def test_pairing_allows_authenticated_requests() -> None:
    client, _auth = _build_client(auth_enabled=True)
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
    client, _auth = _build_client(auth_enabled=True)
    with client:
        client.post("/api/auth/pair", json={"pin": "123456"})
        resp = client.post("/api/peers/lc_peer/ping")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


def test_chat_pairing_and_message_endpoints() -> None:
    client, _auth = _build_client(auth_enabled=True)
    with client:
        client.post("/api/auth/pair", json={"pin": "123456"})

        state = client.get("/api/peers/lc_peer/chat")
        assert state.status_code == 200
        assert state.json()["pairing"]["pair_required"] is True

        started = client.post("/api/peers/lc_peer/chat/pair/start")
        assert started.status_code == 200
        assert started.json()["ok"] is True

        bad = client.post("/api/peers/lc_peer/chat/pair/verify", json={"pin": "000000"})
        assert bad.status_code == 200
        assert bad.json()["ok"] is False

        good = client.post("/api/peers/lc_peer/chat/pair/verify", json={"pin": "654321"})
        assert good.status_code == 200
        assert good.json()["ok"] is True

        message = client.post(
            "/api/peers/lc_peer/chat/message",
            json={"message": "hello", "skill": "ask", "timeout_s": 10},
        )
        assert message.status_code == 200
        assert message.json()["ok"] is True
        assert message.json()["message"] == "mock response"


def test_events_endpoint_requires_auth_and_is_registered() -> None:
    client, _auth = _build_client(auth_enabled=True)
    with client:
        unauthorized = client.get("/api/events")
        assert unauthorized.status_code == 401

        routes = {route.path for route in client.app.routes}
        assert "/api/events" in routes


def test_api_allows_access_when_auth_disabled() -> None:
    client, _auth = _build_client(auth_enabled=False)
    with client:
        meta = client.get("/api/meta")
        assert meta.status_code == 200
        assert meta.json()["auth_required"] is False

        peers = client.get("/api/peers")
        assert peers.status_code == 200
