const state = {
  peers: new Map(),
  paired: false,
  pollTimer: null,
  chatPeerId: null,
  chatHistory: new Map(),
};

const pairingEl = document.getElementById("pairing");
const dashboardEl = document.getElementById("dashboard");
const pairForm = document.getElementById("pair-form");
const pinInput = document.getElementById("pair-pin");
const pairStatus = document.getElementById("pair-status");
const peersEl = document.getElementById("peers");
const metaLine = document.getElementById("meta-line");
const peerTemplate = document.getElementById("peer-template");

const chatViewEl = document.getElementById("chat-view");
const chatBackBtn = document.getElementById("chat-back");
const chatTitleEl = document.getElementById("chat-title");
const chatMetaEl = document.getElementById("chat-meta");
const chatPairingEl = document.getElementById("chat-pairing");
const chatPairForm = document.getElementById("chat-pair-form");
const chatPairPinInput = document.getElementById("chat-pair-pin");
const chatPairStatus = document.getElementById("chat-pair-status");
const chatPanelEl = document.getElementById("chat-panel");
const chatSkillEl = document.getElementById("chat-skill");
const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInputEl = document.getElementById("chat-input");

bootstrap().catch(() => {
  pairingEl.classList.remove("hidden");
  pairStatus.textContent = "Failed to connect to portal API.";
});

pairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pin = pinInput.value.trim();
  if (!pin) {
    return;
  }
  pairStatus.textContent = "Pairing...";

  const response = await fetch("/api/auth/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }),
  });

  if (!response.ok) {
    pairStatus.textContent = "Invalid PIN";
    return;
  }

  state.paired = true;
  pairingEl.classList.add("hidden");
  dashboardEl.classList.remove("hidden");
  pairStatus.textContent = "";
  pinInput.value = "";

  await refreshMeta();
  await refreshPeers();
  startEventStream();
});

chatBackBtn.addEventListener("click", () => {
  state.chatPeerId = null;
  chatViewEl.classList.add("hidden");
  dashboardEl.classList.remove("hidden");
});

chatPairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const peerId = state.chatPeerId;
  if (!peerId) {
    return;
  }
  const pin = chatPairPinInput.value.trim();
  if (!pin) {
    return;
  }
  chatPairStatus.textContent = "Verifying...";

  const response = await fetch(`/api/peers/${encodeURIComponent(peerId)}/chat/pair/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }),
  });
  if (!response.ok) {
    chatPairStatus.textContent = "Verification failed.";
    return;
  }
  const payload = await response.json();
  if (!payload.ok) {
    chatPairStatus.textContent = payload.message || payload.error || "Invalid PIN";
    return;
  }
  chatPairStatus.textContent = "Paired. You can start chatting.";
  chatPairPinInput.value = "";
  showChatPanel();
  addChatMessage(peerId, "system", "Pairing complete.");
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const peerId = state.chatPeerId;
  if (!peerId) {
    return;
  }
  const message = chatInputEl.value.trim();
  if (!message) {
    return;
  }
  const skill = chatSkillEl.value || null;
  chatInputEl.value = "";
  addChatMessage(peerId, "user", message);

  const submitButton = chatForm.querySelector("button[type=submit]");
  if (submitButton) {
    submitButton.disabled = true;
  }
  try {
    const response = await fetch(`/api/peers/${encodeURIComponent(peerId)}/chat/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, skill, timeout_s: 45 }),
    });
    if (!response.ok) {
      addChatMessage(peerId, "system", "Failed to send message.");
      return;
    }
    const payload = await response.json();
    if (!payload.ok) {
      addChatMessage(peerId, "system", payload.message || payload.error || "Request failed.");
      if (payload.error === "pairing_required") {
        showChatPairing();
      }
      return;
    }
    addChatMessage(peerId, "agent", String(payload.message || ""));
  } finally {
    if (submitButton) {
      submitButton.disabled = false;
    }
  }
});

