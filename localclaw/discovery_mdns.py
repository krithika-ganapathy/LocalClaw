from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Callable

from .config import AgentConfig, resolved_advertise_host
from .peer_store import PeerRecord, PeerStore
from .protocol import SERVICE_TYPE

try:
    from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, Zeroconf
except Exception:  # pragma: no cover - handled at runtime for missing dependency
    IPVersion = None  # type: ignore[assignment]
    ServiceBrowser = None  # type: ignore[assignment]
    ServiceInfo = None  # type: ignore[assignment]
    Zeroconf = None  # type: ignore[assignment]


@dataclass(slots=True)
class DiscoveryStatus:
    running: bool
    service_name: str | None = None


class _ServiceListener:
    def __init__(
        self,
        zeroconf: Zeroconf,
        peer_store: PeerStore,
        loop: asyncio.AbstractEventLoop,
        local_agent_id: str,
        on_update: Callable[[PeerRecord], None] | None = None,
        on_remove: Callable[[str], None] | None = None,
    ) -> None:
        self._zeroconf = zeroconf
        self._peer_store = peer_store
        self._loop = loop
        self._local_agent_id = local_agent_id
        self._on_update = on_update
        self._on_remove = on_remove
        self._service_to_peer: dict[str, str] = {}

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        peer_id = self._service_to_peer.pop(name, None)
        if peer_id:
            self._loop.call_soon_threadsafe(self._remove_peer, peer_id)

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self._resolve_and_upsert(service_type, name)

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self._resolve_and_upsert(service_type, name)

    def _resolve_and_upsert(self, service_type: str, name: str) -> None:
        info = self._zeroconf.get_service_info(service_type, name, timeout=3000)
        if info is None:
            return

        parsed = self._extract_record(info)
        if parsed is None:
            return

        self._service_to_peer[name] = parsed.peer_id
        self._loop.call_soon_threadsafe(self._upsert_peer, parsed)

    def _extract_record(self, info: ServiceInfo) -> PeerRecord | None:
        properties = {self._decode(k): self._decode(v) for k, v in (info.properties or {}).items()}
        peer_id = properties.get("id")
        if not peer_id:
            return None
        if peer_id == self._local_agent_id:
            return None

        host = ""
        addresses = info.parsed_addresses()
        if addresses:
            host = addresses[0]
        if not host and info.server:
            try:
                host = socket.gethostbyname(info.server.rstrip("."))
            except OSError:
                host = "127.0.0.1"

        ttl = getattr(info, "other_ttl", None) or getattr(info, "host_ttl", None) or 120
        now = time.time()
        return PeerRecord(
            peer_id=peer_id,
            name=properties.get("name", peer_id),
            host=host,
            port=info.port,
            caps=[c for c in properties.get("caps", "").split(",") if c],
            model=properties.get("model", "unknown"),
            status=properties.get("status", "unknown"),
            version=properties.get("version", "unknown"),
            trust=properties.get("trust", "unknown"),
            last_seen=now,
            expires_at=now + int(ttl),
            source="mdns",
            raw_txt=properties,
        )

    @staticmethod
    def _decode(value: bytes | str) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _upsert_peer(self, peer: PeerRecord) -> None:
        self._peer_store.upsert(peer)
        if self._on_update is not None:
            self._on_update(peer)

    def _remove_peer(self, peer_id: str) -> None:
        self._peer_store.remove(peer_id)
        if self._on_remove is not None:
            self._on_remove(peer_id)


class MDNSDiscovery:
    def __init__(
        self,
        config: AgentConfig,
        peer_store: PeerStore,
        on_update: Callable[[PeerRecord], None] | None = None,
        on_remove: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._peer_store = peer_store
        self._on_update = on_update
        self._on_remove = on_remove
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._service_info: ServiceInfo | None = None
        self._service_name: str | None = None
        self._advertise = True
        self._browse = True

    @property
    def status(self) -> DiscoveryStatus:
        return DiscoveryStatus(running=self._zeroconf is not None, service_name=self._service_name)

    async def start(self, *, advertise: bool = True, browse: bool = True) -> None:
        if Zeroconf is None or ServiceInfo is None or ServiceBrowser is None:
            raise RuntimeError("zeroconf dependency is missing; install with `pip install zeroconf`.")

        if self._zeroconf is not None:
            return
        if not advertise and not browse:
            return

        self._advertise = advertise
        self._browse = browse

        loop = asyncio.get_running_loop()
        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)

        if advertise:
            address = resolved_advertise_host(self._config)
            service_name = f"{self._config.agent_id}.{SERVICE_TYPE}"
            self._service_info = ServiceInfo(
                type_=SERVICE_TYPE,
                name=service_name,
                addresses=[socket.inet_aton(address)],
                port=self._config.agent_port,
                properties={
                    b"id": self._config.agent_id.encode("utf-8"),
                    b"name": self._config.agent_name.encode("utf-8"),
                    b"caps": ",".join(self._config.caps).encode("utf-8"),
                    b"model": self._config.model.encode("utf-8"),
                    b"status": self._config.status.encode("utf-8"),
                    b"version": self._config.version.encode("utf-8"),
                    b"trust": self._config.trust.encode("utf-8"),
                },
                server=f"{socket.gethostname()}.local.",
            )
            await asyncio.to_thread(self._zeroconf.register_service, self._service_info)
            self._service_name = service_name

        if browse:
            listener = _ServiceListener(
                zeroconf=self._zeroconf,
                peer_store=self._peer_store,
                loop=loop,
                local_agent_id=self._config.agent_id or "",
                on_update=self._on_update,
                on_remove=self._on_remove,
            )
            self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, listener)

    async def stop(self) -> None:
        if self._zeroconf is None:
            return
        if self._browser is not None:
            await asyncio.to_thread(self._browser.cancel)
            self._browser = None
        if self._service_info is not None:
            await asyncio.to_thread(self._zeroconf.unregister_service, self._service_info)
        await asyncio.to_thread(self._zeroconf.close)
        self._service_info = None
        self._zeroconf = None
        self._service_name = None
