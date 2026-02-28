from __future__ import annotations

from typing import Any

from ..pairing import PairingManager
from .registry import SkillRegistry


def register_pairing_skills(registry: SkillRegistry, pairing: PairingManager) -> None:
    async def pair_start(input_data: Any) -> dict[str, Any]:
        payload = input_data if isinstance(input_data, dict) else {}
        requester_id = str(payload.get("requester_id", "")).strip()
        requester_name = str(payload.get("requester_name", "")).strip()
        out = pairing.start_pairing(requester_id=requester_id, requester_name=requester_name)
        return {"type": "result", "ok": bool(out.get("ok", False)), "output": out, "ms": 0}

    async def pair_verify(input_data: Any) -> dict[str, Any]:
        payload = input_data if isinstance(input_data, dict) else {}
        requester_id = str(payload.get("requester_id", "")).strip()
        pin = str(payload.get("pin", "")).strip()
        out = pairing.verify_pairing(requester_id=requester_id, pin=pin)
        return {"type": "result", "ok": bool(out.get("ok", False)), "output": out, "ms": 0}

    async def pair_status(input_data: Any) -> dict[str, Any]:
        payload = input_data if isinstance(input_data, dict) else {}
        requester_id = str(payload.get("requester_id", "")).strip()
        out = pairing.pairing_status(requester_id=requester_id)
        return {"type": "result", "ok": bool(out.get("ok", False)), "output": out, "ms": 0}

    registry.register(
        "localclaw.pair.start",
        pair_start,
        description="Internal pairing handshake start",
        hidden=True,
    )
    registry.register(
        "localclaw.pair.verify",
        pair_verify,
        description="Internal pairing handshake verification",
        hidden=True,
    )
    registry.register(
        "localclaw.pair.status",
        pair_status,
        description="Internal pairing handshake status",
        hidden=True,
    )
