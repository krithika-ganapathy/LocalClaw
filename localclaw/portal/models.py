from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from localclaw.peer_store import PeerRecord


@dataclass(slots=True)
class PeerHealthRecord:
    peer_id: str
    name: str
    host: str
    port: int
    caps: list[str]
    model: str
    status: str
    trust: str
    last_seen_mdns: float
    source: str = "mdns"
    reachable: bool | None = None
    last_ping_at: float | None = None
    last_rtt_ms: int | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "caps": list(self.caps),
            "model": self.model,
            "status": self.status,
            "trust": self.trust,
            "last_seen_mdns": self.last_seen_mdns,
            "source": self.source,
            "reachable": self.reachable,
            "last_ping_at": self.last_ping_at,
            "last_rtt_ms": self.last_rtt_ms,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


class PeerHealthStore:
    def __init__(self) -> None:
        self._records: dict[str, PeerHealthRecord] = {}

    def upsert_discovered(self, peer: PeerRecord) -> tuple[PeerHealthRecord, bool]:
        now = time.time()
        rec = self._records.get(peer.peer_id)
        if rec is None:
            rec = PeerHealthRecord(
                peer_id=peer.peer_id,
                name=peer.name,
                host=peer.host,
                port=peer.port,
                caps=list(peer.caps),
                model=peer.model,
                status=peer.status,
                trust=peer.trust,
                last_seen_mdns=peer.last_seen,
                source=peer.source,
                updated_at=now,
            )
            self._records[peer.peer_id] = rec
            return rec, True

        changed = False
        for field_name, value in (
            ("name", peer.name),
            ("host", peer.host),
            ("port", peer.port),
            ("caps", list(peer.caps)),
            ("model", peer.model),
            ("status", peer.status),
            ("trust", peer.trust),
            ("source", peer.source),
        ):
            if getattr(rec, field_name) != value:
                setattr(rec, field_name, value)
                changed = True

        if rec.last_seen_mdns != peer.last_seen:
            rec.last_seen_mdns = peer.last_seen
            changed = True

        if changed:
            rec.updated_at = now
        return rec, changed

    def mark_ping_result(
        self,
        peer_id: str,
        *,
        reachable: bool,
        rtt_ms: int | None,
        error: str | None,
        at: float | None = None,
    ) -> PeerHealthRecord | None:
        rec = self._records.get(peer_id)
        if rec is None:
            return None

        now = at or time.time()
        rec.reachable = reachable
        rec.last_ping_at = now
        rec.last_rtt_ms = rtt_ms
        rec.last_error = error
        rec.consecutive_failures = 0 if reachable else rec.consecutive_failures + 1
        rec.updated_at = now
        return rec

    def remove(self, peer_id: str) -> PeerHealthRecord | None:
        return self._records.pop(peer_id, None)

    def get(self, peer_id: str) -> PeerHealthRecord | None:
        return self._records.get(peer_id)

    def all(self) -> list[PeerHealthRecord]:
        return sorted(self._records.values(), key=lambda rec: (rec.name.lower(), rec.peer_id))

    def ids(self) -> set[str]:
        return set(self._records)
