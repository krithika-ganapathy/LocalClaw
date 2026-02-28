from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .peer_store import PeerRecord, PeerStore
from .protocol import ProtocolError, build_error, make_message, new_message_id, validate_message
from .transport_tcp import PeerConnection, TCPTransport, TransportError


TaskCallback = Callable[[dict[str, Any], PeerRecord | None], Awaitable[Any] | AsyncIterator[dict[str, Any]]]
CapabilityCallback = Callable[[dict[str, Any], PeerRecord | None], Awaitable[dict[str, Any]]]
TransferCallback = Callable[[dict[str, Any], Path, PeerRecord | None], Awaitable[dict[str, Any]]]
DelegateCallback = Callable[[dict[str, Any], PeerRecord | None], Awaitable[None]]


@dataclass(slots=True)
class TaskHandle:
    task_id: str
    result: asyncio.Future[dict[str, Any]]


class _StreamChannel:
    def __init__(self, max_size: int) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=max_size)
        self.last_seq: int = -1
        self.closed = False

    def push(self, msg: dict[str, Any]) -> bool:
        if self.closed:
            return False

        seq = msg.get("seq")
        if isinstance(seq, int):
            if seq <= self.last_seq:
                return False
            self.last_seq = seq

        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(msg)
        return True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(None)

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


async def _default_on_task(task_msg: dict[str, Any], _peer: PeerRecord | None) -> dict[str, Any]:
    skill = task_msg.get("skill")
    if skill == "echo":
        return {
            "type": "result",
            "task_id": task_msg["task_id"],
            "ok": True,
            "output": task_msg.get("input"),
            "ms": 0,
        }
    if skill == "capabilities":
        return {
            "type": "result",
            "task_id": task_msg["task_id"],
            "ok": True,
            "output": {
                "skills": ["echo", "capabilities"],
                "limits": {},
                "agent": {"runtime": "person-a-stub"},
            },
            "ms": 0,
        }
    return {
        "type": "result",
        "task_id": task_msg["task_id"],
        "ok": False,
        "error": build_error("unknown_skill", f"Skill '{skill}' is not available"),
        "ms": 0,
    }


async def _default_on_capability_query(_query: dict[str, Any], _peer: PeerRecord | None) -> dict[str, Any]:
    return {
        "skills": ["echo", "capabilities"],
        "limits": {"max_transfer_bytes": 100 * 1024 * 1024},
        "agent": {"runtime": "person-a-stub"},
    }


async def _default_on_transfer_received(_meta: dict[str, Any], temp_path: Path, _peer: PeerRecord | None) -> dict[str, Any]:
    return {"saved_to": str(temp_path)}


