# Person B Handoff Contract (Decision-Complete)

This is the integration contract for Person B (runtime, skills, backends) against Person A’s implemented network substrate.

## 1) Ownership Boundary

### Person A owns

- Discovery: mDNS advertise/browse (`localclaw/discovery_mdns.py`)
- Transport: TCP NDJSON + binary framing (`localclaw/transport_tcp.py`)
- Protocol validation and message creation (`localclaw/protocol.py`)
- Router, correlation, streams, transfer plumbing (`localclaw/router.py`)
- Node lifecycle + heartbeat maintenance (`localclaw/node.py`)
- Network-facing CLI commands (`localclaw/cli.py`)

### Person B owns

- Runtime behavior for tasks (`echo`, `capabilities`, `summarize`, etc.)
- Model backend wiring (Mistral API / local OpenAI-compatible backend)
- Delegation policy and task-chain logic
- Capability manifest content beyond baseline structure

## 2) How Person B Integrates

Instantiate `AgentNode` with callback injection:

```python
node = AgentNode(
    config,
    on_task=on_task,
    on_capability_query=on_capability_query,
    on_transfer_received=on_transfer_received,
    on_delegate=on_delegate,  # optional
)
```

`AgentNode` will own lifecycle (`start/stop`) and invoke callbacks from the router.

## 3) Wire Contract

### Service discovery

- Service type is fixed: `_LocalClaw._tcp.local.`
- TXT keys emitted: `id,name,caps,model,status,version,trust`

### Control channel

- Transport: direct TCP
- Framing: NDJSON (one JSON object per line)
- Encoding: UTF-8
- Max control message size: `256 KiB` (`MAX_CONTROL_LINE_BYTES`)

### Binary channel (`transfer`)

- After receiving `transfer` header, next frame is:
  - 4-byte big-endian unsigned length
  - raw payload bytes

## 4) Message Types and Required Fields

All messages require envelope:

- `type: str`
- `id: str` (non-empty)
- `from: str` (non-empty)
- `ts: int` (unix ms)

Supported `type` values:

- `task`
- `result`
- `capability_query`
- `capability_response`
- `heartbeat`
- `transfer`
- `delegate`
- `stream`

Required body fields by type:

- `task`: `task_id`, `skill`, `input`
- `result`: `task_id`, `ok`
- `capability_query`: none required
- `capability_response`: `skills`, `limits`, `agent`
- `heartbeat`: none required
- `transfer`: `task_id`, `name`, `mime`, `bytes`
- `delegate`: `task_id`, `to`, `task`
- `stream`: `task_id`, `delta`

Validation rules enforced today:

- `result.ok` must be boolean
- if `result.ok == true`, `output` must exist
- if `result.ok == false`, `error` must exist
- `transfer.bytes` must be non-negative int
- if `stream.seq` exists, it must be non-negative int
- unknown extra fields are allowed unless they break required validation

## 5) Correlation and Lifecycle Rules

Correlation keys used in router:

- heartbeat request/response: `id` + `reply_to`
- capability request/response: `id` + `reply_to`
- task result/stream: `task_id`

Router behavior:

- `ping()` sends heartbeat with custom `id`; remote replies with `heartbeat.reply_to=<request id>`
- `capability_query()` sends query with custom `id`; remote sends `capability_response.reply_to=<request id>`
- `send_task()` allocates `task_id`, waits for `result` with matching `task_id`
- `stream(task_id)` yields stream events until final `result` closes stream channel

## 6) Callback Contract (Exact)

## `on_task(task_msg, peer)`

Type:

```python
Callable[[dict[str, Any], PeerRecord | None], Awaitable[Any] | AsyncIterator[dict[str, Any]]]
```

Supported return modes:

1. Awaitable returning one final result event (dict)
2. Async iterator yielding zero or more stream events and one final result event

Accepted yielded/returned event shapes:

- Stream event:

```python
{"type": "stream", "delta": "...", "seq": 0, "done": False}
```

- Result event:

```python
{"type": "result", "ok": True, "output": {...}, "ms": 123, "usage": {...}}
```

Notes:

- Router injects envelope + `task_id` into outgoing `stream/result` messages.
- If handler yields unsupported shape, router sends failed result:
  - `error.code = "invalid_handler_event"`
- If handler never emits a result, router emits fallback success result with `output=null`.

## `on_capability_query(query_msg, peer)`

