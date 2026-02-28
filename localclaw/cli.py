from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from .config import AgentConfig, _save_yaml, config_path, ensure_config, load_config, resolved_advertise_host
from .node import AgentNode
from .peer_store import PeerRecord
from .router import Router

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table

    _RICH = True
    _CONSOLE: Console | None = Console()
except Exception:  # pragma: no cover - optional dependency fallback
    _RICH = False
    _CONSOLE = None

try:
    import qrcode

    _QRCODE = True
except Exception:  # pragma: no cover - optional dependency fallback
    _QRCODE = False


def _print_line(message: str) -> None:
    if _RICH and _CONSOLE is not None:
        _CONSOLE.print(message)
        return
    print(message)


def _print_json(payload: Any) -> None:
    rendered = _format_json(payload)
    if _RICH and _CONSOLE is not None:
        _CONSOLE.print(Syntax(rendered, "json", word_wrap=False))
        return
    print(rendered)


def _print_qr(url: str) -> None:
    if not _QRCODE:
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = ["".join("██" if cell else "  " for cell in row) for row in matrix]
    ascii_qr = "\n".join(lines)

    if _RICH and _CONSOLE is not None:
        _CONSOLE.print(Panel.fit(ascii_qr, title="Scan to open portal", border_style="cyan"))
        return
    print(ascii_qr)


def _inject_direct_peer(node: AgentNode, peer_id: str, direct: str) -> None:
    """Inject a peer into the store directly from HOST:PORT, skipping mDNS."""
    host, _, port_str = direct.rpartition(":")
    if not host or not port_str.isdigit():
        raise ValueError(f"--direct must be HOST:PORT, got: {direct!r}")
    node.peer_store.upsert(
        PeerRecord(peer_id=peer_id, name=peer_id, host=host, port=int(port_str), source="direct")
    )


def _format_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _print_peer_table(node: AgentNode) -> None:
    self_id = node.config.agent_id
    peers = sorted(
        (peer for peer in node.peer_store.all() if peer.peer_id != self_id),
        key=lambda p: p.peer_id,
    )
    if not peers:
        _print_line("No peers discovered.")
        return
    if _RICH and _CONSOLE is not None:
        table = Table(title="Discovered Peers")
        table.add_column("Peer ID")
        table.add_column("Name")
        table.add_column("Host")
        table.add_column("Status")
        table.add_column("Caps")
        for peer in peers:
            table.add_row(
                peer.peer_id,
                peer.name,
                f"{peer.host}:{peer.port}",
                peer.status,
                ",".join(peer.caps),
            )
        _CONSOLE.print(table)
        return
    for peer in peers:
        _print_line(
            f"{peer.peer_id}  name={peer.name}  host={peer.host}:{peer.port} "
            f"status={peer.status} caps={','.join(peer.caps)}"
        )


def _run_doctor(config: AgentConfig) -> int:
    checks: list[tuple[str, bool, str]] = []

    udp_ok = False
    udp_detail = ""
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind(("", 5353))
        mreq = socket.inet_aton("224.0.0.251") + socket.inet_aton("0.0.0.0")
        udp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        udp_ok = True
        udp_detail = "multicast bind/join on 224.0.0.251:5353 succeeded"
    except OSError as exc:
        udp_detail = f"mDNS multicast check failed: {exc}"
    finally:
        udp_sock.close()
    checks.append(("mDNS multicast", udp_ok, udp_detail))

    tcp_ok = False
    tcp_detail = ""
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        tcp_sock.bind((config.bind_host, config.agent_port))
        tcp_sock.listen(1)
        tcp_ok = True
        tcp_detail = f"TCP bind/listen succeeded on {config.bind_host}:{config.agent_port}"
    except OSError as exc:
        tcp_detail = f"TCP bind failed on {config.bind_host}:{config.agent_port}: {exc}"
    finally:
        tcp_sock.close()
    checks.append(("TCP inbound", tcp_ok, tcp_detail))

    for name, ok, detail in checks:
        state = "OK" if ok else "FAIL"
        if _RICH and _CONSOLE is not None:
            style = "green" if ok else "red"
            _CONSOLE.print(f"[{style}][{state}][/{style}] {name}: {detail}")
        else:
            print(f"[{state}] {name}: {detail}")

    if sys.platform.startswith("win"):
        _print_line("Windows note: discovery can fail on Public networks or when firewall blocks multicast UDP 5353.")

    return 0 if all(ok for _, ok, _ in checks) else 2