class Router:
    def __init__(
        self,
        agent_id: str,
        peer_store: PeerStore,
        *,
        on_task: TaskCallback | None = None,
        on_capability_query: CapabilityCallback | None = None,
        on_transfer_received: TransferCallback | None = None,
        on_delegate: DelegateCallback | None = None,
        max_transfer_bytes: int = 100 * 1024 * 1024,
        stream_queue_size: int = 100,
    ) -> None:
        self.agent_id = agent_id
        self.peer_store = peer_store
        self.on_task = on_task or _default_on_task
        self.on_capability_query = on_capability_query or _default_on_capability_query
        self.on_transfer_received = on_transfer_received or _default_on_transfer_received
        self.on_delegate = on_delegate
        self.max_transfer_bytes = max_transfer_bytes
        self.stream_queue_size = stream_queue_size

        self.transport: TCPTransport | None = None

        self.pending_results: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_heartbeats: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_capability: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.stream_channels: dict[str, _StreamChannel] = {}

    def attach_transport(self, transport: TCPTransport) -> None:
        self.transport = transport

    async def handle_message(self, conn: PeerConnection, msg: dict[str, Any]) -> None:
        try:
            msg_type = validate_message(msg)
        except ProtocolError:
            await conn.close()
            return

        from_id = msg.get("from")
        if isinstance(from_id, str) and self.transport is not None:
            self.transport.bind_peer(from_id, conn)
            self.peer_store.touch(from_id)

        handlers = {
            "heartbeat": self._handle_heartbeat,
            "capability_query": self._handle_capability_query,
            "capability_response": self._handle_capability_response,
            "task": self._handle_task,
            "result": self._handle_result,
            "stream": self._handle_stream,
            "transfer": self._handle_transfer,
            "delegate": self._handle_delegate,
        }
        await handlers[msg_type](conn, msg)

    async def _handle_heartbeat(self, conn: PeerConnection, msg: dict[str, Any]) -> None:
        reply_to = msg.get("reply_to")
        if isinstance(reply_to, str):
            future = self.pending_heartbeats.pop(reply_to, None)
            if future is not None and not future.done():
                future.set_result(msg)
            return

        response = make_message("heartbeat", self.agent_id, reply_to=msg["id"])
        await conn.send_json(response)

    async def _handle_capability_query(self, conn: PeerConnection, msg: dict[str, Any]) -> None:
        peer = self.peer_store.get(msg.get("from", ""))
        payload = await self.on_capability_query(msg, peer)
        response = make_message(
            "capability_response",
            self.agent_id,
            skills=payload.get("skills", []),
            limits=payload.get("limits", {}),
            agent=payload.get("agent", {}),
            reply_to=msg["id"],
        )
        await conn.send_json(response)

    async def _handle_capability_response(self, _conn: PeerConnection, msg: dict[str, Any]) -> None:
        reply_to = msg.get("reply_to")
        if isinstance(reply_to, str):
            future = self.pending_capability.pop(reply_to, None)
            if future is not None and not future.done():
                future.set_result(msg)

    async def _handle_task(self, conn: PeerConnection, msg: dict[str, Any]) -> None:
        peer = self.peer_store.get(msg.get("from", ""))
        started = time.perf_counter()
        handler_out = self.on_task(msg, peer)

        sent_result = False
        if hasattr(handler_out, "__aiter__"):
            async for event in handler_out:  # type: ignore[union-attr]
                sent_result = await self._emit_task_event(conn, msg, event) or sent_result
        else:
            event = await handler_out  # type: ignore[assignment]
            sent_result = await self._emit_task_event(conn, msg, event)

        if not sent_result:
            fallback = make_message(
                "result",
                self.agent_id,
                task_id=msg["task_id"],
                ok=True,
                output=None,
                ms=int((time.perf_counter() - started) * 1000),
            )
            await conn.send_json(fallback)

    async def _emit_task_event(self, conn: PeerConnection, task_msg: dict[str, Any], event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type == "stream":
            message = make_message(
                "stream",
                self.agent_id,
                task_id=task_msg["task_id"],
                delta=event.get("delta", ""),
                seq=event.get("seq"),
                done=event.get("done", False),
            )
            await conn.send_json(message)
            return False

        if event_type == "result" or (event_type is None and "ok" in event):
            message = make_message(
                "result",
                self.agent_id,
                task_id=task_msg["task_id"],
                ok=bool(event.get("ok", True)),
                output=event.get("output"),
                error=event.get("error"),
                usage=event.get("usage", {}),
                ms=int(event.get("ms", 0)),
            )
            if message["ok"]:
                message.pop("error", None)
            else:
                message.pop("output", None)
            await conn.send_json(message)
            return True

        unknown = make_message(
            "result",
            self.agent_id,
            task_id=task_msg["task_id"],
            ok=False,
            error=build_error("invalid_handler_event", "Task handler yielded unsupported event shape"),
            ms=0,
        )
        await conn.send_json(unknown)
        return True

    async def _handle_result(self, _conn: PeerConnection, msg: dict[str, Any]) -> None:
        task_id = msg["task_id"]
        future = self.pending_results.pop(task_id, None)
        if future is not None and not future.done():
            future.set_result(msg)

        stream_channel = self.stream_channels.pop(task_id, None)
        if stream_channel is not None:
            stream_channel.close()

    async def _handle_stream(self, _conn: PeerConnection, msg: dict[str, Any]) -> None:
        task_id = msg["task_id"]
        channel = self.stream_channels.get(task_id)
        if channel is None:
            channel = _StreamChannel(max_size=self.stream_queue_size)
            self.stream_channels[task_id] = channel
        channel.push(msg)

    async def _handle_delegate(self, _conn: PeerConnection, msg: dict[str, Any]) -> None:
        if self.on_delegate is None:
            return
        peer = self.peer_store.get(msg.get("from", ""))
        await self.on_delegate(msg, peer)

    async def _handle_transfer(self, conn: PeerConnection, msg: dict[str, Any]) -> None:
        task_id = msg["task_id"]
        begin = time.perf_counter()
        expected_size = int(msg["bytes"])

        if expected_size > self.max_transfer_bytes:
            response = make_message(
                "result",
                self.agent_id,
                task_id=task_id,
                ok=False,
                error=build_error("payload_too_large", "Transfer exceeds configured limit"),
                ms=0,
            )
            await conn.send_json(response)
            return

        try:
            blob = await conn.recv_blob(max_bytes=self.max_transfer_bytes)
            if len(blob) != expected_size:
                raise TransportError(
                    f"Expected {expected_size} bytes but received {len(blob)} bytes"
                )
            sha256 = msg.get("sha256")
            if isinstance(sha256, str):
                digest = hashlib.sha256(blob).hexdigest()
                if digest != sha256:
                    raise TransportError("SHA256 mismatch for transfer payload")

            with tempfile.NamedTemporaryFile(prefix="localclaw_", suffix="_transfer", delete=False) as handle:
                handle.write(blob)
                temp_path = Path(handle.name)

            peer = self.peer_store.get(msg.get("from", ""))
            output = await self.on_transfer_received(msg, temp_path, peer)
            response = make_message(
                "result",
                self.agent_id,
                task_id=task_id,
                ok=True,
                output=output,
                ms=int((time.perf_counter() - begin) * 1000),
            )
            await conn.send_json(response)
        except (TransportError, OSError, asyncio.IncompleteReadError) as exc:
            response = make_message(
                "result",
                self.agent_id,
                task_id=task_id,
                ok=False,
                error=build_error("transfer_failed", str(exc)),
                ms=int((time.perf_counter() - begin) * 1000),
            )
            await conn.send_json(response)

    async def _get_or_connect(self, peer_id: str) -> PeerConnection:
        if self.transport is None:
            raise RuntimeError("Router transport is not attached")

        conn = self.transport.get_connection(peer_id)
        if conn is not None and not conn.is_closing():
            return conn

        peer = self.peer_store.get(peer_id)
        if peer is None:
            raise RuntimeError(f"Unknown peer '{peer_id}'. Run scan or wait for discovery.")
        return await self.transport.connect(peer_id=peer.peer_id, host=peer.host, port=peer.port)

    async def ping(self, peer_id: str, timeout: float = 5.0) -> dict[str, Any]:
        conn = await self._get_or_connect(peer_id)
        msg_id = new_message_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_heartbeats[msg_id] = future
        await conn.send_json(make_message("heartbeat", self.agent_id, id=msg_id))
        return await asyncio.wait_for(future, timeout=timeout)

    async def capability_query(self, peer_id: str, want: list[str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        conn = await self._get_or_connect(peer_id)
        msg_id = new_message_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_capability[msg_id] = future
        payload: dict[str, Any] = {"id": msg_id}
        if want:
            payload["want"] = want
        await conn.send_json(make_message("capability_query", self.agent_id, **payload))
        return await asyncio.wait_for(future, timeout=timeout)

    async def send_task(
        self,
        peer_id: str,
        skill: str,
        input_data: Any,
        *,
        priority: int = 0,
        trace: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> TaskHandle:
        conn = await self._get_or_connect(peer_id)
        task_id = new_message_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_results[task_id] = future

        payload: dict[str, Any] = {
            "task_id": task_id,
            "skill": skill,
            "input": input_data,
            "priority": priority,
        }
        if trace:
            payload["trace"] = trace

        await conn.send_json(make_message("task", self.agent_id, **payload))
        _ = timeout  # timeout is used by callers around task handle future
        return TaskHandle(task_id=task_id, result=future)

    def stream(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        channel = self.stream_channels.get(task_id)
        if channel is None:
            channel = _StreamChannel(max_size=self.stream_queue_size)
            self.stream_channels[task_id] = channel
        return channel.iterate()

    async def send_file(self, peer_id: str, file_path: Path, timeout: float = 60.0) -> dict[str, Any]:
        conn = await self._get_or_connect(peer_id)
        payload = file_path.read_bytes()
        task_id = new_message_id()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_results[task_id] = future

        if len(payload) > self.max_transfer_bytes:
            raise RuntimeError("File exceeds configured max_transfer_bytes")

        sha = hashlib.sha256(payload).hexdigest()
        mime, _ = mimetypes.guess_type(file_path.name)
        message = make_message(
            "transfer",
            self.agent_id,
            task_id=task_id,
            name=file_path.name,
            mime=mime or "application/octet-stream",
            bytes=len(payload),
            sha256=sha,
        )
        await conn.send_json(message)
        await conn.send_blob(payload)
        return await asyncio.wait_for(future, timeout=timeout)

    async def send_result(
        self,
        conn: PeerConnection,
        *,
        task_id: str,
        ok: bool,
        output: Any = None,
        error: dict[str, str] | None = None,
        ms: int = 0,
        usage: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "ok": ok,
            "ms": ms,
            "usage": usage or {},
        }
        if ok:
            payload["output"] = output
        else:
            payload["error"] = error or build_error("unknown", "Unknown error")

        await conn.send_json(make_message("result", self.agent_id, **payload))

    async def send_stream(
        self,
        conn: PeerConnection,
        *,
        task_id: str,
        delta: str,
        seq: int | None = None,
        done: bool = False,
    ) -> None:
        payload: dict[str, Any] = {"task_id": task_id, "delta": delta, "done": done}
        if seq is not None:
            payload["seq"] = seq
        await conn.send_json(make_message("stream", self.agent_id, **payload))

    @staticmethod
    def parse_json_input(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
