# Running LocalClaw Across Devices on a Local Network

Each device runs its own agent. Agents discover each other via mDNS and communicate
directly over TCP — no central server, no cloud required.

---

## Prerequisites

| | Windows | macOS |
|---|---|---|
| Python | 3.11+ (python.org) | 3.11+ (`brew install python@3.11`) |
| Network | Same Wi-Fi or LAN | Same Wi-Fi or LAN |
| Firewall | Allow inbound TCP on port 4117 | Usually open by default |

All devices must be on the **same local network** (same router/subnet).

---

## Step 1 — Install on each device

```bash
# Clone or copy the repo, then from the project root:
pip install -e .
```

Verify it works:
```bash
localclaw --help
```

---

## Step 2 — Configure each agent

Run the setup wizard on each device:

```bash
localclaw setup
```

You will be asked:
- **Agent name** — give each device a unique name (e.g. `laptop-alice`, `macbook-bob`)
- **Which Mistral skills to enable** — see options below
- **Mistral model** — only if any Mistral skill is selected

### Option A — No API key (echo + capabilities only)

Press **Enter** to skip all Mistral skills. The agent runs fully offline.

```
Agent name [hostname]: macbook-bob

Always enabled: echo, capabilities

Mistral-powered skills (require MISTRAL_API_KEY):
  [1] summarize     - Summarize text
  [2] code          - Generate code from a description
  [3] ask           - General Q&A / chat
  [4] translate     - Translate text to another language
  [5] explain       - Explain code or a concept

Enter numbers to enable (e.g. 1,3), 'all', or press Enter to skip:
Skills:               ← press Enter

Config saved. Skills: echo, capabilities
```

### Option B — With Mistral API (any or all skills)

Enter numbers or `all`, then press Enter for the default model.

```
Agent name [hostname]: laptop-alice

Skills: all           ← or e.g. 1,2,3
Mistral model [mistral-small-latest]:    ← press Enter for default

Note: set MISTRAL_API_KEY in your environment before running.
```

Available Mistral skills:

| Skill | What it does | Example input |
|---|---|---|
| `summarize` | Condense long text | `"paste your long text here"` |
| `code` | Generate code | `"a REST API in Flask"` |
| `ask` | General Q&A | `"what is the difference between TCP and UDP?"` |
| `translate` | Translate text | `"bonjour"` (→ English) or use `--input '{"text":"...","to":"french"}'` |
| `explain` | Explain code or concepts | `"def f(x): return x*x"` |

Then create `~/.LocalClaw/.env` on that device:

**macOS/Linux:**
```bash
mkdir -p ~/.LocalClaw
echo "MISTRAL_API_KEY=your-key-here" > ~/.LocalClaw/.env
```

**Windows (PowerShell):**
```powershell
New-Item -Path "$env:USERPROFILE\.LocalClaw\.env" -ItemType File -Force
Add-Content -Path "$env:USERPROFILE\.LocalClaw\.env" -Value "MISTRAL_API_KEY=your-key-here"
```

> The `.env` file is **never committed to git** — it lives only on the local machine.

---

## Step 3 — Start each agent

On every device, run:

```bash
localclaw run
```

You will see:
```
LocalClaw running: agent_id=lc_xxxxxxxxxxxxxxxx name=laptop-alice listen=0.0.0.0:4117
Skills: echo, capabilities, summarize
Press Ctrl+C to stop.
```

Note the `agent_id` — you need it to address tasks to that agent.

---

## Step 4 — Find the LAN IP of the target device

**macOS:**
```bash
ipconfig getifaddr en0
# e.g. 192.168.1.42
```

**Windows (PowerShell):**
```powershell
(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Wi-Fi).IPAddress
# e.g. 192.168.1.55
```

---

## Step 5 — Send tasks between devices

Use `--direct <IP>:4117` to connect without relying on mDNS discovery.
Replace `lc_xxx` with the target agent's actual `agent_id`.

### Test connectivity (ping)

```bash
localclaw ping lc_xxxxxxxxxxxxxxxx --direct 192.168.1.42:4117
```

