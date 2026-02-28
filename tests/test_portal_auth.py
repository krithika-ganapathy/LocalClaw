from __future__ import annotations

from localclaw.portal.auth import PortalAuth


def test_pair_pin_verification() -> None:
    auth = PortalAuth(pin="123456", session_ttl_s=3600, secret=b"x" * 32)
    assert auth.verify_pin("123456") is True
    assert auth.verify_pin("000000") is False


def test_session_token_valid_and_expired() -> None:
    auth = PortalAuth(pin="123456", session_ttl_s=10, secret=b"x" * 32)
    token, exp = auth.issue_session_token(now=100)
    assert exp == 110
    assert auth.verify_session_token(token, now=109) is True
    assert auth.verify_session_token(token, now=111) is False


def test_session_token_rejects_tampering() -> None:
    auth = PortalAuth(pin="123456", session_ttl_s=10, secret=b"x" * 32)
    token, _ = auth.issue_session_token(now=100)
    assert auth.verify_session_token(token, now=100) is True

    tampered = token + "a"
    assert auth.verify_session_token(tampered) is False
    assert auth.verify_session_token("not-a-token") is False