Type:

```python
Callable[[dict[str, Any], PeerRecord | None], Awaitable[dict[str, Any]]]
```

Must return dict with:

- `skills` (list)
- `limits` (dict)
- `agent` (dict)

Router wraps this into a `capability_response` and sets `reply_to`.

## `on_transfer_received(meta, temp_path, peer)`

Type:

```python
Callable[[dict[str, Any], Path, PeerRecord | None], Awaitable[dict[str, Any]]]
```

- `temp_path` points to payload saved by Person A transfer layer
- Return dict becomes `result.output`

## `on_delegate(delegate_msg, peer)` (optional)

Type:

```python
Callable[[dict[str, Any], PeerRecord | None], Awaitable[None]]
```

- If not supplied, incoming `delegate` messages are accepted but ignored.

## 7) Streaming Semantics

- Stream channels are per-`task_id`.
- Queue is bounded by `stream_queue_size` (default `100`).
- If queue is full, oldest event is dropped.
- If `seq` present, router drops duplicates/out-of-order (`seq <= last_seq`).
- Stream channel closes when matching `result` arrives.

## 8) Transfer Semantics and Errors

Incoming transfer path:

1. Validate header (`transfer` message)
2. Enforce configured `max_transfer_bytes` before read
3. Read blob frame
4. Verify byte count equals `transfer.bytes`
5. Verify `sha256` if provided
6. Persist to temp file and call `on_transfer_received`
7. Return `result`

Error codes emitted by transfer path:

- `payload_too_large` (header declared size > config limit)
- `transfer_failed` (frame/size/hash/io errors)

Outgoing transfer (`send_file`) behavior:

- Reads full file in memory
- Rejects locally if file exceeds `max_transfer_bytes`
- Sends `transfer` header including computed `sha256`
- Sends framed blob
- Waits for matching `result`

## 9) Heartbeat and Connection Behavior

From `AgentNode` maintenance loop:

- Wake interval: `max(0.5, heartbeat_interval_s / 2)`
- If a connection is silent for `>= heartbeat_interval_s`, send heartbeat
- If silent for `>= heartbeat_timeout_s`, close connection

Defaults from config:

- `heartbeat_interval_s = 10.0`
- `heartbeat_timeout_s = 30.0`
- `max_transfer_bytes = 104857600` (100 MiB)
- `stream_queue_size = 100`

Connection model:

- One reusable TCP connection per peer id.
- Connection is (re)opened lazily when router needs it and none is active.

## 10) Config Fields Person B Should Use

`AgentConfig` fields relevant to runtime behavior:

- Identity and announce: `agent_name`, `agent_id`, `caps`, `model`, `status`, `version`, `trust`
- Network: `bind_host`, `advertise_host`, `agent_port`
- Runtime limits: `heartbeat_interval_s`, `heartbeat_timeout_s`, `max_transfer_bytes`, `stream_queue_size`

## 11) Known Current Defaults / Placeholders

- Person A stub capabilities/runtime values are placeholders (`person-a-stub`).
- Security (TOFU/signing/encryption/scoping) is not implemented yet.
- Delegation policy and chain bubbling are not implemented by default callback.

## 12) Required Person B Implementation Checklist

1. Implement `on_task` for at least:
   - `echo`
   - `capabilities`
   - `summarize` (with optional streaming)
2. Implement `on_capability_query` with accurate skills/limits/backend metadata.
3. Implement `on_transfer_received` handling desired persistence/indexing behavior.
4. Optionally implement `on_delegate` for multi-agent orchestration.
5. Wire callbacks into `AgentNode` and run e2e checks.

## 13) Verification Commands

From caller node:

```bash
uv run localclaw ping <peer_id> --wait 10
uv run localclaw capability-query --peer <peer_id> --wait 10
uv run localclaw send-task --peer <peer_id> --skill echo --input '{"x":1}' --wait 10
uv run localclaw send-task --peer <peer_id> --skill summarize --input '{"text":"..."}' --stream --wait 10
uv run localclaw send-file --peer <peer_id> --path /tmp/demo.txt --wait 10
```

Automated tests:

```bash
uv run --with pytest --with pytest-asyncio pytest -q
```

## 14) Change Control

If Person B needs protocol changes, update `localclaw/protocol.py` first and treat it as the shared contract file.
All router/runtime changes should preserve backward compatibility for existing required fields.
