from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PeerRecord:
    peer_id: str
    name: str
    host: str
    port: int
    caps: list[str] = field(default_factory=list)
    model: str = "unknown"
    status: str = "unknown"
    version: str = "unknown"
    trust: str = "unknown"
    last_seen: float = field(default_factory=time.time)
    expires_at: float | None = None
    source: str = "mdns"
    raw_txt: dict[str, str] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "caps": self.caps,
            "model": self.model,
            "status": self.status,
            "version": self.version,
            "trust": self.trust,
            "last_seen": self.last_seen,
            "expires_at": self.expires_at,
            "source": self.source,
            "raw_txt": dict(self.raw_txt),
        }


class PeerStore:
    def __init__(self) -> None:
        self._peers: dict[str, PeerRecord] = {}

    def upsert(self, peer: PeerRecord) -> None:
        peer.last_seen = time.time()
        self._peers[peer.peer_id] = peer

    def remove(self, peer_id: str) -> None:
        self._peers.pop(peer_id, None)

    def get(self, peer_id: str) -> PeerRecord | None:
        return self._peers.get(peer_id)

    def all(self) -> list[PeerRecord]:
        self.expire_stale()
        return list(self._peers.values())

    def expire_stale(self, now: float | None = None) -> list[str]:
        if now is None:
            now = time.time()
        expired = [
            peer_id
            for peer_id, rec in self._peers.items()
            if rec.expires_at is not None and rec.expires_at <= now
        ]
        for peer_id in expired:
            self._peers.pop(peer_id, None)
        return expired

    def touch(self, peer_id: str) -> None:
        peer = self._peers.get(peer_id)
        if peer is not None:
            peer.last_seen = time.time()
