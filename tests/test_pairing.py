from __future__ import annotations

from localclaw.pairing import PairingManager


def test_pairing_manager_round_trip_and_persistence(tmp_path) -> None:
    store_path = tmp_path / "trusted.json"
    manager = PairingManager(
        agent_name="AgentA",
        agent_id="lc_a",
        store_path=store_path,
        pin_ttl_s=60,
    )

    started = manager.start_pairing("lc_portal", "Portal")
    assert started["ok"] is True
    assert started["pair_required"] is True
    assert started["already_paired"] is False

    pin = manager._pending["lc_portal"]["pin"]  # internal test probe
    verified = manager.verify_pairing("lc_portal", pin)
    assert verified["ok"] is True
    assert verified["paired"] is True

    status = manager.pairing_status("lc_portal")
    assert status["ok"] is True
    assert status["paired"] is True

    reloaded = PairingManager(
        agent_name="AgentA",
        agent_id="lc_a",
        store_path=store_path,
        pin_ttl_s=60,
    )
    persisted = reloaded.pairing_status("lc_portal")
    assert persisted["ok"] is True
    assert persisted["paired"] is True


def test_pairing_manager_rejects_invalid_pin(tmp_path) -> None:
    manager = PairingManager(
        agent_name="AgentA",
        agent_id="lc_a",
        store_path=tmp_path / "trusted.json",
        pin_ttl_s=60,
    )
    manager.start_pairing("lc_portal", "Portal")
    bad = manager.verify_pairing("lc_portal", "000000")
    assert bad["ok"] is False
    assert bad["error"] == "invalid_pin"
