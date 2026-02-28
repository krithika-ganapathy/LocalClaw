from __future__ import annotations

import asyncio
from dataclasses import dataclass

from localclaw.discovery_mdns import _ServiceListener
from localclaw.peer_store import PeerStore


@dataclass
class DummyInfo:
    properties: dict[bytes, bytes]
    port: int = 4117
    server: str = "dummy.local."
    host_ttl: int = 120
    other_ttl: int = 120

    def parsed_addresses(self) -> list[str]:
        return ["127.0.0.1"]


def test_extract_record_filters_local_agent_id() -> None:
    loop = asyncio.new_event_loop()
    try:
        listener = _ServiceListener(
            zeroconf=None,  # type: ignore[arg-type]
            peer_store=PeerStore(),
            loop=loop,
            local_agent_id="lc_a",
        )
        info = DummyInfo(
            properties={
                b"id": b"lc_a",
                b"name": b"A",
                b"caps": b"echo",
                b"model": b"none",
                b"status": b"idle",
                b"version": b"0.1.0",
                b"trust": b"unknown",
            }
        )

        assert listener._extract_record(info) is None
    finally:
        loop.close()


def test_extract_record_keeps_same_id_when_no_local_filter() -> None:
    loop = asyncio.new_event_loop()
    try:
        listener = _ServiceListener(
            zeroconf=None,  # type: ignore[arg-type]
            peer_store=PeerStore(),
            loop=loop,
            local_agent_id="",
        )
        info = DummyInfo(
            properties={
                b"id": b"lc_a",
                b"name": b"A",
                b"caps": b"echo",
                b"model": b"none",
                b"status": b"idle",
                b"version": b"0.1.0",
                b"trust": b"unknown",
            }
        )

        rec = listener._extract_record(info)
        assert rec is not None
        assert rec.peer_id == "lc_a"
    finally:
        loop.close()
