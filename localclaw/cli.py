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

from .config import AgentConfig, ensure_config, load_config
from .node import AgentNode
from .router import Router


def _format_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _print_peer_table(node: AgentNode) -> None:
    peers = sorted(node.peer_store.all(), key=lambda p: p.peer_id)
    if not peers:
        print("No peers discovered.")
        return
    for peer in peers:
        print(
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
        print(f"[{state}] {name}: {detail}")

    if sys.platform.startswith("win"):
        print("Windows note: discovery can fail on Public networks or when firewall blocks multicast UDP 5353.")

    return 0 if all(ok for _, ok, _ in checks) else 2


async def _cmd_print_config(args: argparse.Namespace) -> int:
    if args.ensure:
        ensure_config(args.config)
    cfg = load_config(args.config)
    print(_format_json(cfg.to_dict()))
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    await node.start(with_discovery=not args.no_discovery, with_transport=True)

    print(
        f"LocalClaw running: agent_id={cfg.agent_id} name={cfg.agent_name} "
        f"listen={cfg.bind_host}:{cfg.agent_port}"
    )
    print("Press Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(2.0)
            if args.show_peers:
                _print_peer_table(node)
    except KeyboardInterrupt:
        pass
    finally:
        await node.stop()
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
    await node.start(with_discovery=True, with_transport=True)
    try:
        if args.wait > 0:
            await node.wait_for_peer(args.peer_id, timeout=args.wait)
        response = await node.ping(args.peer_id, timeout=args.timeout)
        print(_format_json(response))
        return 0
    finally:
        await node.stop()


async def _cmd_send_task(args: argparse.Namespace) -> int:
    ensure_config(args.config)
    cfg = load_config(args.config)
    node = AgentNode(cfg)
    await node.start(with_discovery=True, with_transport=True)
    try:
        if args.wait > 0:
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
                    print(_format_json(event))

            stream_task = asyncio.create_task(consume_stream())
            result = await asyncio.wait_for(future, timeout=args.timeout)
            await stream_task
            print(_format_json(result))
            return 0

        result = await node.send_task(
            peer_id=args.peer_id,
            skill=args.skill,
            input_data=payload,
            timeout=args.timeout,
        )
        print(_format_json(result))
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
        print(_format_json(result))
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
        print(_format_json(response))
        return 0
    finally:
        await node.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localclaw",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            LocalClaw Person A network substrate CLI.
            Commands support discovery, transport health checks, ping, tasks, and file transfer.
            """
        ),
    )
    parser.add_argument("--config", default=None, help="Optional config path (default: ~/.LocalClaw/config.yaml)")

    sub = parser.add_subparsers(dest="command", required=True)

    cmd_print = sub.add_parser("print-config", help="Print parsed config and derived agent_id")
    cmd_print.add_argument("--ensure", action="store_true", help="Create default config if missing")

    cmd_run = sub.add_parser("run", help="Run LocalClaw node (mDNS + TCP server)")
    cmd_run.add_argument("--no-discovery", action="store_true", help="Disable mDNS announce+browse")
    cmd_run.add_argument("--show-peers", action="store_true", help="Print discovered peer table every loop")

    cmd_scan = sub.add_parser("scan", help="Browse mDNS peers for a duration")
    cmd_scan.add_argument("--timeout", type=float, default=5.0)

    sub.add_parser("doctor", help="Run network diagnostics for mDNS and inbound TCP")

    cmd_ping = sub.add_parser("ping", help="Send heartbeat ping to a peer_id")
    cmd_ping.add_argument("peer_id")
    cmd_ping.add_argument("--timeout", type=float, default=5.0)
    cmd_ping.add_argument("--wait", type=float, default=5.0, help="Wait for peer discovery before ping")

    cmd_task = sub.add_parser("send-task", help="Send task to a peer")
    cmd_task.add_argument("--peer", dest="peer_id", required=True)
    cmd_task.add_argument("--skill", required=True)
    cmd_task.add_argument("--input", required=True, help="JSON string or raw string")
    cmd_task.add_argument("--stream", action="store_true", help="Print stream messages before final result")
    cmd_task.add_argument("--timeout", type=float, default=30.0)
    cmd_task.add_argument("--wait", type=float, default=5.0)

    cmd_file = sub.add_parser("send-file", help="Send file payload to a peer")
    cmd_file.add_argument("--peer", dest="peer_id", required=True)
    cmd_file.add_argument("--path", required=True)
    cmd_file.add_argument("--timeout", type=float, default=60.0)
    cmd_file.add_argument("--wait", type=float, default=5.0)

    cmd_caps = sub.add_parser("capability-query", help="Request capability manifest from peer")
    cmd_caps.add_argument("--peer", dest="peer_id", required=True)
    cmd_caps.add_argument("--timeout", type=float, default=10.0)
    cmd_caps.add_argument("--wait", type=float, default=5.0)

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
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
