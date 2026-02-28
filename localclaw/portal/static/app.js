const state = {
  peers: new Map(),
  paired: false,
  pollTimer: null,
};

const pairingEl = document.getElementById("pairing");
const dashboardEl = document.getElementById("dashboard");
const pairForm = document.getElementById("pair-form");
const pinInput = document.getElementById("pair-pin");
const pairStatus = document.getElementById("pair-status");
const peersEl = document.getElementById("peers");
const metaLine = document.getElementById("meta-line");
const peerTemplate = document.getElementById("peer-template");

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

    const button = node.querySelector(".ping-btn");
    button.dataset.peerId = peer.peer_id;
    button.addEventListener("click", onPingClick);

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

async function onPingClick(event) {
  const button = event.currentTarget;
  const peerId = button.dataset.peerId;
  if (!peerId) {
    return;
  }
  button.disabled = true;
  try {
    await fetch(`/api/peers/${encodeURIComponent(peerId)}/ping`, { method: "POST" });
  } finally {
    button.disabled = false;
  }
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
