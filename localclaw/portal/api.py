from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import PortalAuth
from .service import PortalService


class PairRequest(BaseModel):
    pin: str


class PeerPairVerifyRequest(BaseModel):
    pin: str


class ChatMessageRequest(BaseModel):
    message: str
    skill: str | None = None
    timeout_s: float = 45.0


def create_portal_app(service: PortalService, auth: PortalAuth) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="LocalClaw Portal", lifespan=lifespan)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def api_auth_middleware(request: Request, call_next):
        path = request.url.path
        if auth.enabled and path.startswith("/api/") and path != "/api/auth/pair":
            token = request.cookies.get(auth.cookie_name)
            if not auth.verify_session_token(token):
                return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    @app.get("/")
    async def portal_index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.post("/api/auth/pair")
    async def pair(payload: PairRequest) -> JSONResponse:
        if not auth.enabled:
            return JSONResponse({"ok": True, "auth_required": False})
        if not auth.verify_pin(payload.pin):
            raise HTTPException(status_code=401, detail="invalid_pin")

        token, exp = auth.issue_session_token()
        response = JSONResponse({"ok": True, "expires_at": exp})
        response.set_cookie(
            key=auth.cookie_name,
            value=token,
            httponly=True,
            max_age=auth.session_ttl_s,
            secure=False,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/meta")
    async def api_meta() -> dict[str, Any]:
        return {
            **service.meta(),
            "auth_required": auth.enabled,
        }

    @app.get("/api/peers")
    async def api_peers() -> dict[str, Any]:
        peers = service.snapshot()
        return {"peers": peers, "count": len(peers)}

    @app.post("/api/peers/{peer_id}/ping")
    async def api_ping_peer(peer_id: str) -> dict[str, Any]:
        try:
            return await service.manual_ping(peer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown_peer") from exc

    @app.get("/api/peers/{peer_id}/chat")
    async def api_chat_state(peer_id: str) -> dict[str, Any]:
        try:
            return await service.chat_state(peer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown_peer") from exc

    @app.post("/api/peers/{peer_id}/chat/pair/start")
    async def api_chat_pair_start(peer_id: str) -> dict[str, Any]:
        try:
            return await service.start_pairing(peer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown_peer") from exc

    @app.post("/api/peers/{peer_id}/chat/pair/verify")
    async def api_chat_pair_verify(peer_id: str, payload: PeerPairVerifyRequest) -> dict[str, Any]:
        try:
            return await service.verify_pairing(peer_id, payload.pin)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown_peer") from exc

    @app.post("/api/peers/{peer_id}/chat/message")
    async def api_chat_message(peer_id: str, payload: ChatMessageRequest) -> dict[str, Any]:
        try:
            return await service.send_chat(
                peer_id,
                message=payload.message,
                skill=payload.skill,
                timeout_s=payload.timeout_s,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown_peer") from exc

    @app.get("/api/events")
    async def api_events() -> StreamingResponse:
        queue = service.subscribe_events(queue_size=300, include_snapshot=True)

        async def event_stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _format_sse(event)
            finally:
                service.unsubscribe_events(queue)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

    return app


def _format_sse(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "message"))
    data = json.dumps(event.get("data", {}), separators=(",", ":"), ensure_ascii=True)
    return f"event: {event_name}\ndata: {data}\n\n"