def _cmd_setup(args: argparse.Namespace) -> int:
    cfg_path = config_path(args.config)
    ensure_config(args.config)
    cfg = load_config(args.config)

    if _RICH and _CONSOLE is not None:
        _CONSOLE.print(Panel.fit("LocalClaw Agent Setup", border_style="blue"))
    else:
        print("=== LocalClaw Agent Setup ===")
    _print_line(f"Config: {cfg_path}\n")

    name = input(f"Agent name [{cfg.agent_name}]: ").strip() or cfg.agent_name

    MISTRAL_SKILLS: list[tuple[str, str]] = [
        ("summarize", "Summarize text"),
        ("code",      "Generate code from a description"),
        ("ask",       "General Q&A / chat"),
        ("translate", "Translate text to another language"),
        ("explain",   "Explain code or a concept"),
    ]

    _print_line("\nAlways enabled: echo, capabilities")
    _print_line("\nMistral-powered skills (require MISTRAL_API_KEY):")
    for i, (skill_name, desc) in enumerate(MISTRAL_SKILLS, 1):
        tag = " [on]" if skill_name in cfg.caps else ""
        _print_line(f"  [{i}] {skill_name:<12} - {desc}{tag}")

    _print_line("\nEnter numbers to enable (e.g. 1,3), 'all', or press Enter to skip:")
    raw = input("Skills: ").strip().lower()

    if raw in ("all", "a"):
        chosen = {n for n, _ in MISTRAL_SKILLS}
    elif raw:
        chosen: set[str] = set()
        for part in raw.replace(" ", "").split(","):
            if part.isdigit() and 1 <= int(part) <= len(MISTRAL_SKILLS):
                chosen.add(MISTRAL_SKILLS[int(part) - 1][0])
    else:
        chosen = set()

    model = cfg.model if cfg.model not in ("none", "", None) else "none"
    if chosen:
        default_model = model if model != "none" else "mistral-small-latest"
        model = input(f"Mistral model [{default_model}]: ").strip() or default_model
        _print_line("\nNote: set MISTRAL_API_KEY in your environment before running 'localclaw run'.")

    caps = ["echo", "capabilities"] + sorted(chosen)

    data: dict[str, Any] = {
        "agent_name": name,
        "agent_port": cfg.agent_port,
        "caps": caps,
        "model": model,
        "status": cfg.status,
        "version": cfg.version,
        "trust": cfg.trust,
        "bind_host": cfg.bind_host,
    }
    if cfg.agent_id:
        data["agent_id"] = cfg.agent_id
    if cfg.advertise_host:
        data["advertise_host"] = cfg.advertise_host

    _save_yaml(cfg_path, data)

    final = load_config(args.config)
    _print_line(f"\nConfig saved to {cfg_path}")
    _print_line(f"Agent ID : {final.agent_id}")
    _print_line(f"Skills   : {', '.join(final.caps)}")
    _print_line(f"Model    : {final.model}")
    return 0


