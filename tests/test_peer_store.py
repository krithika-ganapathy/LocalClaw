from __future__ import annotations

import time

from localclaw.peer_store import PeerRecord, PeerStore


def test_peer_store_expires_stale_entries() -> None:
    store = PeerStore()
    now = time.time()

    store.upsert(
        PeerRecord(
            peer_id="a",
            name="A",
            host="127.0.0.1",
            port=4117,
            expires_at=now - 1,
        )
    )
    store.upsert(
        PeerRecord(
            peer_id="b",
            name="B",
            host="127.0.0.1",
            port=4118,
            expires_at=now + 60,
        )
    )

    expired = store.expire_stale(now=now)
    assert expired == ["a"]
    assert store.get("a") is None
    assert store.get("b") is not None