async function bootstrap() {
  const response = await fetch("/api/meta");
  if (!response.ok) {
    pairingEl.classList.remove("hidden");
    return;
  }
  const meta = await response.json();
  if (meta.auth_required) {
    pairingEl.classList.remove("hidden");
    return;
  }

  state.paired = true;
  pairingEl.classList.add("hidden");
  dashboardEl.classList.remove("hidden");
  await refreshMeta();
  await refreshPeers();
  startEventStream();
}

async function refreshMeta() {
  const response = await fetch("/api/meta");
  if (!response.ok) {
    return;
  }
  const meta = await response.json();
  metaLine.textContent = `${meta.peer_count} agents discovered · ping interval ${meta.ping_interval_s}s`;
}

async function refreshPeers() {
  const response = await fetch("/api/peers");
  if (!response.ok) {
    return;
  }
  const payload = await response.json();
  for (const peer of payload.peers || []) {
    state.peers.set(peer.peer_id, peer);
  }
  renderPeers();
  await refreshMeta();
}

function renderPeers() {
  const peers = [...state.peers.values()].sort((a, b) => {
    return (a.name || "").localeCompare(b.name || "");
  });

  peersEl.textContent = "";
  if (!peers.length) {
    const empty = document.createElement("p");
    empty.className = "status";
    empty.textContent = "No agents discovered yet.";
    peersEl.appendChild(empty);
    return;
  }

  for (const peer of peers) {
    const node = peerTemplate.content.cloneNode(true);
    node.querySelector(".peer-name").textContent = peer.name;
    node.querySelector(".peer-id").textContent = peer.peer_id;
    node.querySelector(".peer-host").textContent = `${peer.host}:${peer.port}`;
    node.querySelector(".peer-caps").textContent = `Caps: ${(peer.caps || []).join(", ") || "none"}`;
    node.querySelector(".peer-ping").textContent = formatPing(peer);

    const pill = node.querySelector(".pill");
    applyStatus(pill, peer.reachable);

    const button = node.querySelector(".chat-btn");
    button.dataset.peerId = peer.peer_id;
    button.addEventListener("click", onChatClick);

    peersEl.appendChild(node);
  }
}

function applyStatus(el, reachable) {
  el.classList.remove("ok", "warn", "bad");
  if (reachable === true) {
    el.textContent = "Online";
    el.classList.add("ok");
    return;
  }
  if (reachable === false) {
    el.textContent = "Offline";
    el.classList.add("bad");
    return;
  }
  el.textContent = "Unknown";
  el.classList.add("warn");
}

function formatPing(peer) {
  if (peer.last_ping_at == null) {
    return "Last ping: never";
  }
  const agoSec = Math.max(0, Math.floor(Date.now() / 1000 - peer.last_ping_at));
  const ago = agoSec < 60 ? `${agoSec}s ago` : `${Math.floor(agoSec / 60)}m ago`;
  if (peer.reachable) {
    return `Last ping: ${ago} · ${peer.last_rtt_ms ?? "?"}ms`;
  }
  return `Last ping: ${ago} · failed${peer.last_error ? ` (${peer.last_error})` : ""}`;
}

async function onChatClick(event) {
  const button = event.currentTarget;
  const peerId = button.dataset.peerId;
  if (!peerId) {
    return;
  }
  button.disabled = true;
  try {
    await openChat(peerId);
  } finally {
    button.disabled = false;
  }
}

async function openChat(peerId) {
  const peer = state.peers.get(peerId);
  state.chatPeerId = peerId;
  dashboardEl.classList.add("hidden");
  chatViewEl.classList.remove("hidden");
  chatTitleEl.textContent = `Chat · ${peer?.name || peerId}`;

  const response = await fetch(`/api/peers/${encodeURIComponent(peerId)}/chat`);
  if (!response.ok) {
    chatMetaEl.textContent = "Failed to load chat state.";
    showChatPairing();
    return;
  }
  const payload = await response.json();

  const skills = payload.skills || [];
  renderSkillOptions(skills, payload.default_skill);
  chatMetaEl.textContent = `${peer?.host || "?"}:${peer?.port || "?"} · skills: ${skills.join(", ") || "none"}`;
  renderHistory(peerId);

  const pairing = payload.pairing || {};
  if (pairing.pair_required && !pairing.paired) {
    showChatPairing();
    chatPairStatus.textContent = "Requesting PIN from agent...";
    await requestPairingPin(peerId);
    return;
  }
  showChatPanel();
}