async def _cmd_print_config(args: argparse.Namespace) -> int:
    if args.ensure:
        ensure_config(args.config)
    cfg = load_config(args.config)
    _print_json(cfg.to_dict())
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)

    from .agent import build_agent

    agent = build_agent(cfg)
    await agent.start(with_discovery=not args.no_discovery, with_transport=True)

    _print_line(
        f"LocalClaw running: agent_id={cfg.agent_id} name={cfg.agent_name} "
        f"listen={cfg.bind_host}:{cfg.agent_port}"
    )
    _print_line(f"Skills: {', '.join(cfg.caps)}")
    _print_line("Press Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(2.0)
            if args.show_peers:
                _print_peer_table(agent.node)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()
    return 0


async def _cmd_scan(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    await node.start(with_discovery=True, with_transport=False)
    try:
        end = time.time() + args.timeout
        while time.time() < end:
            node.peer_store.expire_stale()
            await asyncio.sleep(0.1)
        _print_peer_table(node)
    finally:
        await node.stop()
    return 0


async def _cmd_ping(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    direct = getattr(args, "direct", None)
    await node.start(with_discovery=not direct, with_transport=True)
    try:
        if direct:
            _inject_direct_peer(node, args.peer_id, direct)
        elif args.wait > 0:
            await node.wait_for_peer(args.peer_id, timeout=args.wait)
        response = await node.ping(args.peer_id, timeout=args.timeout)
        _print_json(response)
        return 0
    finally:
        await node.stop()


async def _cmd_send_task(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    direct = getattr(args, "direct", None)
    await node.start(with_discovery=not direct, with_transport=True)
    try:
        if direct:
            _inject_direct_peer(node, args.peer_id, direct)
        elif args.wait > 0:
            await node.wait_for_peer(args.peer_id, timeout=args.wait)

        payload = Router.parse_json_input(args.input)
        if args.stream:
            task_id, future = await node.send_task_streaming(
                peer_id=args.peer_id,
                skill=args.skill,
                input_data=payload,
                timeout=args.timeout,
            )

            async def consume_stream() -> None:
                async for event in node.router.stream(task_id):
                    _print_json(event)

            stream_task = asyncio.create_task(consume_stream())
            result = await asyncio.wait_for(future, timeout=args.timeout)
            await stream_task
            _print_json(result)
            return 0

        result = await node.send_task(
            peer_id=args.peer_id,
            skill=args.skill,
            input_data=payload,
            timeout=args.timeout,
        )
        _print_json(result)
        return 0
    finally:
        await node.stop()


async def _cmd_send_file(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    await node.start(with_discovery=True, with_transport=True)
    try:
        if args.wait > 0:
            await node.wait_for_peer(args.peer_id, timeout=args.wait)
        result = await node.send_file(args.peer_id, Path(args.path), timeout=args.timeout)
        _print_json(result)
        return 0
    finally:
        await node.stop()


async def _cmd_capability_query(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    await node.start(with_discovery=True, with_transport=True)
    try:
        if args.wait > 0:
            await node.wait_for_peer(args.peer_id, timeout=args.wait)
        response = await node.capability_query(args.peer_id, timeout=args.timeout)
        _print_json(response)
        return 0
    finally:
        await node.stop()


async def _cmd_portal(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)

    bind_host = args.bind if args.bind is not None else cfg.portal_bind_host
    port = int(args.port if args.port is not None else cfg.portal_port)
    ping_interval_s = float(
        args.ping_interval if args.ping_interval is not None else cfg.portal_ping_interval_s
    )

    from .portal import PortalAuth, PortalService
    from .portal.api import create_portal_app

    import uvicorn

    service = PortalService(
        cfg,
        ping_interval_s=ping_interval_s,
        with_discovery=True,
    )
    auth = PortalAuth(enabled=bool(args.require_pin), session_ttl_s=cfg.portal_session_ttl_s)
    app = create_portal_app(service, auth)

    lan_host = resolved_advertise_host(cfg) if bind_host == "0.0.0.0" else bind_host
    hostname_local = f"{socket.gethostname().rstrip('.').removesuffix('.local')}.local"
    lan_url = f"http://{lan_host}:{port}"
    _print_line(f"LocalClaw portal starting on {bind_host}:{port}")
    if auth.enabled:
        _print_line(f"Pairing PIN: {auth.pin}")
    else:
        _print_line("Portal auth: disabled (use --require-pin to enable pairing PIN).")
    _print_line(f"URL (LAN): {lan_url}")
    _print_line(f"URL (hostname): http://{hostname_local}:{port}")
    _print_line("Press Ctrl+C to stop.")
    _print_qr(lan_url)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=bind_host,
            port=port,
            log_level="info",
        )
    )
    await server.serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localclaw",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            LocalClaw — LAN-native multi-agent protocol.
            Commands: setup, run, portal, scan, ping, send-task, send-file, capability-query, doctor.
            """
        ),
    )
    config_help = "Optional config path (default: ~/.LocalClaw/config.yaml)"
    parser.add_argument("--config", default=None, help=config_help)

    sub = parser.add_subparsers(dest="command", required=True)

    cmd_setup = sub.add_parser("setup", help="Interactive agent setup wizard")
    cmd_setup.add_argument("--config", default=argparse.SUPPRESS, help=config_help)

    cmd_print = sub.add_parser("print-config", help="Print parsed config and derived agent_id")
    cmd_print.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_print.add_argument("--ensure", action="store_true", help="Create default config if missing")

    cmd_run = sub.add_parser("run", help="Run LocalClaw node (mDNS + TCP server)")
    cmd_run.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_run.add_argument("--no-discovery", action="store_true", help="Disable mDNS announce+browse")
    cmd_run.add_argument("--show-peers", action="store_true", help="Print discovered peer table every loop")

    cmd_scan = sub.add_parser("scan", help="Browse mDNS peers for a duration")
    cmd_scan.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_scan.add_argument("--timeout", type=float, default=5.0)

    cmd_doctor = sub.add_parser("doctor", help="Run network diagnostics for mDNS and inbound TCP")
    cmd_doctor.add_argument("--config", default=argparse.SUPPRESS, help=config_help)

    cmd_ping = sub.add_parser("ping", help="Send heartbeat ping to a peer_id")
    cmd_ping.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_ping.add_argument("peer_id")
    cmd_ping.add_argument("--timeout", type=float, default=5.0)
    cmd_ping.add_argument("--wait", type=float, default=5.0, help="Wait for peer discovery before ping")
    cmd_ping.add_argument("--direct", default=None, metavar="HOST:PORT", help="Skip mDNS, connect directly")

    cmd_task = sub.add_parser("send-task", help="Send task to a peer")
    cmd_task.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_task.add_argument("--peer", dest="peer_id", required=True)
    cmd_task.add_argument("--skill", required=True)
    cmd_task.add_argument("--input", required=True, help="JSON string or raw string")
    cmd_task.add_argument("--stream", action="store_true", help="Print stream messages before final result")
    cmd_task.add_argument("--timeout", type=float, default=30.0)
    cmd_task.add_argument("--wait", type=float, default=5.0)
    cmd_task.add_argument("--direct", default=None, metavar="HOST:PORT", help="Skip mDNS, connect directly")

    cmd_file = sub.add_parser("send-file", help="Send file payload to a peer")
    cmd_file.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_file.add_argument("--peer", dest="peer_id", required=True)
    cmd_file.add_argument("--path", required=True)
    cmd_file.add_argument("--timeout", type=float, default=60.0)
    cmd_file.add_argument("--wait", type=float, default=5.0)

    cmd_caps = sub.add_parser("capability-query", help="Request capability manifest from peer")
    cmd_caps.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_caps.add_argument("--peer", dest="peer_id", required=True)
    cmd_caps.add_argument("--timeout", type=float, default=10.0)
    cmd_caps.add_argument("--wait", type=float, default=5.0)

    cmd_portal = sub.add_parser("portal", help="Run LAN portal (discovery + periodic ping + web UI)")
    cmd_portal.add_argument("--config", default=argparse.SUPPRESS, help=config_help)
    cmd_portal.add_argument("--bind", default=None, help="Portal HTTP bind host (default from config)")
    cmd_portal.add_argument("--port", type=int, default=None, help="Portal HTTP port (default from config)")
    cmd_portal.add_argument(
        "--ping-interval",
        type=float,
        default=None,
        help="Periodic ping interval in seconds (default from config)",
    )
    cmd_portal.add_argument(
        "--require-pin",
        action="store_true",
        help="Require portal PIN pairing before API/UI access",
    )

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "setup":
        return _cmd_setup(args)
    if args.command == "print-config":
        return await _cmd_print_config(args)
    if args.command == "run":
        return await _cmd_run(args)
    if args.command == "scan":
        return await _cmd_scan(args)
    if args.command == "doctor":
        cfg = load_config(args.config)
        return _run_doctor(cfg)
    if args.command == "ping":
        return await _cmd_ping(args)
    if args.command == "send-task":
        return await _cmd_send_task(args)
    if args.command == "send-file":
        return await _cmd_send_file(args)
    if args.command == "capability-query":
        return await _cmd_capability_query(args)
    if args.command == "portal":
        return await _cmd_portal(args)
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        return 130
    except TimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 124
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
