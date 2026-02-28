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

## Mistral API Key

If you enable Mistral-backed skills (`summarize`, `code`, `ask`, `translate`, `explain`), set:

`MISTRAL_API_KEY=...`

Supported key locations (lookup order):

1. Existing process environment variable
2. `~/.LocalClaw/.env`
3. `./.env` (project-local fallback)

Example (`~/.LocalClaw/.env`):

```env
MISTRAL_API_KEY=your-key-here
```

## 2-Device Quickstart (Recommended)

On each device, from project root:

```bash
uv run localclaw setup
```

- Choose a unique agent name per device.
- Enable Mistral skills only on devices where `MISTRAL_API_KEY` is configured.

Then start both agents:

```bash
uv run localclaw run --show-peers

# Optional: run a LAN portal (open from phone browser)
uv run localclaw portal
```

From device A, discover and call device B:

```bash
uv run localclaw scan --timeout 10
uv run localclaw ping <peer_id> --wait 10
uv run localclaw send-task --peer <peer_id> --skill echo --input "hello" --wait 10
uv run localclaw send-task --peer <peer_id> --skill summarize --input "text to summarize" --stream --wait 10
```

If mDNS discovery is blocked, connect directly:

```bash
uv run localclaw ping <peer_id> --direct <target-ip>:4117
uv run localclaw send-task --peer <peer_id> --skill echo --input "hello" --direct <target-ip>:4117
```

## 2-Device Runbook (Manual Config)

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
- `localclaw portal [--bind 0.0.0.0] [--port 7420] [--ping-interval 10]`

## LAN Portal (Phone-Friendly)

Start:

```bash
uv run localclaw portal
```

The portal prints:

- A 6-digit pairing PIN
- LAN URL (for example `http://192.168.1.42:7420`)
- Hostname URL (for example `http://my-laptop.local:7420`)
- Terminal QR code for quick phone scan

Open the URL from any device on the same LAN, enter the PIN once, then view discovered agents and run manual pings.

## Troubleshooting

- If discovery fails on Windows, verify network profile is Private and firewall allows UDP 5353 multicast.
- Run `localclaw doctor --config ...` to validate multicast/TCP readiness.
- If `scan` shows no peers, keep `run --show-peers` active on both devices to confirm ongoing announcements.
- If `summarize`/other Mistral skills fail, confirm `MISTRAL_API_KEY` is set and the selected model is not `none`.
