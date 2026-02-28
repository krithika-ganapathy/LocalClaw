from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PORT = 4117
DEFAULT_STATUS = "idle"
DEFAULT_VERSION = "0.1.0"
DEFAULT_CONFIG_BASENAME = "config.yaml"


@dataclass(slots=True)
class AgentConfig:
    agent_name: str
    agent_port: int = DEFAULT_PORT
    caps: list[str] = field(default_factory=lambda: ["echo", "capabilities"])
    model: str = "none"
    status: str = DEFAULT_STATUS
    version: str = DEFAULT_VERSION
    trust: str = "unknown"
    agent_id: str | None = None
    bind_host: str = "0.0.0.0"
    advertise_host: str | None = None
    heartbeat_interval_s: float = 10.0
    heartbeat_timeout_s: float = 30.0
    max_transfer_bytes: int = 100 * 1024 * 1024
    stream_queue_size: int = 100
    portal_bind_host: str = "0.0.0.0"
    portal_port: int = 7420
    portal_ping_interval_s: float = 10.0
    portal_session_ttl_s: int = 86400

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_dir() -> Path:
    return Path.home() / ".LocalClaw"


def config_path(path: str | Path | None = None) -> Path:
    if path is None:
        return config_dir() / DEFAULT_CONFIG_BASENAME
    return Path(path).expanduser()


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    if text in {"null", "None", ""}:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text.strip('"').strip("'")


def _simple_yaml_parse(raw: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip()] = _parse_scalar(value)
    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(raw)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return _simple_yaml_parse(raw)


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore

        text = yaml.safe_dump(data, sort_keys=False)
    except Exception:
        lines = [f"{k}: {json.dumps(v) if isinstance(v, (list, dict)) else v}" for k, v in data.items()]
        text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")


def _identity_seed_path(base_dir: Path) -> Path:
    return base_dir / "identity.seed"


def _ensure_identity_seed(base_dir: Path) -> bytes:
    base_dir.mkdir(parents=True, exist_ok=True)
    seed_path = _identity_seed_path(base_dir)
    if seed_path.exists():
        return seed_path.read_bytes()
    seed = os.urandom(32)
    seed_path.write_bytes(seed)
    return seed


def derive_agent_id(seed: bytes) -> str:
    digest = hashlib.sha256(seed).hexdigest()
    return f"lc_{digest[:16]}"


def default_agent_name() -> str:
    return socket.gethostname() or "LocalClaw-Agent"


def load_config(path: str | Path | None = None) -> AgentConfig:
    cfg_path = config_path(path)
    raw = _load_yaml(cfg_path)

    raw_caps = raw.get("caps", ["echo", "capabilities"])
    if isinstance(raw_caps, str):
        caps = [part.strip() for part in raw_caps.split(",") if part.strip()]
    elif isinstance(raw_caps, list):
        caps = [str(item).strip() for item in raw_caps if str(item).strip()]
    else:
        caps = ["echo", "capabilities"]

    cfg = AgentConfig(
        agent_name=str(raw.get("agent_name") or default_agent_name()),
        agent_port=int(raw.get("agent_port", DEFAULT_PORT)),
        caps=caps,
        model=str(raw.get("model", "none")),
        status=str(raw.get("status", DEFAULT_STATUS)),
        version=str(raw.get("version", DEFAULT_VERSION)),
        trust=str(raw.get("trust", "unknown")),
        agent_id=(str(raw["agent_id"]) if raw.get("agent_id") else None),
        bind_host=str(raw.get("bind_host", "0.0.0.0")),
        advertise_host=(str(raw["advertise_host"]) if raw.get("advertise_host") else None),
        heartbeat_interval_s=float(raw.get("heartbeat_interval_s", 10.0)),
        heartbeat_timeout_s=float(raw.get("heartbeat_timeout_s", 30.0)),
        max_transfer_bytes=int(raw.get("max_transfer_bytes", 100 * 1024 * 1024)),
        stream_queue_size=int(raw.get("stream_queue_size", 100)),
        portal_bind_host=str(raw.get("portal_bind_host", "0.0.0.0")),
        portal_port=int(raw.get("portal_port", 7420)),
        portal_ping_interval_s=float(raw.get("portal_ping_interval_s", 10.0)),
        portal_session_ttl_s=int(raw.get("portal_session_ttl_s", 86400)),
    )

    if cfg.agent_id is None:
        seed = _ensure_identity_seed(cfg_path.parent)
        cfg.agent_id = derive_agent_id(seed)

    return cfg


def ensure_config(path: str | Path | None = None) -> Path:
    cfg_path = config_path(path)
    if cfg_path.exists():
        return cfg_path

    base = cfg_path.parent
    seed = _ensure_identity_seed(base)
    cfg = AgentConfig(
        agent_name=default_agent_name(),
        agent_id=derive_agent_id(seed),
    )
    _save_yaml(cfg_path, cfg.to_dict())
    return cfg_path


def resolved_advertise_host(config: AgentConfig) -> str:
    if config.advertise_host:
        return config.advertise_host

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        addr = sock.getsockname()[0]
    except OSError:
        addr = "127.0.0.1"
    finally:
        sock.close()
    return addr
