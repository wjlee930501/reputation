"""get_request_ip right-to-left X-Forwarded-For parsing (Codex review fix).

The leftmost XFF entry is client-supplied and spoofable; the real client is the
rightmost entry not in TRUSTED_PROXY_IPS.
"""
from types import SimpleNamespace

from app.core import rate_limit


def _req(ip, xff=None):
    headers = {}
    if xff:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(
        client=SimpleNamespace(host=ip),
        headers=SimpleNamespace(get=lambda key, default=None: headers.get(key.lower(), default)),
    )


def test_untrusted_remote_ignores_forwarded(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/8"])
    # Direct (untrusted) peer → never trust XFF, use the connection IP.
    assert rate_limit.get_request_ip(_req("203.0.113.9", xff="1.1.1.1")) == "203.0.113.9"


def test_rightmost_untrusted_shadows_spoofed_leftmost(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/8"])
    # Trusted proxy peer; chain = <spoofed>, <real client (LB-observed)>, <proxy>.
    # The real client (203.0.113.7) shadows the attacker-supplied 9.9.9.9.
    req = _req("10.1.2.3", xff="9.9.9.9, 203.0.113.7, 10.9.9.9")
    assert rate_limit.get_request_ip(req) == "203.0.113.7"


def test_single_forwarded_entry(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/8"])
    assert rate_limit.get_request_ip(_req("10.1.2.3", xff="203.0.113.7")) == "203.0.113.7"


def test_all_trusted_falls_back_to_remote(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/8"])
    # No untrusted entry → fall back to the connection IP (safe, not spoofable).
    assert rate_limit.get_request_ip(_req("10.1.2.3", xff="10.4.5.6")) == "10.1.2.3"
