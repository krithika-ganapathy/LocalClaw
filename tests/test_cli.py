from __future__ import annotations

from types import SimpleNamespace

from localclaw.cli import _print_peer_table, build_parser
from localclaw.peer_store import PeerRecord, PeerStore


def test_config_flag_works_before_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["--config", "a.yaml", "run"])
    assert args.config == "a.yaml"
    assert args.command == "run"


def test_config_flag_works_after_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--config", "b.yaml"])
    assert args.config == "b.yaml"
    assert args.command == "run"


def test_peer_table_hides_self(capsys) -> None:
    store = PeerStore()
    store.upsert(PeerRecord(peer_id="lc_a", name="A", host="127.0.0.1", port=4117))
    store.upsert(PeerRecord(peer_id="lc_b", name="B", host="127.0.0.1", port=4118))

    node = SimpleNamespace(config=SimpleNamespace(agent_id="lc_a"), peer_store=store)
    _print_peer_table(node)

    out = capsys.readouterr().out
    assert "lc_b" in out
    assert "lc_a" not in out


def test_portal_parser_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["portal", "--bind", "0.0.0.0", "--port", "7420", "--ping-interval", "12"])
    assert args.command == "portal"
    assert args.bind == "0.0.0.0"
    assert args.port == 7420
    assert args.ping_interval == 12
