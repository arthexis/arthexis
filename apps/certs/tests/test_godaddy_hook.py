from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HOOK_PATH = Path(__file__).resolve().parents[3] / "scripts" / "certbot" / "godaddy_hook.py"
SPEC = importlib.util.spec_from_file_location("godaddy_hook", HOOK_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_zone_and_name_validates_zone_override_suffix():
    with pytest.raises(RuntimeError, match="GODADDY_ZONE"):
        MODULE._zone_and_name("_acme-challenge.example.com", "other.com")


def test_emit_log_writes_to_configured_log_file(tmp_path, capsys, monkeypatch):
    log_path = tmp_path / "hook.log"
    monkeypatch.setattr(MODULE, "HOOK_LOG_PATH", str(log_path))

    MODULE._emit_log("diagnostic-message")

    captured = capsys.readouterr()
    assert "diagnostic-message" in captured.out
    assert "diagnostic-message" in log_path.read_text(encoding="utf-8")


def test_zone_and_name_derives_zone_without_override(capsys):
    zone, host = MODULE._zone_and_name("_acme-challenge.example.com")

    captured = capsys.readouterr()
    assert zone == "example.com"
    assert host == "_acme-challenge"
    assert "derived zone 'example.com'" in captured.out


def test_wait_for_dns_txt_propagation_raises_timeout(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 1, 2, 2]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_authoritative_txt_values",
        lambda *_args, **_kwargs: {"stale-value"},
    )

    with pytest.raises(RuntimeError, match="DNS propagation timeout"):
        MODULE._wait_for_dns_txt_propagation(
            zone="example.com",
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=1,
        )


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
    monkeypatch.setattr(MODULE, "_wait_for_dns_txt_propagation", lambda **_kwargs: None)

    MODULE._upsert_txt_record()

    assert calls == [
        (
            "PUT",
            "/v1/domains/example.com/records/TXT/_acme-challenge",
            [{"data": "new-value", "ttl": 600}],
        )
    ]
