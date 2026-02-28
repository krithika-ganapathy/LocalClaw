# LocalClaw (Person A Network Substrate)

LocalClaw is a LAN-native agent protocol substrate: mDNS discovery + direct TCP NDJSON messaging.
This repo currently implements Person A scope through Milestone 5.

## Prerequisites

- Python 3.11+
- `uv`
- Same LAN/subnet for multi-device discovery

## Install and Smoke Check

```bash
uv run localclaw --help
uv run --with pytest --with pytest-asyncio pytest -q
```

## 2-Device Runbook

Set up two configs (different `agent_id` and `agent_port`) and run one agent per device.

### Device A config (`.localclaw/a/config.yaml`)

```yaml
agent_name: agent-a
agent_id: lc_a
agent_port: 4117
caps: echo,capabilities
model: none
status: idle
version: 0.1.0
trust: unknown
bind_host: 0.0.0.0
```

### Device B config (`.localclaw/b/config.yaml`)

```yaml
agent_name: agent-b
agent_id: lc_b
agent_port: 4118
caps: echo,capabilities
model: none
status: idle
version: 0.1.0
trust: unknown
bind_host: 0.0.0.0
```

### Start agents

Device A:

```bash
uv run localclaw run --config .localclaw/a/config.yaml --show-peers
```

Device B:

```bash
uv run localclaw run --config .localclaw/b/config.yaml --show-peers
```

Note: `--config` is accepted both before and after the subcommand.

### Validate milestones from Device A

```bash
uv run localclaw ping lc_b --config .localclaw/a/config.yaml --wait 10
uv run localclaw capability-query --peer lc_b --config .localclaw/a/config.yaml --wait 10
uv run localclaw send-task --peer lc_b --skill echo --input '{"x":1}' --config .localclaw/a/config.yaml --wait 10
echo "hello-localclaw" > /tmp/localclaw-demo.txt
uv run localclaw send-file --peer lc_b --path /tmp/localclaw-demo.txt --config .localclaw/a/config.yaml --wait 10
```

## Commands

- `localclaw print-config [--ensure]`
- `localclaw run [--show-peers] [--no-discovery]`
- `localclaw scan [--timeout 5]`
- `localclaw doctor`
- `localclaw ping <peer_id> [--wait 5]`
- `localclaw capability-query --peer <peer_id>`
- `localclaw send-task --peer <peer_id> --skill <name> --input '<json-or-string>' [--stream]`
- `localclaw send-file --peer <peer_id> --path <file>`

## Troubleshooting

- If discovery fails on Windows, verify network profile is Private and firewall allows UDP 5353 multicast.
- Run `localclaw doctor --config ...` to validate multicast/TCP readiness.
- If `scan` shows no peers, keep `run --show-peers` active on both devices to confirm ongoing announcements.
