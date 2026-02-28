from __future__ import annotations

import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any


class PairingManager:
    def __init__(
        self,
        *,
        agent_name: str,
        agent_id: str,
        store_path: Path | None = None,
        pin_ttl_s: int = 300,
    ) -> None:
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.pin_ttl_s = int(pin_ttl_s)
        self.store_path = store_path or (Path.home() / ".LocalClaw" / "trusted_portal_peers.json")
        self._trusted: dict[str, dict[str, Any]] = self._load_trusted()
        self._pending: dict[str, dict[str, Any]] = {}

    def start_pairing(self, requester_id: str, requester_name: str = "") -> dict[str, Any]:
        requester_id = (requester_id or "").strip()
        if not requester_id:
            return {"ok": False, "error": "missing_requester_id"}

        if requester_id in self._trusted:
            return {"ok": True, "pair_required": False, "already_paired": True}

        now = int(time.time())
        self._evict_expired(now)

        pin = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + self.pin_ttl_s
        self._pending[requester_id] = {
            "pin": pin,
            "requester_name": requester_name or requester_id,
            "created_at": now,
            "expires_at": expires_at,
        }

        # Intentional CLI output: user needs this PIN to pair from portal.
        print(
            f"[LocalClaw Pairing] Request from {requester_name or requester_id} ({requester_id}) "
            f"to agent {self.agent_name} ({self.agent_id})."
        )
        print(f"[LocalClaw Pairing] Pairing PIN: {pin} (expires in {self.pin_ttl_s}s)")

        return {
            "ok": True,
            "pair_required": True,
            "already_paired": False,
            "expires_at": expires_at,
        }

    def verify_pairing(self, requester_id: str, pin: str) -> dict[str, Any]:
        requester_id = (requester_id or "").strip()
        pin = (pin or "").strip()
        if not requester_id:
            return {"ok": False, "error": "missing_requester_id"}
        if not pin:
            return {"ok": False, "error": "missing_pin"}

        if requester_id in self._trusted:
            return {"ok": True, "paired": True, "already_paired": True}

        now = int(time.time())
        self._evict_expired(now)
        pending = self._pending.get(requester_id)
        if pending is None:
            return {"ok": False, "error": "pairing_not_started"}

        expected_pin = str(pending.get("pin", ""))
        if not hmac.compare_digest(expected_pin, pin):
            return {"ok": False, "error": "invalid_pin"}

        self._trusted[requester_id] = {
            "requester_name": pending.get("requester_name", requester_id),
            "paired_at": now,
            "agent_name": self.agent_name,
            "agent_id": self.agent_id,
        }
        self._pending.pop(requester_id, None)
        self._save_trusted()

        print(
            f"[LocalClaw Pairing] Paired requester {requester_id} "
            f"with agent {self.agent_name} ({self.agent_id})."
        )
        return {"ok": True, "paired": True, "already_paired": False}

    def pairing_status(self, requester_id: str) -> dict[str, Any]:
        requester_id = (requester_id or "").strip()
        if not requester_id:
            return {"ok": False, "error": "missing_requester_id"}
        if requester_id in self._trusted:
            return {"ok": True, "paired": True}
        self._evict_expired(int(time.time()))
        pending = self._pending.get(requester_id)
        if pending is None:
            return {"ok": True, "paired": False, "pair_required": True}
        return {
            "ok": True,
            "paired": False,
            "pair_required": True,
            "expires_at": int(pending.get("expires_at", 0)),
        }

    def _evict_expired(self, now: int) -> None:
        expired = [
            requester_id
            for requester_id, payload in self._pending.items()
            if int(payload.get("expires_at", 0)) <= now
        ]
        for requester_id in expired:
            self._pending.pop(requester_id, None)

    def _load_trusted(self) -> dict[str, dict[str, Any]]:
        if not self.store_path.exists():
            return {}
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            pass
        return {}

    def _save_trusted(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(self._trusted, indent=2, sort_keys=True),
            encoding="utf-8",
        )
