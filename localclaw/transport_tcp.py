from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .protocol import MAX_CONTROL_LINE_BYTES


class TransportError(RuntimeError):
    pass


@dataclass(slots=True)
class PeerConnection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    peer_id: str | None = None
    host: str = ""
    port: int = 0
    last_activity: float = field(default_factory=time.time)

    async def send_json(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":"), ensure_ascii=True)
        if len(line.encode("utf-8")) > MAX_CONTROL_LINE_BYTES:
            raise TransportError("JSON control message exceeds maximum line size")
        self.writer.write(line.encode("utf-8") + b"\n")
        await self.writer.drain()
        self.last_activity = time.time()

    async def recv_json(self) -> dict[str, Any]:
        try:
            raw = await self.reader.readline()
        except asyncio.LimitOverrunError as exc:
            raise TransportError("Incoming control line exceeds stream read limit") from exc

        if not raw:
            raise EOFError("Connection closed")
        if len(raw) > MAX_CONTROL_LINE_BYTES:
            raise TransportError("Incoming control line exceeds max control message size")

        self.last_activity = time.time()
        try:
            msg = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TransportError("Malformed JSON frame") from exc
        if not isinstance(msg, dict):
            raise TransportError("Control frame must decode to a JSON object")
        return msg

    async def send_blob(self, payload: bytes) -> None:
        if len(payload) > 0xFFFFFFFF:
            raise TransportError("Payload exceeds 4-byte framing limit")
        self.writer.write(struct.pack(">I", len(payload)))
        self.writer.write(payload)
        await self.writer.drain()
        self.last_activity = time.time()

    async def recv_blob(self, max_bytes: int | None = None) -> bytes:
        header = await self.reader.readexactly(4)
        (size,) = struct.unpack(">I", header)
        if max_bytes is not None and size > max_bytes:
            raise TransportError(f"Payload size {size} exceeds configured max {max_bytes}")
        data = await self.reader.readexactly(size)
        self.last_activity = time.time()
        return data

    def is_closing(self) -> bool:
        return self.writer.is_closing()

    async def close(self) -> None:
        if self.writer.is_closing():
            return
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


class TCPTransport:
    def __init__(
        self,
        on_message: Callable[[PeerConnection, dict[str, Any]], Awaitable[None]],
        on_disconnect: Callable[[PeerConnection], Awaitable[None]] | None = None,
        read_limit: int = MAX_CONTROL_LINE_BYTES + 2,
    ) -> None:
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._read_limit = read_limit
        self._server: asyncio.base_events.Server | None = None
        self._connections_by_peer_id: dict[str, PeerConnection] = {}
        self._read_tasks: set[asyncio.Task[None]] = set()

    async def start(self, host: str, port: int) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_incoming, host=host, port=port, limit=self._read_limit)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        read_tasks = list(self._read_tasks)
        for task in read_tasks:
            task.cancel()
        for task in read_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        connections = list(set(self._connections_by_peer_id.values()))
        self._connections_by_peer_id.clear()
        for conn in connections:
            await conn.close()

    async def connect(self, peer_id: str, host: str, port: int) -> PeerConnection:
        existing = self._connections_by_peer_id.get(peer_id)
        if existing is not None and not existing.is_closing():
            return existing

        reader, writer = await asyncio.open_connection(host=host, port=port, limit=self._read_limit)
        conn = PeerConnection(reader=reader, writer=writer, peer_id=peer_id, host=host, port=port)
        self.bind_peer(peer_id, conn)
        self._spawn_reader(conn)
        return conn

    def bind_peer(self, peer_id: str, conn: PeerConnection) -> None:
        conn.peer_id = peer_id
        self._connections_by_peer_id[peer_id] = conn

    def get_connection(self, peer_id: str) -> PeerConnection | None:
        conn = self._connections_by_peer_id.get(peer_id)
        if conn is not None and conn.is_closing():
            self._connections_by_peer_id.pop(peer_id, None)
            return None
        return conn

    def all_connections(self) -> list[PeerConnection]:
        return list(self._connections_by_peer_id.values())

    async def _handle_incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        host = ""
        port = 0
        if isinstance(peername, tuple) and len(peername) >= 2:
            host, port = str(peername[0]), int(peername[1])

        conn = PeerConnection(reader=reader, writer=writer, host=host, port=port)
        self._spawn_reader(conn)

    def _spawn_reader(self, conn: PeerConnection) -> None:
        task = asyncio.create_task(self._reader_loop(conn))
        self._read_tasks.add(task)
        task.add_done_callback(self._read_tasks.discard)

    async def _reader_loop(self, conn: PeerConnection) -> None:
        try:
            while not conn.is_closing():
                msg = await conn.recv_json()
                await self._on_message(conn, msg)
        except (EOFError, asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except TransportError:
            pass
        finally:
            if conn.peer_id is not None:
                current = self._connections_by_peer_id.get(conn.peer_id)
                if current is conn:
                    self._connections_by_peer_id.pop(conn.peer_id, None)
            await conn.close()
            if self._on_disconnect is not None:
                await self._on_disconnect(conn)
