from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[3] / "scripts" / "certbot" / "godaddy_hook.py"
SPEC = importlib.util.spec_from_file_location("godaddy_hook", HOOK_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

@pytest.mark.parametrize(
    ("domain", "zone_override", "match"),
    [
        ("_acme-challenge.example.com", "other.com", "GODADDY_ZONE"),
        ("_acme-challenge.example.com", "", None),
    ],
)
def test_zone_and_name_validation_and_derivation(domain, zone_override, match, capsys):
    if match:
        with pytest.raises(RuntimeError, match=match):
            MODULE._zone_and_name(domain, zone_override)
        return
    zone, host = MODULE._zone_and_name(domain, zone_override)
    captured = capsys.readouterr()
    assert zone == "example.com"
    assert host == "_acme-challenge"
    assert "derived zone 'example.com'" in captured.out

def test_emit_log_writes_to_configured_log_file(tmp_path, capsys, monkeypatch):
    log_path = tmp_path / "hook.log"
    monkeypatch.setattr(MODULE, "HOOK_LOG_PATH", str(log_path))

    MODULE._emit_log("diagnostic-message")

    captured = capsys.readouterr()
    assert "diagnostic-message" in captured.out
    assert "diagnostic-message" in log_path.read_text(encoding="utf-8")

def test_fetch_existing_txt_values_returns_empty_for_404(monkeypatch):
    class Response:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(MODULE, "_godaddy_request", lambda *_args, **_kwargs: Response())

    assert MODULE._fetch_existing_txt_values("example.com", "_acme-challenge") == []

def test_upsert_txt_record_replaces_existing_records(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.setenv("GODADDY_DNS_WAIT_SECONDS", "0")

    monkeypatch.setattr(MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: ["old"])

    class Response:
        status_code = 200
        text = "ok"

    def fake_request(method, path, *, payload=None):
        calls.append((method, path, payload))
        return Response()

    monkeypatch.setattr(MODULE, "_godaddy_request", fake_request)

    wait_calls: list[dict[str, object]] = []

    def fake_wait(**kwargs):
        wait_calls.append(kwargs)

    monkeypatch.setattr(MODULE, "_wait_for_dns_txt_propagation", fake_wait)
    monkeypatch.setattr(MODULE, "_wait_for_public_recursive_txt_propagation", fake_wait)

    MODULE._upsert_txt_record()

    assert calls == [
        (
            "PUT",
            "/v1/domains/example.com/records/TXT/_acme-challenge",
            [
                {"data": "new-value", "ttl": 600},
                {"data": "old", "ttl": 600},
            ],
        )
    ]
    assert wait_calls == []

def test_upsert_txt_record_uses_300_second_default_wait(monkeypatch):
    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.delenv("GODADDY_DNS_WAIT_SECONDS", raising=False)

    class Response:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(MODULE, "_godaddy_request", lambda *_args, **_kwargs: Response())

    wait_calls: list[dict[str, object]] = []

    def fake_wait(**kwargs):
        wait_calls.append(kwargs)

    monkeypatch.setattr(MODULE, "_wait_for_dns_txt_propagation", fake_wait)
    monkeypatch.setattr(MODULE, "_wait_for_public_recursive_txt_propagation", fake_wait)

    MODULE._upsert_txt_record()

    assert wait_calls[0]["timeout_seconds"] == 300
    assert wait_calls[1]["timeout_seconds"] == 300

def test_cleanup_txt_record_removes_only_current_validation_value(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.setattr(
        MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: ["old", "new-value"]
    )

    class Response:
        status_code = 200
        text = "ok"

    def fake_request(method, path, *, payload=None):
        calls.append((method, path, payload))
        return Response()

    monkeypatch.setattr(MODULE, "_godaddy_request", fake_request)

    MODULE._cleanup_txt_record()

    assert calls == [
        (
            "PUT",
            "/v1/domains/example.com/records/TXT/_acme-challenge",
            [{"data": "old", "ttl": 600}],
        )
    ]

def test_cleanup_txt_record_requires_validation_env(monkeypatch):
    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.delenv("CERTBOT_VALIDATION", raising=False)

    with pytest.raises(RuntimeError, match="CERTBOT_VALIDATION"):
        MODULE._cleanup_txt_record()

def test_wait_for_public_recursive_txt_propagation_ignores_failed_resolvers(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 0]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_public_recursive_txt_values",
        lambda *_args, **_kwargs: (
            {"1.1.1.1": set(), "8.8.8.8": {"expected-value"}},
            {"1.1.1.1"},
        ),
    )

    MODULE._wait_for_public_recursive_txt_propagation(
        challenge_domain="_acme-challenge.example.com",
        expected_value="expected-value",
        timeout_seconds=1,
    )