async function requestPairingPin(peerId) {
  const response = await fetch(`/api/peers/${encodeURIComponent(peerId)}/chat/pair/start`, {
    method: "POST",
  });
  if (!response.ok) {
    chatPairStatus.textContent = "Could not request pairing PIN.";
    return;
  }
  const payload = await response.json();
  if (!payload.ok) {
    chatPairStatus.textContent = payload.message || payload.error || "Could not request pairing PIN.";
    return;
  }
  if (payload.pair_required) {
    chatPairStatus.textContent = "PIN requested. Enter the 6-digit PIN from that agent's CLI.";
    return;
  }
  chatPairStatus.textContent = "Already paired.";
  showChatPanel();
}

function renderSkillOptions(skills, defaultSkill) {
  chatSkillEl.textContent = "";
  for (const skill of skills) {
    const option = document.createElement("option");
    option.value = skill;
    option.textContent = skill;
    if (skill === defaultSkill) {
      option.selected = true;
    }
    chatSkillEl.appendChild(option);
  }
}

function renderHistory(peerId) {
  chatMessagesEl.textContent = "";
  const history = state.chatHistory.get(peerId) || [];
  for (const item of history) {
    const row = document.createElement("div");
    row.className = `chat-row ${item.role}`;
    const role = document.createElement("div");
    role.className = "chat-role";
    role.textContent = item.role === "user" ? "You" : item.role === "agent" ? "Agent" : "System";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = item.text;
    row.appendChild(role);
    row.appendChild(bubble);
    chatMessagesEl.appendChild(row);
  }
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function addChatMessage(peerId, role, text) {
  const history = state.chatHistory.get(peerId) || [];
  history.push({ role, text: String(text || "") });
  state.chatHistory.set(peerId, history);
  if (state.chatPeerId === peerId) {
    renderHistory(peerId);
  }
}

function showChatPairing() {
  chatPairingEl.classList.remove("hidden");
  chatPanelEl.classList.add("hidden");
}

function showChatPanel() {
  chatPairingEl.classList.add("hidden");
  chatPanelEl.classList.remove("hidden");
}

function startEventStream() {
  if (!window.EventSource) {
    startPollingFallback();
    return;
  }

  const source = new EventSource("/api/events");
  source.addEventListener("snapshot", (event) => {
    const payload = parseEvent(event);
    if (!payload) {
      return;
    }
    state.peers.clear();
    for (const peer of payload.peers || []) {
      state.peers.set(peer.peer_id, peer);
    }
    renderPeers();
    refreshMeta();
  });

  source.addEventListener("peer_upsert", (event) => {
    const payload = parseEvent(event);
    if (!payload || !payload.peer_id) {
      return;
    }
    state.peers.set(payload.peer_id, payload);
    renderPeers();
    refreshMeta();
  });

  source.addEventListener("peer_remove", (event) => {
    const payload = parseEvent(event);
    if (!payload || !payload.peer_id) {
      return;
    }
    state.peers.delete(payload.peer_id);
    renderPeers();
    refreshMeta();
  });

  source.addEventListener("ping_result", (event) => {
    const payload = parseEvent(event);
    const peer = payload?.state;
    if (!peer || !peer.peer_id) {
      return;
    }
    state.peers.set(peer.peer_id, peer);
    renderPeers();
    refreshMeta();
  });

  source.onerror = () => {
    source.close();
    startPollingFallback();
  };
}

function startPollingFallback() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
  }
  state.pollTimer = setInterval(() => {
    refreshPeers().catch(() => {});
  }, 5000);
}

function parseEvent(event) {
  try {
    return JSON.parse(event.data);
  } catch {
    return null;
  }
}
