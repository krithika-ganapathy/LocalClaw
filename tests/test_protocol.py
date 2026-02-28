from __future__ import annotations

import pytest

from localclaw.protocol import ProtocolError, make_message, validate_message


def test_validate_task_message_minimal() -> None:
    msg = make_message("task", "agent_a", task_id="t1", skill="echo", input={"x": 1})
    assert validate_message(msg) == "task"


def test_validate_result_requires_output_when_ok() -> None:
    msg = make_message("result", "agent_a", task_id="t1", ok=True, output={"x": 1}, ms=1)
    msg.pop("output")
    with pytest.raises(ProtocolError):
        validate_message(msg)


def test_validate_result_requires_error_when_not_ok() -> None:
    msg = make_message("result", "agent_a", task_id="t1", ok=False, error={"code": "x", "message": "y"}, ms=1)
    msg.pop("error")
    with pytest.raises(ProtocolError):
        validate_message(msg)


def test_validate_transfer_bytes_non_negative() -> None:
    msg = make_message("transfer", "agent_a", task_id="t1", name="f", mime="text/plain", bytes=1)
    msg["bytes"] = -5
    with pytest.raises(ProtocolError):
        validate_message(msg)


def test_unknown_message_type_rejected() -> None:
    with pytest.raises(ProtocolError):
        validate_message({"type": "other", "id": "1", "from": "a", "ts": 1})
