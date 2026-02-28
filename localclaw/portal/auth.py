from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


class PortalAuth:
    def __init__(
        self,
        *,
        pin: str | None = None,
        enabled: bool = False,
        session_ttl_s: int = 86400,
        cookie_name: str = "localclaw_session",
        secret: bytes | None = None,
    ) -> None:
        self.enabled = enabled
        self.pin = pin or f"{secrets.randbelow(1_000_000):06d}"
        self.session_ttl_s = session_ttl_s
        self.cookie_name = cookie_name
        self._secret = secret or secrets.token_bytes(32)

    def verify_pin(self, supplied_pin: str) -> bool:
        return hmac.compare_digest(self.pin, (supplied_pin or "").strip())

    def issue_session_token(self, now: int | None = None) -> tuple[str, int]:
        issued = int(now or time.time())
        exp = issued + int(self.session_ttl_s)
        raw_payload = json.dumps({"exp": exp}, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded_payload = base64.urlsafe_b64encode(raw_payload).rstrip(b"=")

        signature = hmac.new(self._secret, encoded_payload, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        token = f"{encoded_payload.decode('ascii')}.{encoded_signature.decode('ascii')}"
        return token, exp

    def verify_session_token(self, token: str | None, now: int | None = None) -> bool:
        if not token or "." not in token:
            return False
        encoded_payload_s, encoded_signature_s = token.split(".", 1)
        if not encoded_payload_s or not encoded_signature_s:
            return False

        try:
            encoded_payload = encoded_payload_s.encode("ascii")
            supplied_signature = _urlsafe_b64decode(encoded_signature_s)
        except Exception:
            return False

        expected_signature = hmac.new(self._secret, encoded_payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False

        try:
            payload_raw = _urlsafe_b64decode(encoded_payload_s)
            payload = json.loads(payload_raw.decode("utf-8"))
            exp = int(payload["exp"])
        except Exception:
            return False

        current = int(now or time.time())
        return current <= exp


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
