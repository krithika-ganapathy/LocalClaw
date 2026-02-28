from __future__ import annotations

import time
import uuid
from typing import Any, Literal, TypedDict


MAX_CONTROL_LINE_BYTES = 256 * 1024
SERVICE_TYPE = "_LocalClaw._tcp.local."
MESSAGE_TYPES = {
    "task",
    "result",
    "capability_query",
    "capability_response",
    "heartbeat",
    "transfer",
    "delegate",
    "stream",
}


class ProtocolError(ValueError):
    pass


MessageType = Literal[
    "task",
    "result",
    "capability_query",
    "capability_response",
    "heartbeat",
    "transfer",
    "delegate",
    "stream",
]


class Envelope(TypedDict):
    type: MessageType
    id: str
    from_: str
    ts: int


REQUIRED_BY_TYPE: dict[str, set[str]] = {
    "task": {"task_id", "skill", "input"},
    "result": {"task_id", "ok"},
    "capability_query": set(),
    "capability_response": {"skills", "limits", "agent"},
    "heartbeat": set(),
    "transfer": {"task_id", "name", "mime", "bytes"},
    "delegate": {"task_id", "to", "task"},
    "stream": {"task_id", "delta"},
}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_message_id() -> str:
    return uuid.uuid4().hex


def make_message(message_type: MessageType, sender_id: str, **payload: Any) -> dict[str, Any]:
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError(f"Unsupported message type: {message_type}")
    message = {
        "type": message_type,
        "id": payload.pop("id", new_message_id()),
        "from": sender_id,
        "ts": int(payload.pop("ts", now_ms())),
    }
    message.update(payload)
    validate_message(message)
    return message


def validate_message(msg: dict[str, Any]) -> MessageType:
    msg_type = msg.get("type")
    if msg_type not in MESSAGE_TYPES:
        raise ProtocolError("Missing or invalid 'type'")

    for field in ("id", "from", "ts"):
        if field not in msg:
            raise ProtocolError(f"Missing required envelope field: {field}")

    if not isinstance(msg["id"], str) or not msg["id"]:
        raise ProtocolError("Envelope field 'id' must be a non-empty string")
    if not isinstance(msg["from"], str) or not msg["from"]:
        raise ProtocolError("Envelope field 'from' must be a non-empty string")
    if not isinstance(msg["ts"], int):
        raise ProtocolError("Envelope field 'ts' must be an integer")

    missing = [field for field in REQUIRED_BY_TYPE[msg_type] if field not in msg]
    if missing:
        raise ProtocolError(f"Missing required fields for {msg_type}: {', '.join(sorted(missing))}")

    if msg_type == "result":
        if not isinstance(msg["ok"], bool):
            raise ProtocolError("'result.ok' must be bool")
        if msg["ok"] and "output" not in msg:
            raise ProtocolError("Successful result must include 'output'")
        if not msg["ok"] and "error" not in msg:
            raise ProtocolError("Failed result must include 'error'")

    if msg_type == "transfer":
        if not isinstance(msg["bytes"], int) or msg["bytes"] < 0:
            raise ProtocolError("'transfer.bytes' must be a non-negative integer")

    if msg_type == "stream":
        if "seq" in msg and (not isinstance(msg["seq"], int) or msg["seq"] < 0):
            raise ProtocolError("'stream.seq' must be a non-negative integer")

    return msg_type


def build_error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