### Echo (works on any device, no API needed)

```bash
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill echo \
  --input "hello from across the room" --direct 192.168.1.42:4117
```

### Capabilities (see what skills the remote agent has)

```bash
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill capabilities \
  --input null --direct 192.168.1.42:4117
```

### Mistral skills with streaming (device must have API key configured)

All Mistral skills support streaming — add `--stream` to see tokens as they arrive.

**macOS/Linux:**
```bash
# Summarize
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill summarize \
  --input "Your long text here" --stream --direct 192.168.1.42:4117

# Generate code
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill code \
  --input "a Python function that checks if a number is prime" \
  --stream --direct 192.168.1.42:4117

# Ask a question
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill ask \
  --input "what is the difference between TCP and UDP?" \
  --stream --direct 192.168.1.42:4117

# Translate (defaults to English; pass {"text":"...","to":"french"} for other targets)
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill translate \
  --input "bonjour, comment allez-vous?" --stream --direct 192.168.1.42:4117

# Explain code or a concept
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill explain \
  --input "what is a TCP three-way handshake?" --stream --direct 192.168.1.42:4117
```

**Windows (PowerShell):** replace `\` line continuations with a backtick `` ` ``.

```powershell
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill code `
  --input "a Python function that checks if a number is prime" `
  --stream --direct 192.168.1.42:4117
```

---

## Auto-discovery with mDNS (optional)

If mDNS works on your network, agents discover each other automatically — no `--direct` needed.

```bash
# Scan for nearby agents
localclaw scan --timeout 10

# Send task using discovered peer (drop --direct)
localclaw send-task --peer lc_xxxxxxxxxxxxxxxx --skill echo --input "hi" --wait 10
```

mDNS works reliably on macOS. On Windows it may fail if:
- The network is set to "Public" in Windows settings → change it to "Private"
- Windows Firewall blocks multicast UDP 5353 → run `localclaw doctor` to diagnose

---

## Delegation (automatic skill routing)

If Agent A receives a task for a skill it doesn't have, but Agent B does, Agent A
automatically delegates to Agent B and returns the result.

Example: Agent A has only `echo` + `capabilities`. Agent B has `summarize`.
Send a `summarize` task to Agent A — it will delegate to Agent B transparently.

This requires both agents to be running and able to reach each other via TCP.

---

## LAN portal (Chromecast-style discovery view)

Start the watcher on one machine:

```bash
localclaw portal
```

It prints a LAN URL and a 6-digit PIN. Open the URL from your phone (same network),
enter the PIN once, and the portal will show discovered agents plus ping status.

---

## macOS firewall note

If macOS prompts "Do you want to allow incoming network connections?" when running
`localclaw run` — click **Allow**. This is needed for other devices to connect.

To pre-allow it:
```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add $(which python3)
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp $(which python3)
```

---

## Quick reference

| Command | Purpose |
|---|---|
| `localclaw setup` | Configure agent name, skills, model |
| `localclaw run` | Start agent (keep running) |
| `localclaw portal` | Start LAN portal (discover + ping + phone UI) |
| `localclaw scan` | Discover nearby agents via mDNS |
| `localclaw doctor` | Check network prerequisites |
| `localclaw ping <id> --direct HOST:PORT` | Test connectivity |
| `localclaw send-task --peer <id> --skill echo --input "hi" --direct HOST:PORT` | Send task |
| `localclaw send-task --peer <id> --skill summarize --input "..." --stream --direct HOST:PORT` | Summarize |
| `localclaw send-task --peer <id> --skill code --input "..." --stream --direct HOST:PORT` | Generate code |
| `localclaw send-task --peer <id> --skill ask --input "..." --stream --direct HOST:PORT` | Q&A |
| `localclaw send-task --peer <id> --skill translate --input "..." --stream --direct HOST:PORT` | Translate |
| `localclaw send-task --peer <id> --skill explain --input "..." --stream --direct HOST:PORT` | Explain |
